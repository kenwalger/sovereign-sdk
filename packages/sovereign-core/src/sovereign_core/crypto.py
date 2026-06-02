import base64
import json
import hashlib
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict
from pydantic import BaseModel, Field

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization


class SuccessionReceipt(TypedDict):
    """Cryptographic audit record produced when a node rotates its Ed25519 identity keypair.

    Every field is covered by ``succession_signature``, which is an Ed25519
    signature produced by the *previous* private key over the canonical JSON
    serialisation of ``previous_public_key``, ``new_public_key``, and
    ``rotation_timestamp``.  This gives any auditor holding the old public key
    the ability to verify that the rotation was authorised by the legitimate
    keyholder.

    Attributes:
        previous_public_key: Base64-encoded raw Ed25519 public key that was
            active before the rotation event.
        new_public_key: Base64-encoded raw Ed25519 public key that is active
            after the rotation event.
        rotation_timestamp: ISO 8601 UTC timestamp captured at the moment the
            rotation payload was signed.
        succession_signature: Base64-encoded raw Ed25519 signature produced by
            signing the canonical rotation payload with the *previous* private
            key.
    """

    previous_public_key: str
    new_public_key: str
    rotation_timestamp: str
    succession_signature: str


class ForensicReceipt(TypedDict):
    """Immutable, cryptographically sealed provenance record for a single tool execution.

    Every field in this envelope is covered by the Ed25519 signature stored in
    ``signature``.  Mutating any value — including those inside ``metadata`` —
    causes :meth:`SovereignKeyManager.verify_receipt` to return ``False``.

    Attributes:
        timestamp: ISO 8601 UTC timestamp captured at receipt-minting time.
        payload_hash: Hex-encoded SHA-256 digest of the deterministically
            serialised execution payload.  During verification this value is
            explicitly cross-checked against a fresh digest re-derived from the
            original payload; a mismatch causes
            :meth:`SovereignKeyManager.verify_receipt` to return ``False``
            before the signature check is even attempted.
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


class PublicKeyBundle(BaseModel):
    """Structured export of an Ed25519 public verification key with self-signed attestation.

    Carries sufficient information for a downstream consumer to independently
    verify both the authenticity of the key (via the ``attestation`` receipt)
    and the identity of the issuing node (via ``node_id``).  The bundle is
    serialisable to JSON via Pydantic v2 :meth:`model_dump_json`.

    Attributes:
        public_key: Base64-encoded raw 32-byte Ed25519 public key.
        attestation: A :class:`ForensicReceipt` dictionary that is
            self-signed by the node — i.e. the signing key matches
            ``public_key`` — proving that the bundle issuer controls the
            corresponding private key.
        issued_at: UTC ISO 8601 timestamp recorded at bundle creation time.
        node_id: Optional human-readable label for the issuing node.
            Defaults to ``None`` when not supplied.
    """

    public_key: str
    attestation: dict[str, Any]
    issued_at: str
    node_id: str | None = None


class SovereignKeyManager:
    """Manages the local-first Ed25519 identity lifecycle and cryptographic receipt operations.

    All private key material is stored on disk encrypted with the passphrase
    sourced from the ``SOVEREIGN_NODE_SECRET`` environment variable.  The
    corresponding public key is written as plain PEM for audit and rotation
    purposes.

    Both the legacy-migration and greenfield key-write paths enforce identical,
    umask-independent ``0o600`` file permissions via descriptor-level
    ``os.fchmod`` (with a path-based ``os.chmod`` fallback on platforms that
    lack ``fchmod``) applied to the open staging file descriptor before any
    bytes are written, and both promote the fully synced temp file over the
    target path via ``os.replace()`` for atomicity.

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

    @property
    def has_identity(self) -> bool:
        """Returns ``True`` when the Ed25519 keypair is fully initialised in memory.

        A clean, public alternative to inspecting the private ``_private_key`` or
        ``_public_key`` slots from outside the class.  Callers should check this
        property before calling :meth:`public_key` or :meth:`get_base64_public_key`
        to avoid the ``RuntimeError`` that those methods raise when the keypair is
        not yet loaded.

        Returns:
            ``True`` if both ``_private_key`` and ``_public_key`` are non-``None``,
            ``False`` otherwise.
        """
        return self._private_key is not None and self._public_key is not None

    def load_or_generate_keypair(self) -> tuple[str, str]:
        """Loads an existing Ed25519 keypair or generates and persists a new one.

        Loading follows a two-attempt upgrade path to handle legacy deployments:

        1. The PEM file is first loaded with the active ``SOVEREIGN_NODE_SECRET``
           passphrase via
           :func:`~cryptography.hazmat.primitives.serialization.BestAvailableEncryption`.
        2. If that raises :exc:`TypeError` or :exc:`ValueError` (indicating an
           unencrypted legacy key), a second attempt is made with
           ``password=None``.  On success, an advisory warning is printed to
           ``stderr`` and the key is immediately re-written to disk using the
           current passphrase, migrating the file to the encrypted format
           transparently.
        3. If both attempts fail, a :exc:`RuntimeError` is raised with explicit
           rotation guidance for the operator.

        Both the migration and greenfield paths enforce ``0o600`` permissions
        via a descriptor-level call — ``os.fchmod(fd, 0o600)`` on POSIX hosts,
        with a portable fallback to ``os.chmod(path, 0o600)`` on Windows — applied
        immediately after the staging temp file is created and before any bytes
        are written.  Operating on the file descriptor rather than the path closes
        the TOCTOU window present in path-based permission calls.  The fully synced
        temp file is promoted over the target path via ``os.replace()`` for
        atomicity on both paths.

        Returns:
            A 2-tuple of ``(base64_private_key, base64_public_key)`` where each
            element is the raw-bytes representation of the respective key
            encoded as a base64 string.

        Raises:
            RuntimeError: If ``SOVEREIGN_NODE_SECRET`` is not set, or if the
                on-disk PEM cannot be loaded by either attempt (corrupted file
                or wrong passphrase).
        """
        passphrase = self._resolve_node_secret()

        if self.private_key_path.exists():
            pem_data: bytes = self.private_key_path.read_bytes()

            try:
                self._private_key = serialization.load_pem_private_key(pem_data, password=passphrase)
            except (TypeError, ValueError):
                # First attempt failed — check for a legacy unencrypted key.
                try:
                    self._private_key = serialization.load_pem_private_key(pem_data, password=None)
                except Exception:
                    raise RuntimeError(
                        f"Cannot load private key at '{self.private_key_path}': the file is "
                        "either corrupted or was encrypted with a different "
                        "SOVEREIGN_NODE_SECRET value.  Rotate the key by removing the file "
                        "and restarting the node to generate a new encrypted identity."
                    )

                # Legacy unencrypted key loaded — warn and atomically re-encrypt in place.
                print(
                    "⚠️  Legacy unencrypted keypair detected. Automatically upgrading "
                    "identity configuration to encrypted storage format...",
                    file=sys.stderr,
                )
                encrypted_pem: bytes = self._private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.BestAvailableEncryption(passphrase),
                )
                tmp_path: str = ""
                try:
                    with tempfile.NamedTemporaryFile(
                        dir=os.path.dirname(self.private_key_path),
                        delete=False,
                    ) as tmp:
                        tmp_path = tmp.name
                        if hasattr(os, "fchmod"):
                            os.fchmod(tmp.fileno(), 0o600)
                        else:
                            os.chmod(tmp_path, 0o600)
                        tmp.write(encrypted_pem)
                        tmp.flush()
                        os.fsync(tmp.fileno())
                    os.replace(tmp_path, self.private_key_path)
                    tmp_path = ""  # Renamed successfully; nothing left to clean up.
                finally:
                    if tmp_path:
                        try:
                            os.remove(tmp_path)
                        except FileNotFoundError:
                            pass

            self._public_key = self._private_key.public_key()
        else:
            self._private_key = ed25519.Ed25519PrivateKey.generate()
            self._public_key = self._private_key.public_key()

            self.key_dir.mkdir(parents=True, exist_ok=True)
            pem_bytes = self._private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.BestAvailableEncryption(passphrase),
            )
            tmp_path: str = ""
            try:
                with tempfile.NamedTemporaryFile(
                    dir=os.path.dirname(self.private_key_path),
                    delete=False,
                ) as tmp_file:
                    tmp_path = tmp_file.name
                    if hasattr(os, "fchmod"):
                        os.fchmod(tmp_file.fileno(), 0o600)
                    else:
                        os.chmod(tmp_path, 0o600)
                    tmp_file.write(pem_bytes)
                    tmp_file.flush()
                    os.fsync(tmp_file.fileno())
                os.replace(tmp_path, self.private_key_path)
                tmp_path = ""
            except Exception:
                if tmp_path:
                    try:
                        os.remove(tmp_path)
                    except FileNotFoundError:
                        pass
                raise

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

    @property
    def public_key(self) -> str:
        """The node's pinned public key as a base64-encoded string.

        Convenience accessor that returns the same value as
        :meth:`get_base64_public_key`.  Intended to be passed directly as the
        ``expected_public_key`` argument to
        :meth:`verify_receipt` so that call sites can enforce key pinning
        without holding a separate reference to the raw key string.

        Returns:
            Base64-encoded string of the 32-byte Ed25519 public key.

        Raises:
            RuntimeError: If the keypair has not been loaded via
                :meth:`load_or_generate_keypair`.
        """
        return self.get_base64_public_key()

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

    def rotate_keypair(self) -> SuccessionReceipt:
        """Rotates the node's Ed25519 identity keypair and mints a cryptographic succession receipt.

        Generates a brand-new Ed25519 keypair, constructs a canonical rotation
        payload covering ``previous_public_key``, ``new_public_key``, and
        ``rotation_timestamp``, signs that payload with the *current* (outgoing)
        private key, then atomically promotes the new private key PEM over the
        on-disk file using the same ``os.replace``/``os.fchmod`` pattern as
        :meth:`load_or_generate_keypair`.

        The in-memory ``_private_key`` and ``_public_key`` slots are updated
        only after the disk promotion succeeds, so a write failure leaves the
        node in a consistent state with the old identity still active.

        Returns:
            A :class:`SuccessionReceipt` TypedDict whose ``succession_signature``
            is an Ed25519 signature produced by the *outgoing* private key over
            the canonical rotation payload, providing an auditable chain of
            identity handoff.

        Raises:
            RuntimeError: If ``SOVEREIGN_NODE_SECRET`` is not set, or if the
                keypair is not loaded and cannot be loaded from disk.
        """
        if not self._private_key:
            self.load_or_generate_keypair()

        passphrase = self._resolve_node_secret()
        old_private_key = self._private_key
        old_public_key_b64 = self.get_base64_public_key()

        new_private_key = ed25519.Ed25519PrivateKey.generate()
        new_public_key = new_private_key.public_key()
        new_public_key_b64 = base64.b64encode(
            new_public_key.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        ).decode("utf-8")

        rotation_timestamp = datetime.now(timezone.utc).isoformat()

        rotation_payload = json.dumps(
            {
                "new_public_key": new_public_key_b64,
                "previous_public_key": old_public_key_b64,
                "rotation_timestamp": rotation_timestamp,
            },
            sort_keys=True,
        )
        raw_sig = old_private_key.sign(rotation_payload.encode("utf-8"))
        succession_signature = base64.b64encode(raw_sig).decode("utf-8")

        new_pem = new_private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(passphrase),
        )

        tmp_path: str = ""
        try:
            with tempfile.NamedTemporaryFile(
                dir=os.path.dirname(self.private_key_path),
                delete=False,
            ) as tmp:
                tmp_path = tmp.name
                if hasattr(os, "fchmod"):
                    os.fchmod(tmp.fileno(), 0o600)
                else:
                    os.chmod(tmp_path, 0o600)
                tmp.write(new_pem)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, self.private_key_path)
            tmp_path = ""
        except Exception:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except FileNotFoundError:
                    pass
            raise

        with open(self.public_key_path, "wb") as f:
            f.write(
                new_public_key.public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            )

        self._private_key = new_private_key
        self._public_key = new_public_key

        return SuccessionReceipt(
            previous_public_key=old_public_key_b64,
            new_public_key=new_public_key_b64,
            rotation_timestamp=rotation_timestamp,
            succession_signature=succession_signature,
        )

    @staticmethod
    def verify_succession(receipt: SuccessionReceipt) -> bool:
        """Validates that a key rotation event is cryptographically genuine.

        Reconstructs the canonical rotation payload from the three non-signature
        fields of ``receipt`` and verifies the ``succession_signature`` against
        the ``previous_public_key`` embedded in the receipt.  Because the
        previous public key is encoded in the receipt itself, a standalone
        auditor with no access to any private key material or live node can
        call this method.

        Args:
            receipt: A :class:`SuccessionReceipt` produced by
                :meth:`rotate_keypair`.

        Returns:
            ``True`` if the ``succession_signature`` is a valid Ed25519
            signature over the canonical rotation payload produced by the
            private key corresponding to ``previous_public_key``.  ``False``
            for any decoding error, signature failure, or structural anomaly.
        """
        try:
            old_pub_bytes = base64.b64decode(receipt["previous_public_key"])
            sig_bytes = base64.b64decode(receipt["succession_signature"])
            old_public_key = ed25519.Ed25519PublicKey.from_public_bytes(old_pub_bytes)

            rotation_payload = json.dumps(
                {
                    "new_public_key": receipt["new_public_key"],
                    "previous_public_key": receipt["previous_public_key"],
                    "rotation_timestamp": receipt["rotation_timestamp"],
                },
                sort_keys=True,
            )
            old_public_key.verify(sig_bytes, rotation_payload.encode("utf-8"))
            return True
        except Exception:
            return False

    @staticmethod
    def verify_receipt(
        receipt: ForensicReceipt,
        original_payload: dict[str, Any],
        expected_public_key: str | None = None,
    ) -> bool:
        """Verifies the cryptographic integrity of a ForensicReceipt against the original payload.

        Verification proceeds in three sequential, independent steps:

        1. **Key-pin assertion** *(optional)*: When ``expected_public_key`` is
           supplied, the base64-encoded public key string extracted from
           ``receipt["public_key"]`` is compared directly against it.  A
           mismatch returns ``False`` immediately, before any cryptographic
           operation is attempted.  This prevents *identity self-attestation
           forgery*: without this gate, an attacker could mint a receipt with
           a rogue keypair that verifies correctly against its own public key
           rather than the node's pinned identity.  Pass
           :attr:`SovereignKeyManager.public_key` to enforce provenance.

        2. **Explicit payload-hash assertion**: The SHA-256 digest of
           ``original_payload`` is re-derived and compared byte-for-byte
           against ``receipt["payload_hash"]``.  A mismatch returns ``False``
           before any signature operation is attempted, closing the *phantom
           field* attack vector.

        3. **Signature verification**: The canonical manifest
           ``{"metadata": …, "payload_hash": …, "timestamp": …}`` is
           reconstructed using ``receipt["payload_hash"]`` (confirmed
           consistent with ``original_payload``) and verified against the
           Ed25519 ``signature`` embedded in the envelope.  Any mutation of
           ``metadata`` or ``timestamp`` is caught here.

        Args:
            receipt: The :class:`ForensicReceipt` envelope to audit.
            original_payload: The exact payload dictionary passed to
                :meth:`generate_receipt`.  Its SHA-256 digest is re-derived
                internally and compared against ``receipt["payload_hash"]``;
                the caller must not pre-hash it.
            expected_public_key: Optional base64-encoded Ed25519 public key
                string that the receipt's embedded key must match exactly.
                When provided, any receipt whose ``public_key`` field differs
                from this value is rejected before crypto operations begin.
                Pass :attr:`SovereignKeyManager.public_key` to pin receipts
                to the local node identity.  Defaults to ``None`` (no pin).

        Returns:
            ``True`` if and only if all active checks pass: the optional key
            pin matches, the re-derived payload hash equals
            ``receipt["payload_hash"]``, and the Ed25519 signature is valid
            for the reconstructed manifest.  ``False`` for any pin mismatch,
            hash mismatch, signature failure, or decoding error.
        """
        try:
            # Step 1 — key-pin assertion (identity provenance guard).
            # Fail fast on a string comparison before touching any crypto.
            if expected_public_key is not None and receipt["public_key"] != expected_public_key:
                return False

            public_bytes = base64.b64decode(receipt["public_key"])
            signature_bytes = base64.b64decode(receipt["signature"])
            public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_bytes)

            # Step 2 — explicit payload-hash assertion (phantom field guard).
            serialized_payload = json.dumps(original_payload, sort_keys=True, default=str)
            expected_payload_hash = hashlib.sha256(serialized_payload.encode("utf-8")).hexdigest()
            if expected_payload_hash != receipt["payload_hash"]:
                return False

            # Step 3 — reconstruct the canonical manifest and verify the signature.
            # receipt["payload_hash"] is now confirmed to match original_payload.
            manifest = {
                "metadata": receipt["metadata"],
                "payload_hash": receipt["payload_hash"],
                "timestamp": receipt["timestamp"],
            }
            canonical = json.dumps(manifest, sort_keys=True, default=str)

            public_key.verify(signature_bytes, canonical.encode("utf-8"))
            return True
        except Exception:
            return False
