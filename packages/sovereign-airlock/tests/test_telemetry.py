"""TDD test suite for sovereign_airlock.telemetry — write before implementation."""

import hashlib

import pytest

from sovereign_airlock.telemetry import AirlockTelemetry
from sovereign_sieve import SieveOutput


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sieve_output(
    text: str = "clean text",
    raw_token_count: int = 100,
    optimized_token_count: int = 80,
    tax_savings_percentage: float = 20.0,
) -> SieveOutput:
    return SieveOutput(
        text=text,
        raw_token_count=raw_token_count,
        optimized_token_count=optimized_token_count,
        tax_savings_percentage=tax_savings_percentage,
    )


# ---------------------------------------------------------------------------
# TestAirlockTelemetry
# ---------------------------------------------------------------------------

class TestAirlockTelemetry:
    def test_raw_tokens_from_sieve_output(self) -> None:
        """raw_tokens is sourced directly from SieveOutput.raw_token_count."""
        output = _make_sieve_output(raw_token_count=200)
        telemetry = AirlockTelemetry.from_sieve_output(output, "raw input text")
        assert telemetry.raw_tokens == 200

    def test_sieved_tokens_from_sieve_output(self) -> None:
        """sieved_tokens is sourced from SieveOutput.optimized_token_count."""
        output = _make_sieve_output(optimized_token_count=150)
        telemetry = AirlockTelemetry.from_sieve_output(output, "raw input text")
        assert telemetry.sieved_tokens == 150

    def test_tax_savings_percentage_calculated_correctly(self) -> None:
        """tax_savings_percentage is re-derived from raw and sieved token counts."""
        output = _make_sieve_output(raw_token_count=100, optimized_token_count=75)
        telemetry = AirlockTelemetry.from_sieve_output(output, "content")
        assert telemetry.tax_savings_percentage == pytest.approx(25.0, abs=1e-3)

    def test_zero_raw_tokens_defaults_to_zero_savings(self) -> None:
        """Explicit ZeroDivisionError guard: raw_tokens==0 → tax_savings_percentage=0.0."""
        output = _make_sieve_output(raw_token_count=0, optimized_token_count=0)
        telemetry = AirlockTelemetry.from_sieve_output(output, "")
        assert telemetry.tax_savings_percentage == 0.0

    def test_payload_hash_is_sha256_of_raw_content(self) -> None:
        """payload_hash equals the SHA-256 hex digest of the raw content string."""
        raw = "the raw input before sieving"
        output = _make_sieve_output()
        telemetry = AirlockTelemetry.from_sieve_output(output, raw)
        expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        assert telemetry.payload_hash == expected

    def test_payload_hash_uniqueness_across_distinct_content(self) -> None:
        """Different raw content strings produce different payload hashes."""
        output = _make_sieve_output()
        t1 = AirlockTelemetry.from_sieve_output(output, "content A")
        t2 = AirlockTelemetry.from_sieve_output(output, "content B")
        assert t1.payload_hash != t2.payload_hash

    def test_immutable_frozen_dataclass(self) -> None:
        """AirlockTelemetry is frozen; field assignment raises FrozenInstanceError."""
        from dataclasses import FrozenInstanceError

        output = _make_sieve_output()
        telemetry = AirlockTelemetry.from_sieve_output(output, "content")
        with pytest.raises(FrozenInstanceError):
            telemetry.raw_tokens = 999  # type: ignore[misc]

    def test_tax_savings_percentage_rounds_to_four_decimal_places(self) -> None:
        """tax_savings_percentage is rounded to exactly 4 decimal places."""
        # 1/3 savings → 33.333...% — must round to 4 dp
        output = _make_sieve_output(raw_token_count=3, optimized_token_count=2)
        telemetry = AirlockTelemetry.from_sieve_output(output, "abc")
        assert telemetry.tax_savings_percentage == round(100.0 / 3.0, 4)

    def test_payload_hash_is_64_hex_characters(self) -> None:
        """SHA-256 output is always 64 lowercase hex characters."""
        output = _make_sieve_output()
        telemetry = AirlockTelemetry.from_sieve_output(output, "any content")
        assert len(telemetry.payload_hash) == 64
        assert all(c in "0123456789abcdef" for c in telemetry.payload_hash)

    def test_negative_savings_clamped_to_zero(self) -> None:
        """When sieved_tokens > raw_tokens (content expansion), tax_savings_percentage is clamped to 0.0."""
        output = _make_sieve_output(raw_token_count=50, optimized_token_count=75)
        telemetry = AirlockTelemetry.from_sieve_output(output, "short input that expanded")
        assert telemetry.tax_savings_percentage == 0.0

    def test_over_optimized_savings_clamped_to_hundred(self) -> None:
        """When optimized_token_count is negative (impossible expansion inversion), tax_savings_percentage is clamped to 100.0."""
        output = _make_sieve_output(raw_token_count=100, optimized_token_count=-10)
        telemetry = AirlockTelemetry.from_sieve_output(output, "impossible sieve output")
        assert telemetry.tax_savings_percentage == 100.0

    def test_full_sieve_round_trip_via_sieve_with_metrics(self) -> None:
        """AirlockTelemetry built from a live sieve_with_metrics pass is self-consistent."""
        from sovereign_sieve import sieve_with_metrics

        raw = "Hello! Please just run the analysis pipeline."
        live_output = sieve_with_metrics(raw)
        telemetry = AirlockTelemetry.from_sieve_output(live_output, raw)
        assert telemetry.raw_tokens == live_output.raw_token_count
        assert telemetry.sieved_tokens == live_output.optimized_token_count
        if live_output.raw_token_count == 0:
            assert telemetry.tax_savings_percentage == 0.0
        else:
            expected_pct = round(
                (live_output.raw_token_count - live_output.optimized_token_count)
                / live_output.raw_token_count
                * 100.0,
                4,
            )
            assert telemetry.tax_savings_percentage == expected_pct
