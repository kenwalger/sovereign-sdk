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


class SovereignDoubleFaultError(RuntimeError):
    """Raised when a signed receipt cannot reach either the ledger or the off-grid buffer.

    Encapsulates a total persistence failure: the ledger was unreachable (raising
    :exc:`~sovereign_ledger.SovereignStorageError` or ``sqlite3.Error``) and the
    off-grid buffer push also failed (raising any :exc:`Exception`).  The signed
    :class:`~sovereign_core.crypto.ForensicReceipt` dict is attached via
    :attr:`receipt` so the host application can retrieve it through an alternative
    channel rather than losing the payload entirely.

    :param args: Positional message arguments forwarded to :class:`RuntimeError`.
    :param receipt: The fully signed ForensicReceipt dict that could not be persisted.
    :type receipt: dict[str, Any]
    """

    def __init__(self, *args: object, receipt: dict[str, Any]) -> None:
        super().__init__(*args)
        self.receipt: dict[str, Any] = receipt


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
        :raises SovereignDoubleFaultError: If the ledger raises
            :exc:`~sovereign_ledger.SovereignStorageError` or ``sqlite3.Error`` *and*
            the subsequent :meth:`~sovereign_edge.buffer.OffGridBuffer.push` also raises.
            The signed receipt dict is attached to the exception via :attr:`~SovereignDoubleFaultError.receipt`.
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
                dict(frame.d), separators=(",", ":"), sort_keys=True, ensure_ascii=False
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
        except (SovereignStorageError, sqlite3.Error) as ledger_err:
            try:
                self._buffer.push(receipt_dict, sieve_result.text)
                payload_hash = receipt_dict["payload_hash"]
                buffered = True
            except Exception as push_err:
                raise SovereignDoubleFaultError(
                    "Ledger unavailable and off-grid buffer rejected payload; "
                    "the signed receipt is attached to this exception for host-level recovery",
                    receipt=receipt_dict,
                ) from push_err

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

        Eagerly materialises the drained entry list before the replay loop so
        that a ``processed`` index can track how far through the list the loop
        advanced.  If an unexpected exception (any :exc:`Exception` not caught
        by the inner :exc:`~sovereign_ledger.SovereignStorageError` /
        ``sqlite3.Error`` guard) aborts the replay pass mid-iteration, the
        ``except`` branch appends every un-processed entry
        (``drained[processed:]``) to the requeue list before the exception is
        re-raised.  This closes the data-loss window where local function scope
        held the only surviving references to those entries: when the original
        ``for`` loop was interrupted, all entries after the crash point were
        silently abandoned.

        Successfully committed entries are returned as ``payload_hash`` strings.
        Any entry that fails due to a persistent :exc:`~sovereign_ledger.SovereignStorageError`
        or ``sqlite3.Error`` is re-queued to the buffer so that no receipt is
        discarded on a transient ledger fault.

        The re-queue pass always iterates to completion regardless of
        :exc:`RuntimeError` from individual :meth:`~sovereign_edge.buffer.OffGridBuffer.push`
        calls so that every remaining item is attempted.  If an unexpected exception
        interrupted the replay pass AND some items cannot be re-queued, the requeue
        :exc:`RuntimeError` is raised chained from the crash exception so both failure
        sources are visible in the traceback.

        :return: ``payload_hash`` strings for every receipt successfully committed to
            the ledger on this drain pass.  Entries that could not be committed are
            re-queued and excluded from the returned list.
        :rtype: list[str]
        :raises RuntimeError: If the off-grid buffer file raises :exc:`OSError` on read
            (chained from the :exc:`OSError`), or if one or more entries cannot be re-queued
            after a ledger failure or after the replay loop was interrupted.  The exception
            is raised only after all requeue items have been attempted; when both the
            replay crash and a requeue failure occur simultaneously, the requeue
            :exc:`RuntimeError` is chained from the replay exception via ``__cause__``.
        """
        committed: list[str] = []
        requeue: list[tuple[dict[str, Any], str]] = []

        try:
            drained: list[tuple[dict[str, Any], str]] = list(self._buffer.drain())
        except OSError as read_err:
            raise RuntimeError(
                "drain_buffer() cannot replay buffered receipts: the off-grid buffer file "
                "could not be read from disk — verify that the storage tier is accessible"
            ) from read_err
        crash_exc: Exception | None = None
        processed: int = 0

        try:
            for receipt_dict, sieved_content in drained:
                try:
                    payload_hash: str = self._ledger.append_receipt(receipt_dict, sieved_content)
                    committed.append(payload_hash)
                except (SovereignStorageError, sqlite3.Error):
                    requeue.append((receipt_dict, sieved_content))
                processed += 1
        except Exception as exc:
            crash_exc = exc
            requeue.extend(drained[processed:])

        push_failure_count: int = 0
        for receipt_dict, sieved_content in requeue:
            try:
                self._buffer.push(receipt_dict, sieved_content)
            except RuntimeError:
                push_failure_count += 1

        if crash_exc is not None:
            if push_failure_count:
                requeue_err: RuntimeError = RuntimeError(
                    f"drain_buffer() could not re-queue {push_failure_count} "
                    f"receipt{'s' if push_failure_count != 1 else ''} after an unexpected "
                    "exception in the ledger replay pass; call drain() on the buffer to "
                    "recover un-committed entries"
                )
                raise requeue_err from crash_exc
            raise crash_exc

        if push_failure_count:
            raise RuntimeError(
                f"drain_buffer() could not re-queue {push_failure_count} "
                f"receipt{'s' if push_failure_count != 1 else ''} after ledger failure: "
                "buffer is closed or its background writer has terminated; "
                "call drain() on the buffer to recover pending entries"
            )

        return committed

    def close(self) -> None:
        """Flush outstanding buffered receipts and terminate the background buffer worker.

        Executes a best-effort :meth:`drain_buffer` pass before invoking
        :meth:`~sovereign_edge.buffer.OffGridBuffer.close` so that any receipts queued
        while the ledger was unreachable are committed if the ledger has since recovered.

        Exception handling preserves root-cause visibility across the dual-failure path:
        if :meth:`drain_buffer` raises, the exception is captured and
        :meth:`~sovereign_edge.buffer.OffGridBuffer.close` is still called to guarantee
        background thread teardown.  If :meth:`~sovereign_edge.buffer.OffGridBuffer.close`
        also raises (e.g., un-journaled write errors remain after the drain attempt), the
        buffer exception is chained from the drain exception via ``raise buf_exc from
        drain_exc`` so the original root cause is preserved in the traceback.  If only
        one of the two raises, that exception propagates normally.

        The pipeline does not own the ledger lifecycle; the caller remains responsible
        for invoking :meth:`~sovereign_ledger.SovereignLedger.close` on the ledger
        instance after calling this method.

        :return: None
        :rtype: None
        :raises RuntimeError: If :meth:`drain_buffer` could not re-queue one or more
            entries, or if :meth:`~sovereign_edge.buffer.OffGridBuffer.close` detects
            un-journaled write errors after the drain attempt.  When both raise, the
            buffer :exc:`RuntimeError` is chained from the drain :exc:`RuntimeError`
            to preserve the root-cause traceback.
        """
        drain_exc: Exception | None = None
        try:
            self.drain_buffer()
        except Exception as exc:
            drain_exc = exc
        try:
            self._buffer.close()
        except RuntimeError as buf_exc:
            if drain_exc is not None:
                raise buf_exc from drain_exc
            raise
        if drain_exc is not None:
            raise drain_exc

    @property
    def buffer_depth(self) -> int:
        """Current count of receipts queued in the off-grid buffer.

        :return: Number of buffered receipt entries awaiting ledger submission.
        :rtype: int
        """
        return self._buffer.size
