"""TDD test suite for sovereign_airlock.boundary — write before implementation."""

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sovereign_ledger import SovereignLedger, SovereignStorageError

from sovereign_airlock.boundary import AirlockBoundary, AirlockResult
from sovereign_airlock.exception import AirlockPolicyViolation
from sovereign_airlock.payload import (
    NormalizedPayload,
    normalize_anthropic,
    normalize_openai,
    normalize_raw,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_ledger() -> SovereignLedger:
    ledger = SovereignLedger(":memory:")
    yield ledger
    ledger.close()


@pytest.fixture
def airlock(tmp_path: Path, policy_path: Path, mem_ledger: SovereignLedger, sovereign_secret: str) -> AirlockBoundary:
    """Fully configured AirlockBoundary with an in-memory ledger."""
    return AirlockBoundary(
        policy_path=policy_path,
        signing_key=str(tmp_path / "keys"),
        ledger=mem_ledger,
    )


@pytest.fixture
def airlock_no_ledger(tmp_path: Path, policy_path: Path, sovereign_secret: str) -> AirlockBoundary:
    """AirlockBoundary operating without a ledger."""
    return AirlockBoundary(
        policy_path=policy_path,
        signing_key=str(tmp_path / "keys"),
        ledger=None,
    )


# ---------------------------------------------------------------------------
# TestAirlockBoundaryHappyPath
# ---------------------------------------------------------------------------

class TestAirlockBoundaryHappyPath:
    async def test_process_returns_airlock_result(self, airlock: AirlockBoundary) -> None:
        """process() returns an AirlockResult for a clean payload."""
        payload = normalize_raw("Analyse the temperature dataset.")
        result = await airlock.process(payload)
        assert isinstance(result, AirlockResult)

    async def test_result_contains_sieved_content(self, airlock: AirlockBoundary) -> None:
        """AirlockResult.sieved_content holds the Prose-Tax-minimised string."""
        payload = normalize_raw("Hello! Please just run the analysis.")
        result = await airlock.process(payload)
        assert isinstance(result.sieved_content, str)
        assert len(result.sieved_content) > 0

    async def test_result_contains_telemetry(self, airlock: AirlockBoundary) -> None:
        """AirlockResult.telemetry carries non-None AirlockTelemetry."""
        from sovereign_airlock.telemetry import AirlockTelemetry

        payload = normalize_raw("Run the pipeline.")
        result = await airlock.process(payload)
        assert isinstance(result.telemetry, AirlockTelemetry)

    async def test_result_contains_signed_receipt(self, airlock: AirlockBoundary) -> None:
        """AirlockResult.receipt is a signed ForensicReceipt when signing succeeds."""
        payload = normalize_raw("Clean payload for signing.")
        result = await airlock.process(payload)
        assert result.receipt is not None
        assert "signature" in result.receipt
        assert "payload_hash" in result.receipt

    async def test_result_policy_warnings_empty_for_clean_payload(self, airlock: AirlockBoundary) -> None:
        """A payload triggering no policy rules produces an empty warnings list."""
        payload = normalize_raw("Analyse the public dataset.")
        result = await airlock.process(payload)
        assert result.policy_warnings == []

    async def test_ledger_receives_receipt_after_process(
        self, airlock: AirlockBoundary, mem_ledger: SovereignLedger
    ) -> None:
        """The ledger chain is extended by one after a successful process call."""
        payload = normalize_raw("Governance test payload.")
        result = await airlock.process(payload)
        assert result.receipt is not None
        assert mem_ledger.verify_ledger_integrity(expected_tip_hash=result.receipt["payload_hash"])


# ---------------------------------------------------------------------------
# TestAirlockBoundaryPolicyDenial
# ---------------------------------------------------------------------------

class TestAirlockBoundaryPolicyDenial:
    async def test_deny_raises_airlock_policy_violation(self, airlock: AirlockBoundary) -> None:
        """A payload matching a deny rule raises AirlockPolicyViolation."""
        payload = normalize_raw("context: -----BEGIN PRIVATE KEY----- MIII...")
        with pytest.raises(AirlockPolicyViolation):
            await airlock.process(payload)

    async def test_policy_violation_message_contains_rule_name(self, airlock: AirlockBoundary) -> None:
        """AirlockPolicyViolation message identifies the violated rule name."""
        payload = normalize_raw("-----BEGIN PRIVATE KEY-----")
        with pytest.raises(AirlockPolicyViolation, match="block_private_keys"):
            await airlock.process(payload)

    async def test_deny_does_not_commit_to_ledger(
        self, airlock: AirlockBoundary, mem_ledger: SovereignLedger
    ) -> None:
        """Denied payloads produce no ledger entry."""
        payload = normalize_raw("-----BEGIN PRIVATE KEY-----")
        with pytest.raises(AirlockPolicyViolation):
            await airlock.process(payload)
        assert mem_ledger.verify_ledger_integrity()
        # Chain is intact but empty — no receipt was committed
        assert mem_ledger.verify_ledger_integrity(expected_tip_hash=None)


# ---------------------------------------------------------------------------
# TestAirlockBoundaryPolicyWarning
# ---------------------------------------------------------------------------

class TestAirlockBoundaryPolicyWarning:
    async def test_warn_returns_result_with_policy_warnings(self, airlock: AirlockBoundary) -> None:
        """A payload triggering a warn rule returns AirlockResult with non-empty policy_warnings."""
        payload = NormalizedPayload(
            source="openai",
            content=["contact internal.sovereign.local for config"],
            metadata={},
            tools=[],
            token_estimate=50,
        )
        result = await airlock.process(payload)
        assert result.policy_warnings != []
        assert any("guard_internal_namespaces" in w for w in result.policy_warnings)

    async def test_warn_does_not_block_process(self, airlock: AirlockBoundary) -> None:
        """A warn-level rule does not prevent AirlockResult from being returned."""
        payload = NormalizedPayload(
            source="raw",
            content=["internal.sovereign.local endpoint config"],
            metadata={},
            tools=[],
            token_estimate=50,
        )
        result = await airlock.process(payload)
        assert isinstance(result, AirlockResult)

    async def test_warn_policy_warnings_sealed_in_receipt_metadata(
        self, airlock: AirlockBoundary
    ) -> None:
        """Policy warnings from warn rules are propagated into the receipt metadata."""
        payload = NormalizedPayload(
            source="raw",
            content=["access internal.sovereign.local"],
            metadata={},
            tools=[],
            token_estimate=50,
        )
        result = await airlock.process(payload)
        assert result.receipt is not None
        assert "policy_warnings" in result.receipt["metadata"]


# ---------------------------------------------------------------------------
# TestAirlockBoundaryTransportNeutrality
# ---------------------------------------------------------------------------

class TestAirlockBoundaryTransportNeutrality:
    async def test_openai_normalized_payload_processes_correctly(
        self, airlock: AirlockBoundary
    ) -> None:
        """OpenAI-normalised payload traverses the full governance pipeline."""
        request = {
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Summarise the quarterly report."},
            ],
        }
        payload = normalize_openai(request)
        result = await airlock.process(payload)
        assert result.sieved_content
        assert result.telemetry.raw_tokens >= 0

    async def test_anthropic_normalized_payload_processes_correctly(
        self, airlock: AirlockBoundary
    ) -> None:
        """Anthropic-normalised payload traverses the full governance pipeline."""
        request = {
            "model": "claude-sonnet-4-6",
            "system": "You are a data analyst.",
            "messages": [{"role": "user", "content": "Analyse the dataset."}],
        }
        payload = normalize_anthropic(request)
        result = await airlock.process(payload)
        assert result.sieved_content
        assert result.telemetry.raw_tokens >= 0

    async def test_raw_normalized_payload_processes_correctly(
        self, airlock: AirlockBoundary
    ) -> None:
        """Raw-normalised payload traverses the full governance pipeline."""
        payload = normalize_raw("Process this raw text payload.", source="custom")
        result = await airlock.process(payload)
        assert result.sieved_content
        assert result.telemetry is not None


# ---------------------------------------------------------------------------
# TestAirlockBoundaryResiliency
# ---------------------------------------------------------------------------

class TestAirlockBoundaryResiliency:
    async def test_ledger_write_failure_does_not_abort_result(
        self,
        tmp_path: Path,
        policy_path: Path,
        sovereign_secret: str,
        caplog,
    ) -> None:
        """A ledger write failure is non-fatal; process() returns a result with a valid receipt."""
        failing_ledger = MagicMock(spec=SovereignLedger)
        failing_ledger.append_receipt.side_effect = SovereignStorageError("simulated failure")
        boundary = AirlockBoundary(
            policy_path=policy_path,
            signing_key=str(tmp_path / "keys"),
            ledger=failing_ledger,
        )
        payload = normalize_raw("governance test")

        with caplog.at_level(logging.WARNING):
            result = await boundary.process(payload)

        # Signing succeeded; ledger write failed silently — receipt is still returned
        assert result is not None
        assert result.receipt is not None

    async def test_process_succeeds_without_ledger(
        self, airlock_no_ledger: AirlockBoundary
    ) -> None:
        """AirlockBoundary operates correctly when no ledger is configured."""
        payload = normalize_raw("no ledger test payload")
        result = await airlock_no_ledger.process(payload)
        assert isinstance(result, AirlockResult)
        assert result.receipt is not None

    async def test_sieve_metrics_are_accurate(self, airlock: AirlockBoundary) -> None:
        """Telemetry metrics from the sieve pass are numerically consistent."""
        raw = "Hello! Please just help me run the analysis pipeline."
        payload = normalize_raw(raw)
        result = await airlock.process(payload)
        assert result.telemetry.raw_tokens >= result.telemetry.sieved_tokens
        assert 0.0 <= result.telemetry.tax_savings_percentage <= 100.0
