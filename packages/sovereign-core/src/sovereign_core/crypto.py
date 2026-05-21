import base64
import json
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, Any
from pydantic import BaseModel, Field

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization


class ForensicReceipt(TypedDict):
    """
    A typed representation of a secure provenance log entry.
    Ensures structural integrity when passed through local-first runtime layers.
    """
    timestamp: str  # ISO 8601 UTC timestamp
    payload_hash: str  # Deterministic hex-encoded SHA-256 data digest
    public_key: str  # Base64-encoded Ed25519 public key
    signature: str  # Base64-encoded cryptographic signature
    metadata: dict[str, Any]


class ReceiptSchema(BaseModel):
    """Pydantic validation layer for forensic runtime checks."""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload_hash: str
    public_key: str
    signature: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SovereignKeyManager:
    """Manages local-first Ed25519 lifecycle operations and cryptographic verification."""

    def __init__(self, key_dir: str | Path = ".keys"):
        self.key_dir = Path(key_dir)
        self.private_key_path = self.key_dir / "sovereign_identity.pem"
        self.public_key_path = self.key_dir / "sovereign_identity.pub"

        self._private_key: ed25519.Ed25519PrivateKey | None = None
        self._public_key: ed25519.Ed25519PublicKey | None = None

    def load_or_generate_keypair(self) -> tuple[str, str]:
        """Derives or loads an Ed25519 identity keypair."""
        if self.private_key_path.exists():
            with open(self.private_key_path, "rb") as f:
                self._private_key = serialization.load_pem_private_key(f.read(), password=None)
            self._public_key = self._private_key.public_key()
        else:
            self._private_key = ed25519.Ed25519PrivateKey.generate()
            self._public_key = self._private_key.public_key()

            self.key_dir.mkdir(parents=True, exist_ok=True)
            with open(self.private_key_path, "wb") as f:
                f.write(
                    self._private_key.private_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PrivateFormat.PKCS8,
                        encryption_algorithm=serialization.NoEncryption()
                    )
                )
            os.chmod(self.private_key_path, 0o600)

            with open(self.public_key_path, "wb") as f:
                f.write(
                    self._public_key.public_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PublicFormat.SubjectPublicKeyInfo
                    )
                )

        return self.get_base64_private_key(), self.get_base64_public_key()

    def get_base64_private_key(self) -> str:
        if not self._private_key:
            raise RuntimeError("Keypair not loaded.")
        raw_bytes = self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption()
        )
        return base64.b64encode(raw_bytes).decode("utf-8")

    def get_base64_public_key(self) -> str:
        if not self._public_key:
            raise RuntimeError("Keypair not loaded.")
        raw_bytes = self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        return base64.b64encode(raw_bytes).decode("utf-8")

    def generate_receipt(self, payload: dict[str, Any], metadata: dict[str, Any] | None = None) -> ForensicReceipt:
        """
        Signs a structured data payload and constructs an immutable ForensicReceipt payload dictionary.
        Uses process-stable SHA-256 for the data payload_hash.
        """
        if not self._private_key:
            self.load_or_generate_keypair()

        # 1. Deterministic string representation for hash normalization
        serialized_payload = json.dumps(payload, sort_keys=True, default=str)

        # FIXED: Replaced str(hash()) with stable hashlib.sha256 hex digest
        payload_hash = hashlib.sha256(serialized_payload.encode("utf-8")).hexdigest()

        # 2. Generate raw signature over payload
        raw_signature = self._private_key.sign(serialized_payload.encode("utf-8"))

        # 3. Construct and validate receipt structure
        receipt_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload_hash": payload_hash,
            "public_key": self.get_base64_public_key(),
            "signature": base64.b64encode(raw_signature).decode("utf-8"),
            "metadata": metadata or {}
        }

        validated = ReceiptSchema(**receipt_data)

        return ForensicReceipt(
            timestamp=validated.timestamp.isoformat(),
            payload_hash=validated.payload_hash,
            public_key=validated.public_key,
            signature=validated.signature,
            metadata=validated.metadata
        )

    @staticmethod
    def verify_receipt(receipt: ForensicReceipt, original_payload: dict[str, Any]) -> bool:
        """Stateless audit verification of a ForensicReceipt against the original payload dictionary."""
        try:
            public_bytes = base64.b64decode(receipt["public_key"])
            signature_bytes = base64.b64decode(receipt["signature"])

            public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_bytes)
            serialized_payload = json.dumps(original_payload, sort_keys=True, default=str)

            public_key.verify(signature_bytes, serialized_payload.encode("utf-8"))
            return True
        except Exception:
            return False