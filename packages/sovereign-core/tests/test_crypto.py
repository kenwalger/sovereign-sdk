"""Tests for SovereignKeyManager and ForensicReceipt cryptographic primitives."""

import base64
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from sovereign_core.crypto import ForensicReceipt, SovereignKeyManager


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_key_dir(tmp_path: Path) -> Path:
    """Provide an isolated temporary directory for keypair storage."""
    return tmp_path / "keys"


@pytest.fixture
def env_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    """Inject a deterministic SOVEREIGN_NODE_SECRET into the process environment."""
    secret = "sovereign-pytest-secret-2026"
    monkeypatch.setenv("SOVEREIGN_NODE_SECRET", secret)
    return secret


# ---------------------------------------------------------------------------
# Greenfield keypair generation
# ---------------------------------------------------------------------------


class TestGreenfieldKeypairGeneration:
    """Verify that greenfield Ed25519 keypair generation produces valid key material."""

    def test_returns_two_base64_strings(self, tmp_key_dir: Path, env_secret: str) -> None:
        """load_or_generate_keypair returns a 2-tuple of non-empty base64 strings.

        Both the private and public key components must be non-empty strings
        suitable for base64 decoding — a blank or None value signals that the
        key material was never populated.
        """
        manager = SovereignKeyManager(key_dir=tmp_key_dir)
        priv_b64, pub_b64 = manager.load_or_generate_keypair()

        assert isinstance(priv_b64, str), "Private key export must be a string"
        assert isinstance(pub_b64, str), "Public key export must be a string"
        assert len(priv_b64) > 0, "Private key base64 string must not be empty"
        assert len(pub_b64) > 0, "Public key base64 string must not be empty"

    def test_private_key_decodes_to_32_bytes(self, tmp_key_dir: Path, env_secret: str) -> None:
        """Ed25519 private key seed is exactly 32 bytes when decoded from base64.

        The raw Ed25519 seed (a.k.a. private scalar) is always 32 bytes.
        Any other length indicates a serialization format mismatch or
        partial export.
        """
        manager = SovereignKeyManager(key_dir=tmp_key_dir)
        priv_b64, _ = manager.load_or_generate_keypair()
        raw = base64.b64decode(priv_b64)
        assert len(raw) == 32, (
            f"Ed25519 private seed must be exactly 32 bytes; got {len(raw)}"
        )

    def test_public_key_decodes_to_32_bytes(self, tmp_key_dir: Path, env_secret: str) -> None:
        """Ed25519 public key is exactly 32 bytes when decoded from base64.

        The Bernstein curve point representation for Ed25519 is always 32 bytes.
        A different size indicates wrong encoding or key type.
        """
        manager = SovereignKeyManager(key_dir=tmp_key_dir)
        _, pub_b64 = manager.load_or_generate_keypair()
        raw = base64.b64decode(pub_b64)
        assert len(raw) == 32, (
            f"Ed25519 public key must be exactly 32 bytes; got {len(raw)}"
        )

    def test_both_pem_files_written_to_disk(self, tmp_key_dir: Path, env_secret: str) -> None:
        """load_or_generate_keypair persists both identity files to the configured key directory.

        The private key PEM and the public key PEM must both exist on disk
        after a successful greenfield generation so that subsequent boots
        can load the existing identity rather than generating a new keypair.
        """
        manager = SovereignKeyManager(key_dir=tmp_key_dir)
        manager.load_or_generate_keypair()

        assert manager.private_key_path.exists(), "Private key PEM must be written to disk"
        assert manager.public_key_path.exists(), "Public key PEM must be written to disk"

    def test_public_key_property_matches_export(self, tmp_key_dir: Path, env_secret: str) -> None:
        """The public_key property returns the same value as get_base64_public_key.

        The property is a convenience accessor; callers that use it for key
        pinning must receive an identical string to the method-based export.
        """
        manager = SovereignKeyManager(key_dir=tmp_key_dir)
        _, pub_from_return = manager.load_or_generate_keypair()

        assert manager.public_key == pub_from_return, (
            "public_key property must match the value returned by load_or_generate_keypair"
        )


# ---------------------------------------------------------------------------
# Descriptor-level os.fchmod permission locking
# ---------------------------------------------------------------------------


class TestFchmodPermissionLocking:
    """Verify descriptor-level os.fchmod permission locking on the private key file."""

    def test_private_key_file_is_non_empty(self, tmp_key_dir: Path, env_secret: str) -> None:
        """Private key file is non-empty after greenfield generation.

        A zero-byte file means flush or fsync did not complete before
        os.replace() promoted the temp file over the target path.
        """
        manager = SovereignKeyManager(key_dir=tmp_key_dir)
        manager.load_or_generate_keypair()

        size = manager.private_key_path.stat().st_size
        assert size > 0, (
            "Private key PEM must be non-empty — flush/fsync must complete before os.replace()"
        )

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="os.fchmod is unavailable on Windows; fallback os.chmod path is taken instead",
    )
    def test_private_key_has_0o600_permissions_posix(
        self, tmp_key_dir: Path, env_secret: str
    ) -> None:
        """On POSIX hosts, os.fchmod locks the private key file to 0o600 (owner r/w only).

        The descriptor-level fchmod call must execute before any bytes are
        written to the staging temp file.  A mode wider than 0o600 (e.g. 0o644)
        indicates that the hasattr(os, 'fchmod') branch was not taken or the
        fchmod call was reordered after the write.
        """
        manager = SovereignKeyManager(key_dir=tmp_key_dir)
        manager.load_or_generate_keypair()

        mode = os.stat(manager.private_key_path).st_mode & 0o777
        assert mode == 0o600, (
            f"Private key file must have 0o600 permissions on POSIX; got 0o{mode:03o}. "
            "The fchmod gate must be applied to the open descriptor before any bytes are written."
        )

    def test_fchmod_platform_guard_routes_correctly(self) -> None:
        """The hasattr(os, 'fchmod') guard resolves to the correct branch per platform.

        On Windows, os.fchmod must be absent so the portable os.chmod fallback
        is taken.  On POSIX, os.fchmod must be present so the descriptor-level
        path is taken, closing the TOCTOU race window.
        """
        if sys.platform == "win32":
            assert not hasattr(os, "fchmod"), (
                "Windows must NOT expose os.fchmod; the fallback os.chmod path must be taken"
            )
        else:
            assert hasattr(os, "fchmod"), (
                "POSIX hosts must expose os.fchmod for descriptor-level permission enforcement"
            )

    def test_atomic_replace_leaves_no_temp_file(self, tmp_key_dir: Path, env_secret: str) -> None:
        """After successful key generation, no staging temp file is left behind.

        The os.replace() atomic promotion must remove the temp file from disk.
        Any leftover .tmp artifact means the finally-block cleanup ran instead
        of the happy path, indicating a write failure that went undetected.
        """
        manager = SovereignKeyManager(key_dir=tmp_key_dir)
        manager.load_or_generate_keypair()

        orphaned_temps = list(tmp_key_dir.glob("tmp*"))
        assert len(orphaned_temps) == 0, (
            f"No staging temp files should remain after atomic os.replace(); "
            f"found: {orphaned_temps}"
        )


# ---------------------------------------------------------------------------
# Wrong SOVEREIGN_NODE_SECRET raises a cryptographic exception
# ---------------------------------------------------------------------------


class TestWrongSecretKeyLoad:
    """Verify that an incorrect SOVEREIGN_NODE_SECRET raises an exception on key load."""

    def test_wrong_secret_raises_runtime_error(
        self, tmp_key_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Loading a key encrypted with secret A using secret B raises RuntimeError.

        The cryptography library raises ValueError on a bad decrypt, which the
        key manager catches and re-raises as RuntimeError with rotation guidance.
        """
        monkeypatch.setenv("SOVEREIGN_NODE_SECRET", "correct-secret-alpha")
        writer = SovereignKeyManager(key_dir=tmp_key_dir)
        writer.load_or_generate_keypair()

        monkeypatch.setenv("SOVEREIGN_NODE_SECRET", "wrong-secret-beta")
        reader = SovereignKeyManager(key_dir=tmp_key_dir)

        with pytest.raises(RuntimeError):
            reader.load_or_generate_keypair()

    def test_missing_secret_raises_runtime_error(
        self, tmp_key_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Calling load_or_generate_keypair without SOVEREIGN_NODE_SECRET set raises RuntimeError.

        The node must refuse to initialise when the passphrase variable is absent
        rather than silently falling back to unencrypted key storage.
        """
        monkeypatch.delenv("SOVEREIGN_NODE_SECRET", raising=False)
        manager = SovereignKeyManager(key_dir=tmp_key_dir)

        with pytest.raises(RuntimeError, match="SOVEREIGN_NODE_SECRET"):
            manager.load_or_generate_keypair()

    def test_blank_secret_raises_runtime_error(
        self, tmp_key_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A SOVEREIGN_NODE_SECRET containing only whitespace is rejected as invalid.

        The .strip() guard in _resolve_node_secret must treat a whitespace-only
        value as absent, preventing accidental blank-passphrase key encryption.
        """
        monkeypatch.setenv("SOVEREIGN_NODE_SECRET", "   ")
        manager = SovereignKeyManager(key_dir=tmp_key_dir)

        with pytest.raises(RuntimeError, match="SOVEREIGN_NODE_SECRET"):
            manager.load_or_generate_keypair()


# ---------------------------------------------------------------------------
# ForensicReceipt minting and verification
# ---------------------------------------------------------------------------


class TestForensicReceiptMinting:
    """Verify the ForensicReceipt minting engine produces valid, verifiable signatures."""

    def test_receipt_has_all_required_fields(self, tmp_key_dir: Path, env_secret: str) -> None:
        """A freshly minted ForensicReceipt contains all five required envelope fields.

        Missing any field would leave the downstream verify_receipt call unable
        to reconstruct the canonical manifest and the signature check would fail
        or raise KeyError.
        """
        manager = SovereignKeyManager(key_dir=tmp_key_dir)
        manager.load_or_generate_keypair()
        payload: dict[str, Any] = {"tool": "test_runner", "records": 42}

        receipt: ForensicReceipt = manager.generate_receipt(payload)

        for field in ("timestamp", "payload_hash", "public_key", "signature", "metadata"):
            assert field in receipt, f"ForensicReceipt is missing required field: {field!r}"

    def test_receipt_verifies_against_original_payload(
        self, tmp_key_dir: Path, env_secret: str
    ) -> None:
        """verify_receipt returns True for a receipt minted against its original payload.

        Full three-step verification (key-pin assertion, payload-hash assertion,
        Ed25519 signature check) must all pass for a freshly minted, untampered
        receipt.
        """
        manager = SovereignKeyManager(key_dir=tmp_key_dir)
        manager.load_or_generate_keypair()
        payload: dict[str, Any] = {"op": "data_ingestion", "batch_id": "batch-007"}
        receipt = manager.generate_receipt(payload, metadata={"execution_success": True})

        valid = SovereignKeyManager.verify_receipt(
            receipt, payload, expected_public_key=manager.public_key
        )
        assert valid, (
            "A freshly minted, untampered receipt must pass full cryptographic verification"
        )

    def test_receipt_signature_is_64_bytes(self, tmp_key_dir: Path, env_secret: str) -> None:
        """The receipt signature field decodes to exactly 64 bytes (Ed25519 output size).

        An Ed25519 signature is always exactly 512 bits (64 bytes).  Any other
        size indicates a wrong algorithm was used or the bytes were truncated.
        """
        manager = SovereignKeyManager(key_dir=tmp_key_dir)
        manager.load_or_generate_keypair()
        receipt = manager.generate_receipt({"ping": "pong"})

        sig_bytes = base64.b64decode(receipt["signature"])
        assert len(sig_bytes) == 64, (
            f"Ed25519 signature must be exactly 64 bytes; got {len(sig_bytes)}"
        )

    def test_tampered_metadata_fails_verification(
        self, tmp_key_dir: Path, env_secret: str
    ) -> None:
        """Mutating receipt metadata after minting causes verify_receipt to return False.

        The Ed25519 signature covers the full canonical manifest including metadata;
        flipping execution_success after issuance is a detectable tamper attempt.
        """
        manager = SovereignKeyManager(key_dir=tmp_key_dir)
        manager.load_or_generate_keypair()
        payload: dict[str, Any] = {"step": "audit_gate"}
        receipt = manager.generate_receipt(payload, metadata={"execution_success": False})

        tampered: ForensicReceipt = {**receipt, "metadata": {"execution_success": True}}

        assert not SovereignKeyManager.verify_receipt(
            tampered, payload, expected_public_key=manager.public_key
        ), "Post-issuance metadata mutation must invalidate the Ed25519 signature"

    def test_wrong_public_key_pin_fails_verification(
        self, tmp_key_dir: Path, env_secret: str
    ) -> None:
        """A mismatched expected_public_key causes verify_receipt to return False before any crypto.

        The key-pin check guards against identity self-attestation forgery: a
        receipt minted by a rogue keypair would verify against its own embedded
        public key unless the caller pins the expected identity explicitly.
        """
        manager = SovereignKeyManager(key_dir=tmp_key_dir)
        manager.load_or_generate_keypair()
        payload: dict[str, Any] = {"sentinel": "canary"}
        receipt = manager.generate_receipt(payload)

        rogue_key = base64.b64encode(b"\x00" * 32).decode()
        assert not SovereignKeyManager.verify_receipt(
            receipt, payload, expected_public_key=rogue_key
        ), "A mismatched key pin must reject the receipt without attempting Ed25519 crypto"

    def test_altered_payload_fails_verification(
        self, tmp_key_dir: Path, env_secret: str
    ) -> None:
        """Verifying a receipt against a different payload than the one signed returns False.

        The payload-hash assertion (Step 2) re-derives the SHA-256 digest from
        original_payload and compares it byte-for-byte against receipt['payload_hash'].
        A different payload produces a different digest and fails before the
        signature check is attempted.
        """
        manager = SovereignKeyManager(key_dir=tmp_key_dir)
        manager.load_or_generate_keypair()
        original_payload: dict[str, Any] = {"value": "authentic"}
        receipt = manager.generate_receipt(original_payload)

        different_payload: dict[str, Any] = {"value": "counterfeit"}
        assert not SovereignKeyManager.verify_receipt(
            receipt, different_payload, expected_public_key=manager.public_key
        ), "Receipt verification against an altered payload must return False"

    def test_receipt_with_metadata_roundtrips(self, tmp_key_dir: Path, env_secret: str) -> None:
        """Arbitrary metadata is sealed inside the envelope and survives a full verify round-trip.

        The metadata dict is part of the canonical manifest; it must arrive at
        verify_receipt exactly as it was passed to generate_receipt.
        """
        manager = SovereignKeyManager(key_dir=tmp_key_dir)
        manager.load_or_generate_keypair()
        payload: dict[str, Any] = {"result": [1, 2, 3]}
        meta: dict[str, Any] = {"execution_success": True, "runtime": "sovereign-v0.7.0"}
        receipt = manager.generate_receipt(payload, metadata=meta)

        assert receipt["metadata"] == meta, (
            "Metadata must be preserved verbatim inside the ForensicReceipt envelope"
        )
        assert SovereignKeyManager.verify_receipt(
            receipt, payload, expected_public_key=manager.public_key
        ), "Receipt with explicit metadata must pass full verification"
