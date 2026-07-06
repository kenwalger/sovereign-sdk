# packages/sovereign-airlock/src/sovereign_airlock/receipt.py
import logging
from typing import Any

from sovereign_core import ForensicReceipt, SovereignKeyManager
from sovereign_ledger import SovereignLedger

from .telemetry import AirlockTelemetry

_logger = logging.getLogger(__name__)


class ReceiptBuilder:
    """Assembles and commits cryptographically signed :class:`ForensicReceipt` records for
    Airlock boundary crossing events.

    Coordinates with :class:`~sovereign_core.SovereignKeyManager` for Ed25519 signing
    and with :class:`~sovereign_ledger.SovereignLedger` for durable evidence commit.
    Ledger write failures are non-fatal: a warning is emitted locally and the receipt
    is returned regardless, so that outbound transmission is never blocked by a
    storage-tier anomaly (per SAR-0002 and SAR-0009).

    :param key_manager: Initialised :class:`~sovereign_core.SovereignKeyManager` holding
        the node's Ed25519 identity keypair.
    :type key_manager: SovereignKeyManager
    :param ledger: Optional :class:`~sovereign_ledger.SovereignLedger` instance for
        durable receipt persistence.  When ``None``, receipts are signed but not stored.
    :type ledger: SovereignLedger | None
    """

    _key_manager: SovereignKeyManager
    _ledger: SovereignLedger | None

    def __init__(
        self,
        key_manager: SovereignKeyManager,
        ledger: SovereignLedger | None = None,
    ) -> None:
        """Initialise the builder with a signing identity and optional durable store.

        :param key_manager: Configured key manager for Ed25519 receipt signing.
        :type key_manager: SovereignKeyManager
        :param ledger: Optional ledger for Write-Side Custody commit.
        :type ledger: SovereignLedger | None
        """
        self._key_manager = key_manager
        self._ledger = ledger

    def build_and_commit(
        self,
        sieved_content: str,
        telemetry: AirlockTelemetry,
        policy_warnings: list[str],
        source: str,
    ) -> ForensicReceipt:
        """Build a signed :class:`ForensicReceipt` and attempt a durable ledger commit.

        Assembles the receipt metadata from the Airlock boundary telemetry, optional
        policy warnings, and source transport identifier.  Signs the payload via
        :meth:`~sovereign_core.SovereignKeyManager.generate_receipt` using the node's
        Ed25519 private key, then commits to :class:`~sovereign_ledger.SovereignLedger`
        if one is configured.

        Ledger write failure is non-fatal.  If :meth:`~sovereign_ledger.SovereignLedger.append_receipt`
        raises, the failure is logged at ``WARNING`` level with the payload hash and
        exception details, and the signed receipt is returned to the caller regardless.
        This behaviour preserves the SAR-0009 intent — evidence of the boundary crossing
        is created before transmission — while honouring the plan's failure contract:
        evidence recording must never block or abort outbound transmission.

        :param sieved_content: The Prose-Tax-minimised string produced by the sieve pass.
        :type sieved_content: str
        :param telemetry: Sieve metrics for this boundary crossing event.
        :type telemetry: AirlockTelemetry
        :param policy_warnings: List of non-fatal policy warning messages collected during
            evaluation.  Omitted from metadata when empty.
        :type policy_warnings: list[str]
        :param source: Source transport identifier from the :class:`~sovereign_airlock.payload.NormalizedPayload`.
        :type source: str
        :return: A signed :class:`~sovereign_core.ForensicReceipt` whose ``metadata`` includes
            ``boundary``, ``source_transport``, ``payload_hash`` (the pre-sieve raw content
            hash from telemetry), ``prose_tax_summary``, and optionally ``policy_warnings``.
        :rtype: ForensicReceipt
        :raises Exception: Any exception raised by :meth:`~sovereign_core.SovereignKeyManager.generate_receipt`
            propagates directly (signing failures are not silenced).
        """
        metadata: dict[str, Any] = {
            "boundary": "sovereign-sdk-airlock",
            "source_transport": source,
            "payload_hash": telemetry.payload_hash,
            "prose_tax_summary": {
                "raw_token_count": telemetry.raw_tokens,
                "optimized_token_count": telemetry.sieved_tokens,
                "tax_savings_percentage": telemetry.tax_savings_percentage,
            },
        }
        if policy_warnings:
            metadata["policy_warnings"] = policy_warnings

        receipt: ForensicReceipt = self._key_manager.generate_receipt(
            {"content": sieved_content},
            metadata,
        )

        if self._ledger is not None:
            try:
                self._ledger.append_receipt(receipt, sieved_content)
            except Exception as ledger_exc:
                _logger.warning(
                    "Airlock ledger write failure — payload_hash=%s error=%r",
                    telemetry.payload_hash,
                    ledger_exc,
                )

        return receipt
