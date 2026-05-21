import base64
import json
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict
from pydantic import BaseModel, Field

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization


class ForensicReceipt(TypedDict):
    """Immutable, cryptographically sealed provenance record for a single tool execution.

    Every field in this envelope is covered by the Ed25519 signature stored in
    ``signature``.  Mutating any value — including those inside ``metadata`` —
    causes :meth:`SovereignKeyManager.verify_receipt` to return ``False``.

    Attributes:
        timestamp: ISO 8601 UTC timestamp captured at receipt-minting time.
        payload_hash: Hex-encoded SHA-256 digest of the deterministically
            serialised execution payload.
        public_key: Base64-encoded raw Ed25519 public key bytes used to
            produce and verify ``signature``.
        signature: Base64-encoded raw Ed25519 signature over the canonical
            manifest (``timestamp`` + ``payload_hash`` + ``metadata``).
        metadata: Arbitrary key/value execution annotations sealed inside the
            signature, e.g. ``execution_success``, ``runtime``, ``py_ver``.
    """

    timestamp: str
    payload_hash: str
    public_key: str
    signature: str
    metadata: dict[str, Any]


class ReceiptSchema(BaseModel):
    """Pydantic validation layer applied to every freshly minted ForensicReceipt.

    Attributes:
        timestamp: UTC datetime of receipt creation.  Defaults to the current
            instant when not supplied explicitly.
        payload_hash: Hex-encoded SHA-256 digest of the signed payload.
        public_key: Base64-encoded raw Ed25519 public key bytes.
        signature: Base64-encoded raw Ed25519 signature bytes.
        metadata: Arbitrary execution annotation dictionary.
    """

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload_hash: str
    public_key: str
    signature: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SovereignKeyManager:
    """Manages the local-first Ed25519 identity lifecycle and cryptographic receipt operations.

    All private key material is stored on disk encrypted with the passphrase
    sourced from the ``SOVEREIGN_NODE_SECRET`` environment variable.  The
    corresponding public key is written as plain PEM for audit and rotation
    purposes.

    Args:
        key_dir: Directory path for on-disk keypair persistence.  Defaults to
            ``.keys`` relative to the current working directory.
    """

    def __init__(self, key_dir: str | Path = ".keys") -> None:
        """Initialises path references for the identity keypair.  No I/O is performed.

        Args:
            key_dir: Directory where ``sovereign_identity.pem`` and
                ``sovereign_identity.pub`` will be written or read.
        """
        self.key_dir = Path(key_dir)
        self.private_key_path = self.key_dir / "sovereign_identity.pem"
        self.public_key_path = self.key_dir / "sovereign_identity.pub"

        self._private_key: ed25519.Ed25519PrivateKey | None = None
        self._public_key: ed25519.Ed25519PublicKey | None = None

    def _resolve_node_secret(self) -> bytes:
        """Reads and validates the PEM encryption passphrase from the environment.

        Returns:
            UTF-8 encoded bytes of the ``SOVEREIGN_NODE_SECRET`` value.

        Raises:
            RuntimeError: If ``SOVEREIGN_NODE_SECRET`` is absent or blank.
        """
        secret = os.getenv("SOVEREIGN_NODE_SECRET", "").strip()
        if not secret:
            raise RuntimeError(
                "SOVEREIGN_NODE_SECRET is not set. "
                "An explicit cryptographic passcode wrapper must be declared before "
                "initializing the sovereign identity keypair."
            )
        return secret.encode("utf-8")

    def load_or_generate_keypair(self) -> tuple[str, str]:
        """Loads an existing encrypted Ed25519 keypair or generates and persists a new one.

        The private key PEM file is encrypted with
        :func:`~cryptography.hazmat.primitives.serialization.BestAvailableEncryption`
        keyed to the passphrase returned by :meth:`_resolve_node_secret`.
        File permissions are tightened to ``0o600`` on creation.

        Returns:
            A 2-tuple of ``(base64_private_key, base64_public_key)`` where each
            element is the raw-bytes representation of the respective key
            encoded as a base64 string.

        Raises:
            RuntimeError: If ``SOVEREIGN_NODE_SECRET`` is not set.
            ValueError: If the on-disk PEM cannot be decrypted with the current
                passphrase (e.g. the key was encrypted with a different secret).
        """
        passphrase = self._resolve_node_secret()

        if self.private_key_path.exists():
            with open(self.private_key_path, "rb") as f:
                self._private_key = serialization.load_pem_private_key(f.read(), password=passphrase)
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
                        encryption_algorithm=serialization.BestAvailableEncryption(passphrase)
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
        """Exports the in-memory private key as a base64-encoded raw byte string.

        Returns:
            Base64-encoded string of the 32-byte Ed25519 private key seed.
            This export is unencrypted and lives only in memory; it is never
            written to disk by this method.

        Raises:
            RuntimeError: If the keypair has not been loaded via
                :meth:`load_or_generate_keypair`.
        """
        if not self._private_key:
            raise RuntimeError("Keypair not loaded.")
        raw_bytes = self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption()
        )
        return base64.b64encode(raw_bytes).decode("utf-8")

    def get_base64_public_key(self) -> str:
        """Exports the in-memory public key as a base64-encoded raw byte string.

        Returns:
            Base64-encoded string of the 32-byte Ed25519 public key.

        Raises:
            RuntimeError: If the keypair has not been loaded via
                :meth:`load_or_generate_keypair`.
        """
        if not self._public_key:
            raise RuntimeError("Keypair not loaded.")
        raw_bytes = self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        return base64.b64encode(raw_bytes).decode("utf-8")

    def generate_receipt(
        self,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> ForensicReceipt:
        """Mints a cryptographically sealed ForensicReceipt for the given payload.

        Assembles a canonical manifest that binds ``timestamp``, ``payload_hash``,
        and ``metadata`` into a single deterministic JSON string, then signs that
        string with the node's Ed25519 private key.  Because the entire manifest
        is signed, any post-issuance mutation of ``metadata`` — including flipping
        ``execution_success`` — is detectable by :meth:`verify_receipt`.

        Args:
            payload: Arbitrary dictionary representing the tool's execution result.
                Serialised with ``json.dumps(sort_keys=True, default=str)`` before
                hashing to guarantee deterministic output.
            metadata: Optional key/value annotations embedded in the sealed
                envelope, e.g. ``{"execution_success": True, "runtime": "…"}``.
                Defaults to an empty dictionary when ``None``.

        Returns:
            A :class:`ForensicReceipt` TypedDict whose ``signature`` covers the
            ``timestamp``, ``payload_hash``, and ``metadata`` fields atomically.

        Raises:
            RuntimeError: If the keypair is not loaded and
                ``SOVEREIGN_NODE_SECRET`` is unset.
        """
        if not self._private_key:
            self.load_or_generate_keypair()

        metadata = metadata or {}

        # 1. Stable SHA-256 digest of the payload
        serialized_payload = json.dumps(payload, sort_keys=True, default=str)
        payload_hash = hashlib.sha256(serialized_payload.encode("utf-8")).hexdigest()

        # 2. Canonical manifest that binds every security-critical field
        timestamp = datetime.now(timezone.utc).isoformat()
        manifest = {
            "metadata": metadata,
            "payload_hash": payload_hash,
            "timestamp": timestamp,
        }
        canonical = json.dumps(manifest, sort_keys=True, default=str)

        # 3. Sign the full manifest, not just the raw payload
        raw_signature = self._private_key.sign(canonical.encode("utf-8"))
        signature_b64 = base64.b64encode(raw_signature).decode("utf-8")

        # 4. Validate structure through Pydantic before returning
        validated = ReceiptSchema(
            timestamp=timestamp,
            payload_hash=payload_hash,
            public_key=self.get_base64_public_key(),
            signature=signature_b64,
            metadata=metadata,
        )

        # Return the exact timestamp string that was signed so verify_receipt can reconstruct it
        return ForensicReceipt(
            timestamp=timestamp,
            payload_hash=validated.payload_hash,
            public_key=validated.public_key,
            signature=validated.signature,
            metadata=validated.metadata,
        )

    @staticmethod
    def verify_receipt(receipt: ForensicReceipt, original_payload: dict[str, Any]) -> bool:
        """Verifies the cryptographic integrity of a ForensicReceipt against the original payload.

        Reconstructs the exact canonical manifest that was assembled at minting
        time — using the receipt's ``timestamp`` and ``metadata`` fields together
        with a freshly re-derived ``payload_hash`` — then verifies the receipt's
        Ed25519 ``signature`` against that manifest.

        Any post-issuance mutation of any envelope field (``metadata``,
        ``timestamp``, or ``payload_hash``) causes signature verification to raise
        ``InvalidSignature`` internally, and this method returns ``False``.

        Args:
            receipt: The :class:`ForensicReceipt` envelope to audit.
            original_payload: The exact payload dictionary passed to
                :meth:`generate_receipt`.  Its SHA-256 digest is re-derived
                internally; the caller must not pre-hash it.

        Returns:
            ``True`` if the receipt's signature is cryptographically valid for the
            given payload and the envelope is unmodified; ``False`` for any
            verification or decoding failure.
        """
        try:
            public_bytes = base64.b64decode(receipt["public_key"])
            signature_bytes = base64.b64decode(receipt["signature"])
            public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_bytes)

            # Re-derive payload_hash from the original payload using the same algorithm
            serialized_payload = json.dumps(original_payload, sort_keys=True, default=str)
            payload_hash = hashlib.sha256(serialized_payload.encode("utf-8")).hexdigest()

            # Reconstruct the exact canonical manifest that was signed
            manifest = {
                "metadata": receipt["metadata"],
                "payload_hash": payload_hash,
                "timestamp": receipt["timestamp"],
            }
            canonical = json.dumps(manifest, sort_keys=True, default=str)

            public_key.verify(signature_bytes, canonical.encode("utf-8"))
            return True
        except Exception:
            return False
