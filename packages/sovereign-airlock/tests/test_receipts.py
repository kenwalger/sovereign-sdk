"""TDD test suite for sovereign_airlock.receipt — write before implementation."""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sovereign_core import ForensicReceipt, SovereignKeyManager
from sovereign_ledger import SovereignLedger, SovereignStorageError

from sovereign_airlock.receipt import ReceiptBuilder
from sovereign_airlock.telemetry import AirlockTelemetry
from sovereign_sieve import SieveOutput


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def key_manager(tmp_path: Path, sovereign_secret: str) -> SovereignKeyManager:
    """Initialised SovereignKeyManager with a fresh keypair in a temp directory."""
    km = SovereignKeyManager(key_dir=str(tmp_path / "keys"))
    km.load_or_generate_keypair()
    return km


@pytest.fixture
def mem_ledger() -> SovereignLedger:
    """In-memory SovereignLedger for isolated test runs."""
    ledger = SovereignLedger(":memory:")
    yield ledger
    ledger.close()


@pytest.fixture
def telemetry() -> AirlockTelemetry:
    import hashlib

    return AirlockTelemetry(
        raw_tokens=120,
        sieved_tokens=90,
        tax_savings_percentage=25.0,
        payload_hash=hashlib.sha256(b"test raw content").hexdigest(),
    )


@pytest.fixture
def builder(key_manager: SovereignKeyManager, mem_ledger: SovereignLedger) -> ReceiptBuilder:
    return ReceiptBuilder(key_manager=key_manager, ledger=mem_ledger)


# ---------------------------------------------------------------------------
# TestReceiptBuilder
# ---------------------------------------------------------------------------

class TestReceiptBuilder:
    def test_build_and_commit_returns_forensic_receipt(
        self, builder: ReceiptBuilder, telemetry: AirlockTelemetry
    ) -> None:
        """build_and_commit returns a populated ForensicReceipt TypedDict."""
        receipt = builder.build_and_commit(
            sieved_content="clean payload text",
            telemetry=telemetry,
            policy_warnings=[],
            source="raw",
        )
        assert isinstance(receipt, dict)
        assert "payload_hash" in receipt
        assert "timestamp" in receipt
        assert "signature" in receipt
        assert "public_key" in receipt

    def test_receipt_metadata_contains_boundary_key(
        self, builder: ReceiptBuilder, telemetry: AirlockTelemetry
    ) -> None:
        """Receipt metadata identifies sovereign-sdk-airlock as the producing boundary."""
        receipt = builder.build_and_commit("content", telemetry, [], "openai")
        assert receipt["metadata"]["boundary"] == "sovereign-sdk-airlock"

    def test_receipt_metadata_contains_prose_tax_summary(
        self, builder: ReceiptBuilder, telemetry: AirlockTelemetry
    ) -> None:
        """prose_tax_summary in metadata carries the three telemetry metrics."""
        receipt = builder.build_and_commit("content", telemetry, [], "openai")
        pts = receipt["metadata"]["prose_tax_summary"]
        assert pts["raw_token_count"] == telemetry.raw_tokens
        assert pts["optimized_token_count"] == telemetry.sieved_tokens
        assert pts["tax_savings_percentage"] == telemetry.tax_savings_percentage

    def test_receipt_metadata_includes_source_transport(
        self, builder: ReceiptBuilder, telemetry: AirlockTelemetry
    ) -> None:
        """source_transport in metadata records the normalised payload origin."""
        receipt = builder.build_and_commit("content", telemetry, [], "anthropic")
        assert receipt["metadata"]["source_transport"] == "anthropic"

    def test_policy_warnings_included_when_non_empty(
        self, builder: ReceiptBuilder, telemetry: AirlockTelemetry
    ) -> None:
        """Non-empty policy_warnings list is sealed inside receipt metadata."""
        warnings = ["Rule 'excessive_context' matched.", "Rule 'guard_internal' matched."]
        receipt = builder.build_and_commit("content", telemetry, warnings, "raw")
        assert receipt["metadata"]["policy_warnings"] == warnings

    def test_policy_warnings_absent_when_empty(
        self, builder: ReceiptBuilder, telemetry: AirlockTelemetry
    ) -> None:
        """Empty policy_warnings list does not add the key to receipt metadata."""
        receipt = builder.build_and_commit("content", telemetry, [], "raw")
        assert "policy_warnings" not in receipt["metadata"]

    def test_receipt_is_cryptographically_verifiable(
        self, builder: ReceiptBuilder, key_manager: SovereignKeyManager, telemetry: AirlockTelemetry
    ) -> None:
        """Receipt produced by build_and_commit verifies successfully against the signing key."""
        sieved = "verified sieved content"
        receipt = builder.build_and_commit(sieved, telemetry, [], "raw")
        payload_dict = {"content": sieved}
        assert SovereignKeyManager.verify_receipt(
            receipt, payload_dict, expected_public_key=key_manager.public_key
        )

    def test_ledger_commit_called_when_ledger_provided(
        self, builder: ReceiptBuilder, mem_ledger: SovereignLedger, telemetry: AirlockTelemetry
    ) -> None:
        """Receipt is appended to the ledger when a ledger instance is configured."""
        receipt = builder.build_and_commit("content", telemetry, [], "raw")
        assert mem_ledger.verify_ledger_integrity(expected_tip_hash=receipt["payload_hash"])

    def test_ledger_write_failure_is_non_fatal(
        self,
        key_manager: SovereignKeyManager,
        telemetry: AirlockTelemetry,
        caplog,
    ) -> None:
        """A ledger write failure does not raise; warning is emitted and receipt returned."""
        failing_ledger = MagicMock(spec=SovereignLedger)
        failing_ledger.append_receipt.side_effect = SovereignStorageError("simulated ledger failure")
        builder = ReceiptBuilder(key_manager=key_manager, ledger=failing_ledger)

        with caplog.at_level(logging.WARNING, logger="sovereign_airlock.receipt"):
            receipt = builder.build_and_commit("content", telemetry, [], "raw")

        assert receipt is not None
        assert "payload_hash" in receipt
        assert any("ledger" in record.message.lower() for record in caplog.records)

    def test_no_ledger_commit_when_ledger_is_none(
        self, key_manager: SovereignKeyManager, telemetry: AirlockTelemetry
    ) -> None:
        """build_and_commit succeeds and returns a receipt when ledger=None."""
        builder = ReceiptBuilder(key_manager=key_manager, ledger=None)
        receipt = builder.build_and_commit("content", telemetry, [], "raw")
        assert receipt is not None
        assert "payload_hash" in receipt
