# packages/sovereign-airlock/src/sovereign_airlock/telemetry.py
import hashlib
from dataclasses import dataclass

from sovereign_sieve import SieveOutput


@dataclass(frozen=True)
class AirlockTelemetry:
    """Immutable metrics record produced by the Airlock sieve convergence pass.

    Carries the four Component C metrics defined in the Airlock specification:
    ``raw_tokens``, ``sieved_tokens``, ``tax_savings_percentage``, and
    ``payload_hash``.  All fields are set at construction time and are
    immutable for the lifetime of the record.

    :param raw_tokens: Estimated token count of the original pre-sieve content.
    :type raw_tokens: int
    :param sieved_tokens: Estimated token count after Prose Tax minimisation.
    :type sieved_tokens: int
    :param tax_savings_percentage: Percentage reduction from raw to sieved, clamped
        to ``[0.0, 100.0]`` and rounded to four decimal places.  Explicitly
        ``0.0`` when ``raw_tokens == 0`` (ZeroDivisionError guard) or when the
        sieve expands content beyond the raw token count.
    :type tax_savings_percentage: float
    :param payload_hash: SHA-256 hex digest of the raw (pre-sieve) content string.
        Serves as an observable identifier for cross-referencing the incoming
        payload across telemetry logs and ledger entries.
    :type payload_hash: str
    """

    raw_tokens: int
    sieved_tokens: int
    tax_savings_percentage: float
    payload_hash: str

    @classmethod
    def from_sieve_output(cls, sieve_output: SieveOutput, raw_content: str) -> "AirlockTelemetry":
        """Construct an :class:`AirlockTelemetry` from a :class:`SieveOutput` and the raw content.

        Re-derives ``tax_savings_percentage`` from the token counts rather than
        trusting the value carried inside ``sieve_output``, providing an independent
        layer of defence against upstream floating-point drift.  Includes an explicit
        guard: if ``raw_tokens == 0``, ``tax_savings_percentage`` is forced to ``0.0``
        to prevent ZeroDivisionError on zero-token or null-pass edge cases.

        :param sieve_output: The structured result of a ``sieve_with_metrics`` pass.
        :type sieve_output: SieveOutput
        :param raw_content: The original string fed into the sieve, used to derive
            ``payload_hash`` via SHA-256.
        :type raw_content: str
        :return: A frozen :class:`AirlockTelemetry` instance.
        :rtype: AirlockTelemetry
        """
        raw_tokens: int = sieve_output.raw_token_count
        sieved_tokens: int = sieve_output.optimized_token_count
        payload_hash: str = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()

        if raw_tokens == 0:
            tax_savings_percentage: float = 0.0
        else:
            tax_savings_percentage = max(0.0, min(100.0, round(
                (raw_tokens - sieved_tokens) / raw_tokens * 100.0, 4
            )))

        return cls(
            raw_tokens=raw_tokens,
            sieved_tokens=sieved_tokens,
            tax_savings_percentage=tax_savings_percentage,
            payload_hash=payload_hash,
        )
