# packages/sovereign-edge/src/sovereign_edge/pipeline.py
"""EdgePipeline: intercept → sieve → sign → ledger dispatch orchestrator."""
import binascii
import hashlib
import hmac as _hmac
import json
import sqlite3
from pathlib import Path
from typing import Any

from sovereign_core.crypto import ForensicReceipt, SovereignKeyManager
from sovereign_ledger import SovereignLedger, SovereignStorageError
from sovereign_sieve import SieveOutput, sieve_with_metrics

from .buffer import OffGridBuffer
from .models import EdgeResult, SensorFrame


class EdgePipeline:
    """Ingestion orchestrator that bridges sovereign-sensor raw payloads and sovereign-ledger storage.

    Intercepts sealed sensor wire frames, applies the sovereign-sieve Prose Tax
    transformation to produce minimized, verified content, mints a signed
    :class:`~sovereign_core.crypto.ForensicReceipt` via its own Ed25519 identity,
    and commits the receipt to the ledger.  When the ledger raises
    :exc:`~sovereign_ledger.SovereignStorageError` or a ``sqlite3`` error, the receipt
    is queued to the off-grid JSONL buffer and can be flushed later via
    :meth:`drain_buffer`.

    The pipeline does not own the ledger lifecycle; the caller is responsible for opening
    and closing it.  The pipeline does own the off-grid buffer lifecycle; callers must
    invoke :meth:`close` to terminate the background buffer writer thread and ensure any
    un-journaled receipts are surfaced before process exit.

    :param ledger: An open :class:`~sovereign_ledger.SovereignLedger` instance.
    :type ledger: SovereignLedger
    :param signing_key: Path to the edge node's Ed25519 private key PEM file.  The
        parent directory is created with ``mode=0o700`` on first use and its permissions
        are explicitly enforced via ``chmod(0o700)`` so that a pre-existing directory
        with lax permissions is corrected.  Defaults to ``".keys/edge_identity.pem"``
        relative to the current working directory.
    :type signing_key: str
    :param buffer_path: Filesystem path for the off-grid JSONL receipt buffer.  Defaults
        to ``".edge_buffer.jsonl"`` in the current working directory.
    :type buffer_path: str
    :param sensor_secret: Shared HMAC-SHA256 secret provisioned to every sensor node in
        this deployment.  When non-empty, :meth:`process` verifies the incoming frame's
        ``s`` field against a locally recomputed digest and raises :exc:`ValueError` on
        mismatch before the payload reaches the sieve or ledger.  Accepts either a
        ``str`` (UTF-8 encoded on assignment) or raw ``bytes``.  Pass an empty string or
        ``b""`` to disable inbound verification (default).
    :type sensor_secret: str | bytes
    """

    def __init__(
        self,
        ledger: SovereignLedger,
        signing_key: str = ".keys/edge_identity.pem",
        buffer_path: str = ".edge_buffer.jsonl",
        sensor_secret: str | bytes = b"",
    ) -> None:
        self._ledger: SovereignLedger = ledger
        self._buffer: OffGridBuffer = OffGridBuffer(buffer_path)
        key_path: Path = Path(signing_key).resolve()
        key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        key_path.parent.chmod(0o700)
        self._key_manager: SovereignKeyManager = SovereignKeyManager(key_dir=key_path.parent)
        self._key_manager.private_key_path = key_path
        self._key_manager.public_key_path = key_path.with_suffix(".pub")
        self._sensor_secret: bytes = (
            sensor_secret.encode("utf-8") if isinstance(sensor_secret, str) else sensor_secret
        )

    def process(self, frame_bytes: bytes) -> EdgeResult:
        """Parse, sieve, sign, and commit a sealed sensor wire frame.

        Execution proceeds in four deterministic stages:

        1. **Deserialize** — :meth:`SensorFrame.from_bytes` decodes the wire
           frame bytes into a typed :class:`SensorFrame`.
        2. **Sieve** — the sensor observation payload is serialized to canonical
           JSON and passed through :func:`~sovereign_sieve.sieve_with_metrics` to
           strip conversational filler, normalize whitespace, and capture Prose Tax
           telemetry.  If the sieve stage raises, the pipeline falls back to the raw
           ``text_content()`` string, sets ``tax_savings_percentage`` to ``0.0``, and
           stamps ``sieve_fault=True`` in the receipt metadata so downstream auditors
           can distinguish fault-path entries from normally minimized payloads.
        3. **Sign** — :meth:`~sovereign_core.crypto.SovereignKeyManager.generate_receipt`
           mints a :class:`~sovereign_core.crypto.ForensicReceipt` whose ``metadata``
           embeds the originating node identifier and the full Prose Tax summary.
        4. **Commit** — the receipt is submitted to the ledger via
           :meth:`~sovereign_ledger.SovereignLedger.append_receipt`.  On
           :exc:`~sovereign_ledger.SovereignStorageError` or ``sqlite3.Error``, the
           receipt is written to the off-grid buffer and ``buffered=True`` is set in
           the returned :class:`EdgeResult`.

        :param frame_bytes: Ultra-minified JSON bytes from
            :meth:`~sovereign_sensor.SovereignEnvelope.seal`.
        :type frame_bytes: bytes
        :return: A fully populated :class:`EdgeResult` recording the receipt
            identifier, sealed envelope, sieved content, Prose Tax metrics, and
            buffering disposition.
        :rtype: EdgeResult
        :raises json.JSONDecodeError: If ``frame_bytes`` is not valid JSON.
        :raises KeyError: If any mandatory sensor wire frame key is absent.
        :raises UnicodeDecodeError: If ``frame_bytes`` is not valid UTF-8.
        :raises ValueError: If ``sensor_secret`` is non-empty and ``frame.alg`` is not
            ``"hmac-sha256"`` (unsupported or unauthenticated algorithm), or if the
            frame's HMAC-SHA256 digest does not match the locally recomputed expected
            signature.
        """
        frame: SensorFrame = SensorFrame.from_bytes(frame_bytes)

        if self._sensor_secret:
            if frame.alg != "hmac-sha256":
                raise ValueError(
                    f"Unsupported or unauthenticated algorithm '{frame.alg}' for node "
                    f"'{frame.n}' sequence {frame.q}: sensor_secret requires hmac-sha256"
                )
            node_bytes: bytes = frame.n.encode("utf-8")
            time_bytes: bytes = frame.t.encode("utf-8")
            algo_bytes: bytes = frame.alg.encode("utf-8")
            canonical: str = json.dumps(
                frame.d, separators=(",", ":"), sort_keys=True, ensure_ascii=False
            )
            preimage: bytes = (
                f"1|{len(node_bytes)}:{frame.n}|{len(time_bytes)}:{frame.t}"
                f"|{frame.q}|{len(algo_bytes)}:{frame.alg}|{canonical}"
            ).encode("utf-8")
            expected_sig: str = binascii.hexlify(
                _hmac.new(self._sensor_secret, preimage, hashlib.sha256).digest()
            ).decode("utf-8")
            if not _hmac.compare_digest(expected_sig, frame.s.lower()):
                raise ValueError(
                    f"Sensor frame signature verification failed for node '{frame.n}' "
                    f"sequence {frame.q}: HMAC-SHA256 digest mismatch"
                )

        raw_text: str = frame.text_content()
        sieve_fault: bool = False
        try:
            sieve_result: SieveOutput = sieve_with_metrics(raw_text)
        except (ValueError, KeyError, RuntimeError, AttributeError, TypeError):
            sieve_fault = True
            token_estimate: int = max(0, len(raw_text.encode("utf-8")) // 4)
            sieve_result = SieveOutput(
                text=raw_text,
                raw_token_count=token_estimate,
                optimized_token_count=token_estimate,
                tax_savings_percentage=0.0,
            )

        edge_payload: dict[str, Any] = {
            "algorithm": frame.alg,
            "node_id": frame.n,
            "sensor_signature": frame.s,
            "sensor_timestamp": frame.t,
            "sequence": frame.q,
            "sieved_content": sieve_result.text,
        }
        metadata: dict[str, Any] = {
            "prose_tax_summary": {
                "optimized_token_count": sieve_result.optimized_token_count,
                "raw_token_count": sieve_result.raw_token_count,
                "tax_savings_percentage": sieve_result.tax_savings_percentage,
                "tokens_eliminated": sieve_result.raw_token_count - sieve_result.optimized_token_count,
            },
            "sensor_node": frame.n,
            "sequence": frame.q,
            "source": "SovereignEdge",
        }
        if sieve_fault:
            metadata["sieve_fault"] = True
        receipt: ForensicReceipt = self._key_manager.generate_receipt(
            payload=edge_payload,
            metadata=metadata,
        )
        receipt_dict: dict[str, Any] = dict(receipt)

        buffered: bool = False
        try:
            payload_hash: str = self._ledger.append_receipt(receipt_dict, sieve_result.text)
        except (SovereignStorageError, sqlite3.Error):
            self._buffer.push(receipt_dict, sieve_result.text)
            payload_hash = receipt_dict["payload_hash"]
            buffered = True

        return EdgeResult(
            payload_hash=payload_hash,
            receipt=receipt_dict,
            sieved_content=sieve_result.text,
            raw_token_count=sieve_result.raw_token_count,
            optimized_token_count=sieve_result.optimized_token_count,
            tax_savings_percentage=sieve_result.tax_savings_percentage,
            buffered=buffered,
        )

    def drain_buffer(self) -> list[str]:
        """Attempt to flush all buffered receipts to the ledger.

        Drains the off-grid buffer, replaying each ``(receipt, sieved_content)``
        pair against :meth:`~sovereign_ledger.SovereignLedger.append_receipt`.
        Successfully committed entries are collected and returned as ``payload_hash``
        strings.  Any entry that fails again due to a persistent ledger error is
        re-queued to the buffer so that no receipt is silently discarded.

        :return: ``payload_hash`` strings for every receipt successfully committed to
            the ledger on this drain pass.  Entries that could not be committed are
            re-queued and excluded from the returned list.
        :rtype: list[str]
        """
        committed: list[str] = []
        requeue: list[tuple[dict[str, Any], str]] = []

        for receipt_dict, sieved_content in self._buffer.drain():
            try:
                payload_hash: str = self._ledger.append_receipt(receipt_dict, sieved_content)
                committed.append(payload_hash)
            except (SovereignStorageError, sqlite3.Error):
                requeue.append((receipt_dict, sieved_content))

        for receipt_dict, sieved_content in requeue:
            self._buffer.push(receipt_dict, sieved_content)

        return committed

    def close(self) -> None:
        """Flush outstanding buffered receipts and terminate the background buffer worker.

        Executes a best-effort :meth:`drain_buffer` pass inside a ``try`` block before
        shutdown so that any receipts queued while the ledger was unreachable are committed
        to the ledger if it has since recovered.  :meth:`~sovereign_edge.buffer.OffGridBuffer.close`
        is invoked inside the corresponding ``finally`` block, guaranteeing that the background
        daemon writer thread is joined and resources are reclaimed even if the drain pass
        raises an unhandled exception — for example, an unexpected error propagating from the
        ledger layer that is not caught by :meth:`drain_buffer`'s internal error handling.

        The pipeline does not own the ledger lifecycle; the caller remains responsible
        for invoking :meth:`~sovereign_ledger.SovereignLedger.close` on the ledger
        instance after calling this method.

        :return: None
        :rtype: None
        :raises RuntimeError: If :meth:`~sovereign_edge.buffer.OffGridBuffer.close`
            detects one or more receipt entries still preserved in ``_write_errors``
            after the drain attempt, indicating that they failed to reach either the
            JSONL file or the ledger and remain un-journaled at shutdown.
        """
        try:
            self.drain_buffer()
        finally:
            self._buffer.close()

    @property
    def buffer_depth(self) -> int:
        """Current count of receipts queued in the off-grid buffer.

        :return: Number of buffered receipt entries awaiting ledger submission.
        :rtype: int
        """
        return self._buffer.size
