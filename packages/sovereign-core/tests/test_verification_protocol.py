"""Tests for Phase 6.1 (Public Key Export & Distribution) and Phase 6.3 (Key Rotation & Succession)."""

import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from sovereign_core.crypto import (
    ForensicReceipt,
    PublicKeyBundle,
    SuccessionReceipt,
    SovereignKeyManager,
    SovereignStorageError,
)
from sovereign_core.gateway import SovereignGateway


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_key_dir(tmp_path: Path) -> Path:
    return tmp_path / "keys"


@pytest.fixture
def env_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    secret = "sovereign-pytest-phase6-secret"
    monkeypatch.setenv("SOVEREIGN_NODE_SECRET", secret)
    return secret


@pytest.fixture
def loaded_manager(tmp_key_dir: Path, env_secret: str) -> SovereignKeyManager:
    manager = SovereignKeyManager(key_dir=tmp_key_dir)
    manager.load_or_generate_keypair()
    return manager


@pytest.fixture
def gateway(tmp_path: Path, env_secret: str) -> SovereignGateway:
    key_path = str(tmp_path / "keys" / "sovereign_identity.pem")
    return SovereignGateway(signing_key=key_path)


# ---------------------------------------------------------------------------
# Phase 6.1 — PublicKeyBundle model
# ---------------------------------------------------------------------------


class TestPublicKeyBundleModel:
    """Verify that the PublicKeyBundle Pydantic model behaves correctly in isolation."""

    def test_model_instantiation_with_all_fields(self) -> None:
        """PublicKeyBundle accepts all four fields and surfaces them as attributes."""
        bundle = PublicKeyBundle(
            public_key="AAEC",
            attestation={"timestamp": "2026-01-01T00:00:00+00:00", "signature": "xyz"},
            issued_at="2026-01-01T00:00:00+00:00",
            node_id="node-test",
        )
        assert bundle.public_key == "AAEC"
        assert bundle.issued_at == "2026-01-01T00:00:00+00:00"
        assert bundle.node_id == "node-test"
        assert bundle.attestation["signature"] == "xyz"

    def test_model_node_id_defaults_to_none(self) -> None:
        """node_id defaults to None when omitted during instantiation."""
        bundle = PublicKeyBundle(
            public_key="AAEC",
            attestation={},
            issued_at="2026-01-01T00:00:00+00:00",
        )
        assert bundle.node_id is None

    def test_model_dump_json_produces_valid_json(self) -> None:
        """model_dump_json() produces a valid JSON string for Pydantic v2 serialization."""
        bundle = PublicKeyBundle(
            public_key="AAEC",
            attestation={"k": "v"},
            issued_at="2026-01-01T00:00:00+00:00",
            node_id="alpha",
        )
        raw = bundle.model_dump_json()
        parsed = json.loads(raw)
        assert parsed["public_key"] == "AAEC"
        assert parsed["node_id"] == "alpha"
        assert parsed["attestation"]["k"] == "v"

    def test_model_json_roundtrip_preserves_none_node_id(self) -> None:
        """A bundle with node_id=None round-trips through JSON with null preserved."""
        bundle = PublicKeyBundle(
            public_key="AAEC",
            attestation={},
            issued_at="2026-01-01T00:00:00+00:00",
        )
        parsed = json.loads(bundle.model_dump_json())
        assert parsed["node_id"] is None


# ---------------------------------------------------------------------------
# Phase 6.1 — export_public_key_bundle()
# ---------------------------------------------------------------------------


class TestPublicKeyBundleExport:
    """Validate the SovereignGateway.export_public_key_bundle() method."""

    def test_export_returns_public_key_bundle_instance(
        self, gateway: SovereignGateway
    ) -> None:
        """export_public_key_bundle() returns a PublicKeyBundle Pydantic model instance."""
        bundle = gateway.export_public_key_bundle()
        assert isinstance(bundle, PublicKeyBundle)

    def test_exported_public_key_decodes_to_32_bytes(
        self, gateway: SovereignGateway
    ) -> None:
        """The public_key field decodes from base64 to exactly 32 bytes (Ed25519 key size)."""
        bundle = gateway.export_public_key_bundle()
        raw = base64.b64decode(bundle.public_key)
        assert len(raw) == 32, (
            f"Ed25519 public key must decode to 32 bytes; got {len(raw)}"
        )

    def test_exported_attestation_has_forensic_receipt_fields(
        self, gateway: SovereignGateway
    ) -> None:
        """The attestation dict contains all five ForensicReceipt envelope fields."""
        bundle = gateway.export_public_key_bundle()
        for field in ("timestamp", "payload_hash", "public_key", "signature", "metadata"):
            assert field in bundle.attestation, (
                f"Attestation is missing ForensicReceipt field: {field!r}"
            )

    def test_attestation_public_key_matches_bundle_public_key(
        self, gateway: SovereignGateway
    ) -> None:
        """The attestation's embedded public_key matches the bundle's top-level public_key."""
        bundle = gateway.export_public_key_bundle()
        assert bundle.attestation["public_key"] == bundle.public_key, (
            "Attestation public_key must match the bundle's public_key field"
        )

    def test_attestation_verifies_against_key_attestation_payload(
        self, gateway: SovereignGateway
    ) -> None:
        """The self-signed attestation receipt passes full Ed25519 verification."""
        bundle = gateway.export_public_key_bundle()
        attestation_payload: dict[str, Any] = {
            "public_key": bundle.public_key,
            "type": "key_attestation",
        }
        valid = SovereignKeyManager.verify_receipt(
            bundle.attestation,  # type: ignore[arg-type]
            attestation_payload,
            expected_public_key=bundle.public_key,
        )
        assert valid, "Self-signed attestation receipt must pass full cryptographic verification"

    def test_exported_issued_at_is_non_empty_string(
        self, gateway: SovereignGateway
    ) -> None:
        """The issued_at field is a non-empty string (UTC ISO 8601 timestamp)."""
        bundle = gateway.export_public_key_bundle()
        assert isinstance(bundle.issued_at, str)
        assert len(bundle.issued_at) > 0
        assert "T" in bundle.issued_at, "issued_at must be an ISO 8601 datetime string"

    def test_exported_node_id_defaults_to_none(
        self, gateway: SovereignGateway
    ) -> None:
        """export_public_key_bundle() without node_id argument leaves node_id as None."""
        bundle = gateway.export_public_key_bundle()
        assert bundle.node_id is None

    def test_exported_node_id_is_set_when_provided(
        self, gateway: SovereignGateway
    ) -> None:
        """Passing node_id to export_public_key_bundle() surfaces it on the returned bundle."""
        bundle = gateway.export_public_key_bundle(node_id="node-canary")
        assert bundle.node_id == "node-canary"

    def test_bundle_public_key_matches_export_public_key(
        self, gateway: SovereignGateway
    ) -> None:
        """The bundle's public_key matches the value returned by export_public_key()."""
        bundle = gateway.export_public_key_bundle()
        assert bundle.public_key == gateway.export_public_key(), (
            "Bundle public_key must be identical to export_public_key() for the same identity"
        )

    def test_attestation_signature_is_64_bytes(
        self, gateway: SovereignGateway
    ) -> None:
        """The attestation's signature field decodes to 64 bytes (Ed25519 output size)."""
        bundle = gateway.export_public_key_bundle()
        sig_bytes = base64.b64decode(bundle.attestation["signature"])
        assert len(sig_bytes) == 64, (
            f"Attestation Ed25519 signature must be 64 bytes; got {len(sig_bytes)}"
        )

    def test_tampered_attestation_metadata_fails_verification(
        self, gateway: SovereignGateway
    ) -> None:
        """Mutating attestation metadata after export causes verify_receipt to return False."""
        bundle = gateway.export_public_key_bundle()
        tampered_attestation = {
            **bundle.attestation,
            "metadata": {"issued_at": "1970-01-01T00:00:00+00:00", "purpose": "INJECTED", "source": "PublicKeyBundle"},
        }
        attestation_payload: dict[str, Any] = {
            "public_key": bundle.public_key,
            "type": "key_attestation",
        }
        valid = SovereignKeyManager.verify_receipt(
            tampered_attestation,  # type: ignore[arg-type]
            attestation_payload,
            expected_public_key=bundle.public_key,
        )
        assert not valid, "Tampered attestation metadata must fail cryptographic verification"

    def test_attestation_with_wrong_key_pin_fails_verification(
        self, gateway: SovereignGateway
    ) -> None:
        """Verifying the attestation against a rogue expected_public_key returns False."""
        bundle = gateway.export_public_key_bundle()
        rogue_key = base64.b64encode(b"\xff" * 32).decode()
        attestation_payload: dict[str, Any] = {
            "public_key": bundle.public_key,
            "type": "key_attestation",
        }
        valid = SovereignKeyManager.verify_receipt(
            bundle.attestation,  # type: ignore[arg-type]
            attestation_payload,
            expected_public_key=rogue_key,
        )
        assert not valid, "Attestation with a mismatched key pin must fail verification"

    def test_bundle_issued_at_is_sealed_in_attestation_signature(
        self, gateway: SovereignGateway
    ) -> None:
        """Mutating issued_at inside the attestation metadata invalidates the Ed25519 signature.

        Fix 3 ensures issued_at is recorded in the signed metadata *before* the
        receipt is minted, so any downstream modification of that field is
        detectable via verify_receipt.
        """
        bundle = gateway.export_public_key_bundle()
        # issued_at must appear in the attestation metadata (not only as a top-level field)
        assert "issued_at" in bundle.attestation["metadata"], (
            "issued_at must be sealed inside the attestation metadata by the Ed25519 signature"
        )
        # Mutating issued_at inside the metadata must break the signature
        tampered = {
            **bundle.attestation,
            "metadata": {
                **bundle.attestation["metadata"],
                "issued_at": "1970-01-01T00:00:00+00:00",
            },
        }
        attestation_payload: dict[str, Any] = {
            "public_key": bundle.public_key,
            "type": "key_attestation",
        }
        valid = SovereignKeyManager.verify_receipt(
            tampered,  # type: ignore[arg-type]
            attestation_payload,
            expected_public_key=bundle.public_key,
        )
        assert not valid, (
            "Modifying issued_at inside the attestation metadata must invalidate the signature"
        )

    def test_bundle_issued_at_matches_attestation_metadata_issued_at(
        self, gateway: SovereignGateway
    ) -> None:
        """bundle.issued_at equals the issued_at value sealed inside the attestation metadata.

        Both values must be identical — the top-level field is a convenience
        accessor for the same timestamp that is cryptographically bound by the
        attestation signature.
        """
        bundle = gateway.export_public_key_bundle()
        assert bundle.issued_at == bundle.attestation["metadata"]["issued_at"], (
            "bundle.issued_at must equal attestation.metadata['issued_at']"
        )


# ---------------------------------------------------------------------------
# Phase 6.1 — save_public_key_bundle()
# ---------------------------------------------------------------------------


class TestPublicKeyBundleSave:
    """Validate the SovereignGateway.save_public_key_bundle() method."""

    def test_save_writes_a_file_at_the_given_path(
        self, gateway: SovereignGateway, tmp_path: Path
    ) -> None:
        """save_public_key_bundle() creates a file at the specified path."""
        bundle_path = tmp_path / "bundle.json"
        gateway.save_public_key_bundle(str(bundle_path))
        assert bundle_path.exists(), "save_public_key_bundle must write a file at the given path"

    def test_saved_file_is_valid_json(
        self, gateway: SovereignGateway, tmp_path: Path
    ) -> None:
        """The file written by save_public_key_bundle() contains valid JSON."""
        bundle_path = tmp_path / "bundle.json"
        gateway.save_public_key_bundle(str(bundle_path))
        content = bundle_path.read_text(encoding="utf-8")
        parsed = json.loads(content)
        assert isinstance(parsed, dict), "Bundle file must contain a JSON object"

    def test_saved_bundle_contains_required_fields(
        self, gateway: SovereignGateway, tmp_path: Path
    ) -> None:
        """The JSON file contains all four required PublicKeyBundle fields."""
        bundle_path = tmp_path / "bundle.json"
        gateway.save_public_key_bundle(str(bundle_path))
        parsed = json.loads(bundle_path.read_text(encoding="utf-8"))
        for field in ("public_key", "attestation", "issued_at", "node_id"):
            assert field in parsed, f"Saved bundle JSON is missing field: {field!r}"

    def test_saved_bundle_public_key_matches_export(
        self, gateway: SovereignGateway, tmp_path: Path
    ) -> None:
        """The public_key in the saved JSON matches the gateway's active identity."""
        expected_key = gateway.export_public_key()
        bundle_path = tmp_path / "bundle.json"
        gateway.save_public_key_bundle(str(bundle_path))
        parsed = json.loads(bundle_path.read_text(encoding="utf-8"))
        assert parsed["public_key"] == expected_key

    def test_save_with_node_id_persists_node_id(
        self, gateway: SovereignGateway, tmp_path: Path
    ) -> None:
        """node_id passed to save_public_key_bundle() appears in the persisted JSON."""
        bundle_path = tmp_path / "bundle.json"
        gateway.save_public_key_bundle(str(bundle_path), node_id="sentinel-node")
        parsed = json.loads(bundle_path.read_text(encoding="utf-8"))
        assert parsed["node_id"] == "sentinel-node"

    def test_save_to_missing_parent_raises_os_error(
        self, gateway: SovereignGateway, tmp_path: Path
    ) -> None:
        """Writing to a path whose parent directory does not exist raises OSError."""
        missing_parent = tmp_path / "does_not_exist" / "bundle.json"
        with pytest.raises(OSError):
            gateway.save_public_key_bundle(str(missing_parent))


# ---------------------------------------------------------------------------
# Phase 6.3 — rotate_keypair()
# ---------------------------------------------------------------------------


class TestKeyRotationBasic:
    """Verify the normal key rotation flow via SovereignKeyManager.rotate_keypair()."""

    def test_rotate_returns_succession_receipt_typeddict(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """rotate_keypair() returns a SuccessionReceipt TypedDict instance."""
        receipt = loaded_manager.rotate_keypair()
        assert isinstance(receipt, dict), "SuccessionReceipt must be a dict-like TypedDict"
        for field in ("previous_public_key", "new_public_key", "rotation_timestamp", "succession_signature"):
            assert field in receipt, f"SuccessionReceipt is missing field: {field!r}"

    def test_rotation_changes_the_active_public_key(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """After rotate_keypair(), the in-memory public key differs from the pre-rotation key."""
        pre_rotation_key = loaded_manager.public_key
        loaded_manager.rotate_keypair()
        post_rotation_key = loaded_manager.public_key
        assert pre_rotation_key != post_rotation_key, (
            "Rotation must replace the active public key with a freshly generated one"
        )

    def test_succession_receipt_previous_key_matches_pre_rotation_key(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """previous_public_key in the receipt equals the key active before rotation."""
        pre_rotation_key = loaded_manager.public_key
        receipt = loaded_manager.rotate_keypair()
        assert receipt["previous_public_key"] == pre_rotation_key, (
            "SuccessionReceipt.previous_public_key must record the exact pre-rotation key"
        )

    def test_succession_receipt_new_key_matches_post_rotation_key(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """new_public_key in the receipt equals the key active after rotation."""
        receipt = loaded_manager.rotate_keypair()
        post_rotation_key = loaded_manager.public_key
        assert receipt["new_public_key"] == post_rotation_key, (
            "SuccessionReceipt.new_public_key must match the post-rotation active key"
        )

    def test_succession_signature_decodes_to_64_bytes(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """The succession_signature field decodes from base64 to exactly 64 bytes."""
        receipt = loaded_manager.rotate_keypair()
        sig_bytes = base64.b64decode(receipt["succession_signature"])
        assert len(sig_bytes) == 64, (
            f"Ed25519 succession signature must be 64 bytes; got {len(sig_bytes)}"
        )

    def test_rotation_updates_pem_file_on_disk(
        self, loaded_manager: SovereignKeyManager, env_secret: str
    ) -> None:
        """The private key PEM on disk is replaced after a successful rotation."""
        pem_before = loaded_manager.private_key_path.read_bytes()
        loaded_manager.rotate_keypair()
        pem_after = loaded_manager.private_key_path.read_bytes()
        assert pem_before != pem_after, (
            "Private key PEM on disk must change after rotate_keypair()"
        )

    def test_rotated_private_key_can_generate_valid_receipt(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """After rotation, the manager can mint a ForensicReceipt that verifies correctly."""
        loaded_manager.rotate_keypair()
        payload: dict[str, Any] = {"step": "post_rotation_audit"}
        receipt = loaded_manager.generate_receipt(payload)
        valid = SovereignKeyManager.verify_receipt(
            receipt, payload, expected_public_key=loaded_manager.public_key
        )
        assert valid, "Post-rotation receipt must pass full cryptographic verification"

    def test_double_rotation_produces_valid_chain(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """Two sequential rotations each produce independently verifiable succession receipts."""
        key_before_first = loaded_manager.public_key
        receipt_1 = loaded_manager.rotate_keypair()
        key_before_second = loaded_manager.public_key
        receipt_2 = loaded_manager.rotate_keypair()

        assert SovereignKeyManager.verify_succession(receipt_1, key_before_first), (
            "First succession receipt must independently verify against its trusted anchor"
        )
        assert SovereignKeyManager.verify_succession(receipt_2, key_before_second), (
            "Second succession receipt must independently verify against its trusted anchor"
        )
        # Chain continuity: receipt_2.previous == receipt_1.new
        assert receipt_2["previous_public_key"] == receipt_1["new_public_key"], (
            "Second receipt's previous_public_key must equal the first receipt's new_public_key"
        )

    def test_rotation_leaves_no_orphaned_temp_files(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """No staging temp files are left behind after a successful rotation."""
        loaded_manager.rotate_keypair()
        orphaned = list(loaded_manager.key_dir.glob("tmp*"))
        assert len(orphaned) == 0, (
            f"No temp files should remain after atomic os.replace(); found: {orphaned}"
        )


# ---------------------------------------------------------------------------
# Phase 6.3 — verify_succession()
# ---------------------------------------------------------------------------


class TestSuccessionVerification:
    """Validate the SovereignKeyManager.verify_succession() static method."""

    def test_verify_succession_returns_true_for_valid_receipt(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """verify_succession() returns True for a receipt produced by rotate_keypair()."""
        pre_rotation_key = loaded_manager.public_key
        receipt = loaded_manager.rotate_keypair()
        assert SovereignKeyManager.verify_succession(receipt, pre_rotation_key) is True

    def test_verify_succession_true_after_double_rotation(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """Both receipts from two sequential rotations pass verify_succession independently."""
        key_before_first = loaded_manager.public_key
        receipt_1 = loaded_manager.rotate_keypair()
        key_before_second = loaded_manager.public_key
        receipt_2 = loaded_manager.rotate_keypair()
        assert SovereignKeyManager.verify_succession(receipt_1, key_before_first)
        assert SovereignKeyManager.verify_succession(receipt_2, key_before_second)

    def test_verify_succession_true_for_receipt_from_different_manager_instance(
        self, tmp_key_dir: Path, env_secret: str
    ) -> None:
        """A succession receipt verifies correctly when called from a fresh manager instance."""
        writer = SovereignKeyManager(key_dir=tmp_key_dir)
        writer.load_or_generate_keypair()
        pre_rotation_key = writer.public_key
        receipt = writer.rotate_keypair()

        assert SovereignKeyManager.verify_succession(receipt, pre_rotation_key), (
            "verify_succession must work without any live key material"
        )

    def test_verify_succession_anchor_check_rejects_wrong_trusted_key(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """Passing the wrong trusted_previous_public_key returns False before any crypto.

        This directly tests Fix 2: even a cryptographically valid receipt is
        rejected when the caller's trusted anchor does not match.
        """
        pre_rotation_key = loaded_manager.public_key
        receipt = loaded_manager.rotate_keypair()
        wrong_anchor = base64.b64encode(b"\x00" * 32).decode()
        assert SovereignKeyManager.verify_succession(receipt, wrong_anchor) is False, (
            "A mismatched trusted_previous_public_key must reject the receipt immediately"
        )
        # Confirm the same receipt passes when the correct anchor is supplied
        assert SovereignKeyManager.verify_succession(receipt, pre_rotation_key) is True


# ---------------------------------------------------------------------------
# Phase 6.3 — Adversarial and malformed cases
# ---------------------------------------------------------------------------


class TestSuccessionAdversarial:
    """Verify that verify_succession() rejects tampered and adversarially crafted inputs."""

    def test_tampered_new_public_key_fails_verification(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """Replacing new_public_key with a rogue key invalidates the succession signature."""
        pre_rotation_key = loaded_manager.public_key
        receipt = loaded_manager.rotate_keypair()
        rogue_key = base64.b64encode(b"\xab" * 32).decode()
        tampered = SuccessionReceipt(
            previous_public_key=receipt["previous_public_key"],
            new_public_key=rogue_key,
            rotation_timestamp=receipt["rotation_timestamp"],
            succession_signature=receipt["succession_signature"],
        )
        assert not SovereignKeyManager.verify_succession(tampered, pre_rotation_key), (
            "Tampered new_public_key must invalidate the succession signature"
        )

    def test_tampered_previous_public_key_fails_verification(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """Replacing previous_public_key with a rogue value fails the anchor check."""
        pre_rotation_key = loaded_manager.public_key
        receipt = loaded_manager.rotate_keypair()
        rogue_key = base64.b64encode(b"\xcd" * 32).decode()
        tampered = SuccessionReceipt(
            previous_public_key=rogue_key,
            new_public_key=receipt["new_public_key"],
            rotation_timestamp=receipt["rotation_timestamp"],
            succession_signature=receipt["succession_signature"],
        )
        assert not SovereignKeyManager.verify_succession(tampered, pre_rotation_key), (
            "Tampered previous_public_key must fail the anchor equality check"
        )

    def test_tampered_rotation_timestamp_fails_verification(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """Altering rotation_timestamp after signing causes verify_succession to return False."""
        pre_rotation_key = loaded_manager.public_key
        receipt = loaded_manager.rotate_keypair()
        tampered = SuccessionReceipt(
            previous_public_key=receipt["previous_public_key"],
            new_public_key=receipt["new_public_key"],
            rotation_timestamp="1970-01-01T00:00:00+00:00",
            succession_signature=receipt["succession_signature"],
        )
        assert not SovereignKeyManager.verify_succession(tampered, pre_rotation_key), (
            "Tampered rotation_timestamp must invalidate the succession signature"
        )

    def test_corrupted_signature_bytes_fails_verification(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """A bit-flipped succession_signature causes verify_succession to return False."""
        pre_rotation_key = loaded_manager.public_key
        receipt = loaded_manager.rotate_keypair()
        raw_sig = bytearray(base64.b64decode(receipt["succession_signature"]))
        raw_sig[0] ^= 0xFF  # flip the first byte
        bad_sig = base64.b64encode(bytes(raw_sig)).decode()
        tampered = SuccessionReceipt(
            previous_public_key=receipt["previous_public_key"],
            new_public_key=receipt["new_public_key"],
            rotation_timestamp=receipt["rotation_timestamp"],
            succession_signature=bad_sig,
        )
        assert not SovereignKeyManager.verify_succession(tampered, pre_rotation_key), (
            "A corrupted succession_signature must fail verification"
        )

    def test_malformed_base64_signature_returns_false_not_exception(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """Garbage base64 in succession_signature returns False without raising an exception."""
        pre_rotation_key = loaded_manager.public_key
        receipt = loaded_manager.rotate_keypair()
        tampered = SuccessionReceipt(
            previous_public_key=receipt["previous_public_key"],
            new_public_key=receipt["new_public_key"],
            rotation_timestamp=receipt["rotation_timestamp"],
            succession_signature="!!! NOT VALID BASE64 !!!",
        )
        result = SovereignKeyManager.verify_succession(tampered, pre_rotation_key)
        assert result is False, (
            "Malformed base64 in succession_signature must return False, not raise"
        )

    def test_rogue_keypair_cannot_forge_valid_succession_receipt(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """A receipt forged by a rogue keypair fails verify_succession against the real anchor."""
        from cryptography.hazmat.primitives.asymmetric import ed25519 as _ed
        from cryptography.hazmat.primitives import serialization as _ser

        loaded_manager.rotate_keypair()
        # post_rotation_key is now the "known" key before any further rotation
        post_rotation_key = loaded_manager.public_key

        # Attacker generates their own keypair and mints a fake succession receipt
        attacker_private = _ed.Ed25519PrivateKey.generate()
        attacker_public_b64 = base64.b64encode(
            attacker_private.public_key().public_bytes(
                encoding=_ser.Encoding.Raw,
                format=_ser.PublicFormat.Raw,
            )
        ).decode()

        from datetime import datetime, timezone as _tz
        fake_timestamp = datetime.now(_tz.utc).isoformat()
        fake_payload = json.dumps(
            {
                "new_public_key": attacker_public_b64,
                "previous_public_key": post_rotation_key,
                "rotation_timestamp": fake_timestamp,
            },
            sort_keys=True,
        )
        fake_sig = base64.b64encode(attacker_private.sign(fake_payload.encode())).decode()

        forged = SuccessionReceipt(
            previous_public_key=post_rotation_key,
            new_public_key=attacker_public_b64,
            rotation_timestamp=fake_timestamp,
            succession_signature=fake_sig,
        )
        # Anchor check passes (previous_public_key matches post_rotation_key),
        # but the crypto check must reject the rogue signature.
        assert not SovereignKeyManager.verify_succession(forged, post_rotation_key), (
            "A receipt signed by a rogue key (not the previous private key) must fail verification"
        )

    def test_empty_signature_string_returns_false(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """An empty succession_signature returns False without raising an exception."""
        pre_rotation_key = loaded_manager.public_key
        receipt = loaded_manager.rotate_keypair()
        tampered = SuccessionReceipt(
            previous_public_key=receipt["previous_public_key"],
            new_public_key=receipt["new_public_key"],
            rotation_timestamp=receipt["rotation_timestamp"],
            succession_signature="",
        )
        result = SovereignKeyManager.verify_succession(tampered, pre_rotation_key)
        assert result is False

    def test_swapped_keys_in_receipt_fails_verification(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """Swapping previous_public_key and new_public_key fields fails verify_succession.

        The anchor check catches the swap immediately: swapped["previous_public_key"]
        is the post-rotation key, which does not equal the trusted pre-rotation anchor.
        """
        pre_rotation_key = loaded_manager.public_key
        receipt = loaded_manager.rotate_keypair()
        swapped = SuccessionReceipt(
            previous_public_key=receipt["new_public_key"],
            new_public_key=receipt["previous_public_key"],
            rotation_timestamp=receipt["rotation_timestamp"],
            succession_signature=receipt["succession_signature"],
        )
        assert not SovereignKeyManager.verify_succession(swapped, pre_rotation_key), (
            "Swapping previous and new keys in the receipt must fail the anchor check"
        )


# ---------------------------------------------------------------------------
# Phase 6.3 — Transactional staging loop rollback regression tests
# ---------------------------------------------------------------------------


class TestKeyRotationAtomicStagingRollback:
    """Regression tests for the transactional staging loop in rotate_keypair().

    These tests simulate disk I/O failures during the staging phase and verify
    that the rollback guarantee holds: neither live key file nor in-memory state
    is mutated when either staging write raises.
    """

    def test_pub_staging_failure_leaves_pem_on_disk_unchanged(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """When the .pub staging write raises, the .pem on disk is byte-identical to before.

        Validates the core transactional invariant: neither live path is touched
        until both staging writes commit successfully.
        """
        original_pem = loaded_manager.private_key_path.read_bytes()

        real_ntf = tempfile.NamedTemporaryFile
        call_count = 0

        def fail_on_second_call(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("Simulated disk failure on .pub staging write")
            return real_ntf(**kwargs)

        with patch("sovereign_core.crypto.tempfile.NamedTemporaryFile", side_effect=fail_on_second_call):
            with pytest.raises(OSError, match="Simulated disk failure on .pub staging write"):
                loaded_manager.rotate_keypair()

        assert loaded_manager.private_key_path.read_bytes() == original_pem, (
            "Private key PEM on disk must be unchanged after a .pub staging failure"
        )

    def test_pub_staging_failure_leaves_pub_on_disk_unchanged(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """When the .pub staging write raises, the .pub file on disk is unchanged."""
        original_pub = loaded_manager.public_key_path.read_bytes() if loaded_manager.public_key_path.exists() else None

        real_ntf = tempfile.NamedTemporaryFile
        call_count = 0

        def fail_on_second_call(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("Simulated disk failure on .pub staging write")
            return real_ntf(**kwargs)

        with patch("sovereign_core.crypto.tempfile.NamedTemporaryFile", side_effect=fail_on_second_call):
            with pytest.raises(OSError):
                loaded_manager.rotate_keypair()

        if original_pub is not None:
            assert loaded_manager.public_key_path.read_bytes() == original_pub, (
                "Public key file on disk must be unchanged after a .pub staging failure"
            )

    def test_pem_staging_failure_leaves_both_files_unchanged(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """When the .pem staging write raises, both live key files remain byte-identical."""
        original_pem = loaded_manager.private_key_path.read_bytes()
        original_pub = loaded_manager.public_key_path.read_bytes() if loaded_manager.public_key_path.exists() else None

        def fail_immediately(**kwargs):
            raise OSError("Simulated disk failure on .pem staging write")

        with patch("sovereign_core.crypto.tempfile.NamedTemporaryFile", side_effect=fail_immediately):
            with pytest.raises(OSError, match="Simulated disk failure on .pem staging write"):
                loaded_manager.rotate_keypair()

        assert loaded_manager.private_key_path.read_bytes() == original_pem
        if original_pub is not None:
            assert loaded_manager.public_key_path.read_bytes() == original_pub

    def test_pub_staging_failure_leaves_no_orphaned_temp_files(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """After a .pub staging failure, the except handler removes all staged temps."""
        real_ntf = tempfile.NamedTemporaryFile
        call_count = 0

        def fail_on_second_call(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("Simulated disk failure on .pub staging write")
            return real_ntf(**kwargs)

        with patch("sovereign_core.crypto.tempfile.NamedTemporaryFile", side_effect=fail_on_second_call):
            with pytest.raises(OSError):
                loaded_manager.rotate_keypair()

        orphaned = list(loaded_manager.key_dir.glob("tmp*"))
        assert len(orphaned) == 0, (
            f"Except handler must remove all staged temp files after failure; found: {orphaned}"
        )

    def test_pub_staging_failure_leaves_in_memory_key_state_unchanged(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """In-memory _private_key and _public_key are not swapped when staging fails."""
        original_key = loaded_manager.public_key

        real_ntf = tempfile.NamedTemporaryFile
        call_count = 0

        def fail_on_second_call(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("Simulated disk failure on .pub staging write")
            return real_ntf(**kwargs)

        with patch("sovereign_core.crypto.tempfile.NamedTemporaryFile", side_effect=fail_on_second_call):
            with pytest.raises(OSError):
                loaded_manager.rotate_keypair()

        assert loaded_manager.public_key == original_key, (
            "In-memory public key must remain the pre-rotation key after a staging failure"
        )

    def test_manager_still_mints_valid_receipts_after_failed_rotation(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """After a staging failure, the manager can still mint and verify receipts with the old key."""
        original_key = loaded_manager.public_key

        real_ntf = tempfile.NamedTemporaryFile
        call_count = 0

        def fail_on_second_call(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("Simulated disk failure on .pub staging write")
            return real_ntf(**kwargs)

        with patch("sovereign_core.crypto.tempfile.NamedTemporaryFile", side_effect=fail_on_second_call):
            with pytest.raises(OSError):
                loaded_manager.rotate_keypair()

        payload: dict[str, Any] = {"recovery_check": "post_failure_receipt"}
        receipt = loaded_manager.generate_receipt(payload)
        valid = SovereignKeyManager.verify_receipt(receipt, payload, expected_public_key=original_key)
        assert valid, (
            "Manager must produce verifiable receipts with the original key after a failed rotation"
        )


# ---------------------------------------------------------------------------
# Phase 6.3 — Promotion phase rollback (second os.replace failure)
# ---------------------------------------------------------------------------


class TestPromotionPhaseRollback:
    """Regression tests for the hardened promotion phase in rotate_keypair().

    These tests simulate a failure specifically on the second os.replace call
    (the .pub promotion) — after the first os.replace (.pem) has already
    committed — and verify that the rollback logic restores the original
    private key, raises SovereignStorageError, and leaves no orphaned files.
    """

    def test_second_replace_failure_raises_sovereign_storage_error(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """A failure on the .pub os.replace raises SovereignStorageError, not the raw OSError."""
        real_replace = os.replace
        call_count = 0

        def fail_on_second_replace(src: str, dst) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("Simulated failure on .pub os.replace")
            return real_replace(src, dst)

        with patch("sovereign_core.crypto.os.replace", side_effect=fail_on_second_replace):
            with pytest.raises(SovereignStorageError, match="Key rotation failed"):
                loaded_manager.rotate_keypair()

    def test_second_replace_failure_restores_original_pem_on_disk(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """After .pub promotion failure, the original private key bytes are restored on disk."""
        original_pem = loaded_manager.private_key_path.read_bytes()
        real_replace = os.replace
        call_count = 0

        def fail_on_second_replace(src: str, dst) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("Simulated failure on .pub os.replace")
            return real_replace(src, dst)

        with patch("sovereign_core.crypto.os.replace", side_effect=fail_on_second_replace):
            with pytest.raises(SovereignStorageError):
                loaded_manager.rotate_keypair()

        assert loaded_manager.private_key_path.read_bytes() == original_pem, (
            "Rollback must restore the byte-identical original private key PEM to disk"
        )

    def test_second_replace_failure_leaves_in_memory_key_unchanged(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """In-memory key state is not updated when the promotion phase rolls back."""
        original_key = loaded_manager.public_key
        real_replace = os.replace
        call_count = 0

        def fail_on_second_replace(src: str, dst) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("Simulated failure on .pub os.replace")
            return real_replace(src, dst)

        with patch("sovereign_core.crypto.os.replace", side_effect=fail_on_second_replace):
            with pytest.raises(SovereignStorageError):
                loaded_manager.rotate_keypair()

        assert loaded_manager.public_key == original_key, (
            "In-memory key state must remain the pre-rotation key after rollback"
        )

    def test_second_replace_failure_leaves_no_orphaned_files(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """No staging or restore temp files remain after a rollback completes."""
        real_replace = os.replace
        call_count = 0

        def fail_on_second_replace(src: str, dst) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("Simulated failure on .pub os.replace")
            return real_replace(src, dst)

        with patch("sovereign_core.crypto.os.replace", side_effect=fail_on_second_replace):
            with pytest.raises(SovereignStorageError):
                loaded_manager.rotate_keypair()

        orphaned = list(loaded_manager.key_dir.glob("tmp*"))
        assert len(orphaned) == 0, (
            f"Rollback must remove all temp files; found: {orphaned}"
        )

    def test_manager_still_functional_after_promotion_rollback(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """After rollback, the manager mints verifiable receipts and can successfully rotate."""
        original_key = loaded_manager.public_key
        real_replace = os.replace
        call_count = 0

        def fail_on_second_replace(src: str, dst) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("Simulated failure on .pub os.replace")
            return real_replace(src, dst)

        with patch("sovereign_core.crypto.os.replace", side_effect=fail_on_second_replace):
            with pytest.raises(SovereignStorageError):
                loaded_manager.rotate_keypair()

        # Receipts still verify against the original key
        payload: dict[str, Any] = {"post_rollback": "audit_check"}
        receipt = loaded_manager.generate_receipt(payload)
        assert SovereignKeyManager.verify_receipt(receipt, payload, expected_public_key=original_key), (
            "Manager must produce valid receipts with the original key after rollback"
        )

        # A subsequent clean rotation must succeed end-to-end
        pre_key = loaded_manager.public_key
        success_receipt = loaded_manager.rotate_keypair()
        assert SovereignKeyManager.verify_succession(success_receipt, pre_key), (
            "A clean rotation following a rolled-back rotation must produce a valid succession receipt"
        )

    def test_first_replace_failure_cleans_up_both_staging_files(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """When the first os.replace (.pem promotion) fails, both staged temp files are deleted.

        Validates Task 1: the cleanup guard around the initial promotion step
        purges both tmp_pem_path and tmp_pub_path so no un-promoted key material
        is left on disk after the exception propagates.
        """
        real_replace = os.replace
        call_count = 0

        def fail_on_first_replace(src: str, dst) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("Simulated failure on .pem os.replace")
            return real_replace(src, dst)

        with patch("sovereign_core.crypto.os.replace", side_effect=fail_on_first_replace):
            with pytest.raises(OSError, match="Simulated failure on .pem os.replace"):
                loaded_manager.rotate_keypair()

        orphaned = list(loaded_manager.key_dir.glob("tmp*"))
        assert len(orphaned) == 0, (
            f"Both staged temp files must be deleted when the first os.replace fails; found: {orphaned}"
        )

    def test_pub_promotion_and_rollback_both_fail_raises_critical_split_state_error(
        self, loaded_manager: SovereignKeyManager
    ) -> None:
        """When .pub promotion and the .pem rollback os.replace both fail, the critical split-state message is raised.

        Validates Task 2: the conditional error message path emits the CRITICAL FAILURE
        warning when rollback_succeeded remains False after the nested restore attempt.
        """
        real_replace = os.replace
        call_count = 0

        def fail_from_second_call_onwards(src: str, dst) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise OSError("Simulated failure")
            return real_replace(src, dst)

        with patch("sovereign_core.crypto.os.replace", side_effect=fail_from_second_call_onwards):
            with pytest.raises(SovereignStorageError) as exc_info:
                loaded_manager.rotate_keypair()

        msg = str(exc_info.value)
        assert "CRITICAL FAILURE" in msg, (
            f"Exception message must contain 'CRITICAL FAILURE' when rollback also fails; got: {msg!r}"
        )
        assert "split-state" in msg, (
            f"Exception message must contain 'split-state' when rollback also fails; got: {msg!r}"
        )
