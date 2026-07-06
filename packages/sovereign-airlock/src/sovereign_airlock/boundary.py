# packages/sovereign-airlock/src/sovereign_airlock/boundary.py
import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sovereign_core import ForensicReceipt, SovereignKeyManager
from sovereign_ledger import SovereignLedger
from sovereign_sieve import sieve_with_metrics

from .exception import AirlockPolicyViolation
from .payload import NormalizedPayload
from .policy import PolicyEngine, PolicyVerdict
from .receipt import ReceiptBuilder
from .telemetry import AirlockTelemetry

_logger = logging.getLogger(__name__)


@dataclass
class AirlockResult:
    """Structured outcome of a successful Airlock boundary crossing evaluation.

    Returned by :meth:`AirlockBoundary.process` when the outbound payload is
    permitted (i.e. no ``deny`` rule matched).  Callers use this object to
    obtain the sieved payload, governance telemetry, the sealed evidence receipt,
    and any non-fatal policy warnings that were triggered during evaluation.

    :param sieved_content: The Prose-Tax-minimised string produced by the
        :mod:`sovereign_sieve` convergence pass.  This is the content that
        was signed and committed to the ledger, and that should be used for
        the actual outbound transmission.
    :type sieved_content: str
    :param telemetry: Sieve-pass metrics for this boundary event.
    :type telemetry: AirlockTelemetry
    :param receipt: The cryptographically signed :class:`~sovereign_core.ForensicReceipt`
        produced for this crossing.  ``None`` only when signing itself fails
        (an unusual, non-ledger failure path).
    :type receipt: ForensicReceipt | None
    :param policy_warnings: Human-readable messages emitted by ``warn``-action rules
        that matched during evaluation.  Empty list when all rules were silent.
    :type policy_warnings: list[str]
    """

    sieved_content: str
    telemetry: AirlockTelemetry
    receipt: ForensicReceipt | None
    policy_warnings: list[str] = field(default_factory=list)


class AirlockBoundary:
    """Outbound governance boundary implementing the four-component Airlock lifecycle.

    Orchestrates the transaction lifecycle defined in the Airlock specification:

    1. **Policy evaluation** (Component B) — payload is inspected against local YAML rules.
    2. **Sieve convergence** (Component C) — context minimisation via :mod:`sovereign_sieve`.
    3. **Evidence generation** (Component D) — a signed :class:`~sovereign_core.ForensicReceipt`
       is committed to the configured :class:`~sovereign_ledger.SovereignLedger`.

    Boundary traversal (step 4 — the actual outbound API call) is performed by the caller
    after this class returns the :class:`AirlockResult`.  Evidence recording is non-fatal:
    a ledger write failure logs a local warning and returns the result regardless, so
    outbound transmission is never blocked by a storage-tier anomaly.

    :param policy_path: Path to the YAML policy configuration file.
    :type policy_path: str | Path
    :param signing_key: Directory path for :class:`~sovereign_core.SovereignKeyManager`
        keypair storage (same semantics as ``key_dir`` on that class).
    :type signing_key: str | Path
    :param ledger: Optional :class:`~sovereign_ledger.SovereignLedger` for durable
        receipt persistence.  When ``None``, receipts are signed but not stored.
    :type ledger: SovereignLedger | None
    :raises AirlockConfigurationError: If the policy YAML file is missing or malformed.
    """

    _policy: PolicyEngine
    _receipt_builder: ReceiptBuilder

    def __init__(
        self,
        policy_path: str | Path,
        signing_key: str | Path,
        ledger: SovereignLedger | None = None,
    ) -> None:
        """Initialise the boundary with a policy engine, signing identity, and optional ledger.

        :param policy_path: Path to the YAML policy configuration file.
        :type policy_path: str | Path
        :param signing_key: Key directory for :class:`~sovereign_core.SovereignKeyManager`.
        :type signing_key: str | Path
        :param ledger: Optional ledger for Write-Side Custody evidence commit.
        :type ledger: SovereignLedger | None
        """
        self._policy = PolicyEngine(policy_path)
        key_manager = SovereignKeyManager(key_dir=signing_key)
        self._receipt_builder = ReceiptBuilder(key_manager=key_manager, ledger=ledger)

    async def process(self, payload: NormalizedPayload) -> AirlockResult:
        """Execute the full Airlock governance lifecycle for a single outbound payload.

        Transaction lifecycle (per the Airlock specification sequence diagram):

        1. Evaluate all policy rules against the normalised payload.  A ``deny`` verdict
           raises :exc:`~sovereign_airlock.exception.AirlockPolicyViolation` immediately
           and no sieve or ledger operation is performed.
        2. Pass the combined content through :func:`~sovereign_sieve.sieve_with_metrics`
           via :func:`asyncio.to_thread` to prevent CPU-bound blocking on the event loop,
           producing the Prose-Tax-minimised sieved content and token metrics.
        3. Invoke :meth:`~sovereign_airlock.policy.PolicyEngine.evaluate_post_sieve` with
           the sieve telemetry.  Post-sieve ``sieved_tokens`` and ``tax_savings_percentage``
           telemetry rules are evaluated against actual values; a ``deny`` outcome raises
           :exc:`~sovereign_airlock.exception.AirlockPolicyViolation`.  The
           ``prose_tax_warning_threshold`` check is also applied here; breaches are
           appended as non-fatal warnings.
        4. Attempt evidence recording via :class:`~sovereign_airlock.receipt.ReceiptBuilder`.
           Ledger write failure is non-fatal: a warning is emitted and the result is
           returned with the signed receipt intact.  Signing failure is logged and the
           result is returned with ``receipt=None``.

        :param payload: The provider-neutral payload produced by one of the normalisation
            factory functions in :mod:`~sovereign_airlock.payload`.
        :type payload: NormalizedPayload
        :return: A populated :class:`AirlockResult` carrying the sieved content, telemetry,
            signed receipt, and any non-fatal policy warnings.
        :rtype: AirlockResult
        :raises AirlockPolicyViolation: If any configured ``deny`` rule matches the payload
            or the global token ceiling is breached.
        """
        # Component B: Policy evaluation (pre-sieve)
        verdict: PolicyVerdict = self._policy.evaluate(payload)
        if not verdict.allowed:
            raise AirlockPolicyViolation(
                "; ".join(verdict.violations),
                warnings=verdict.warnings,
            )

        # Component C: Sieve convergence pass (offloaded to thread pool — CPU-bound)
        raw_content: str = " ".join(payload.content)
        sieve_output = await asyncio.to_thread(sieve_with_metrics, raw_content)
        telemetry: AirlockTelemetry = AirlockTelemetry.from_sieve_output(
            sieve_output, raw_content
        )

        # Post-sieve: evaluate telemetry rules requiring actual sieve output
        post_verdict = self._policy.evaluate_post_sieve(telemetry)
        if not post_verdict.allowed:
            combined_warnings = list(verdict.warnings) + list(post_verdict.warnings)
            raise AirlockPolicyViolation(
                "; ".join(post_verdict.violations),
                warnings=combined_warnings,
            )
        verdict.warnings.extend(post_verdict.warnings)

        # Component D: Evidence generation — non-fatal on any failure
        receipt: ForensicReceipt | None = None
        try:
            receipt = self._receipt_builder.build_and_commit(
                sieve_output.text,
                telemetry,
                verdict.warnings,
                payload.source,
            )
        except Exception as exc:
            _logger.warning(
                "Airlock evidence recording failed — payload_hash=%s error=%r",
                telemetry.payload_hash,
                exc,
            )

        return AirlockResult(
            sieved_content=sieve_output.text,
            telemetry=telemetry,
            receipt=receipt,
            policy_warnings=verdict.warnings,
        )
