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
    """Raised when signed receipts cannot reach either the ledger or the off-grid buffer.

    Encapsulates a total persistence failure across two usage contexts:

    * **Single-receipt fault** (raised from :meth:`EdgePipeline.process`): the ledger
      was unreachable (raising :exc:`~sovereign_ledger.SovereignStorageError` or
      ``sqlite3.Error``) and the off-grid buffer push also failed.  The fully signed
      :class:`~sovereign_core.crypto.ForensicReceipt` dict is attached via :attr:`receipt`
      so the host application can route it through an alternative channel.

    * **Batch-receipt fault** (raised from :meth:`EdgePipeline.drain_buffer`): the
      ledger replay loop crashed via an unhandled exception AND one or more entries
      could not be re-queued to the buffer (e.g. the buffer worker has terminated).
      Every unrecoverable receipt dict is collected in :attr:`uncommitted_receipts`
      so the host can perform out-of-band recovery rather than silently losing them.

    :param args: Positional message arguments forwarded to :class:`RuntimeError`.
    :param receipt: The fully signed ForensicReceipt dict involved in a single-receipt
        fault.  ``None`` when the exception originates from a batch-drain double fault.
    :type receipt: dict[str, Any] | None
    :param uncommitted_receipts: List of ForensicReceipt dicts that could not be
        persisted or re-queued during a :meth:`~EdgePipeline.drain_buffer` replay pass.
        ``None`` when the exception originates from a single-receipt :meth:`process` fault.
    :type uncommitted_receipts: list[dict[str, Any]] | None
    :param ledger_error: The original exception (root cause of the fallback sequence)
        that triggered the buffer push attempt or crashed the replay loop.  Preserved as
        a named attribute so diagnostic code can inspect the full two-tier failure without
        relying on implicit ``__context__`` suppression.
    :type ledger_error: Exception
    """

    def __init__(
        self,
        *args: object,
        receipt: dict[str, Any] | None = None,
        uncommitted_receipts: list[dict[str, Any]] | None = None,
        ledger_error: Exception,
    ) -> None:
        super().__init__(*args)
        self.receipt: dict[str, Any] | None = receipt
        self.uncommitted_receipts: list[dict[str, Any]] | None = uncommitted_receipts
        self.ledger_error: Exception = ledger_error


class SovereignConfigurationError(ValueError):
    """Raised when :class:`EdgePipeline` is constructed with an insecure or contradictory configuration.

    Signals that the caller attempted to initialize the pipeline without a ``sensor_secret``
    while ``allow_unauthenticated`` was left at its secure default of ``False``.  To run an
    unauthenticated pipeline explicitly pass ``allow_unauthenticated=True``.

    Inherits from :exc:`ValueError` so callers that already catch :exc:`ValueError` from
    the construction phase continue to handle this case without modification.
    """


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
        ``str`` (UTF-8 encoded on assignment) or raw ``bytes``.  Omitting this parameter
        or passing an empty value requires ``allow_unauthenticated=True``; otherwise
        :exc:`SovereignConfigurationError` is raised immediately at construction time.
    :type sensor_secret: str | bytes
    :param allow_unauthenticated: Explicit opt-out of the mandatory secret requirement.
        When ``True``, constructing an :class:`EdgePipeline` without a ``sensor_secret``
        is permitted and inbound frame verification is disabled.  Defaults to ``False``
        to enforce secure-by-default initialization.
    :type allow_unauthenticated: bool
    :raises SovereignConfigurationError: If ``sensor_secret`` is empty or ``None`` and
        ``allow_unauthenticated`` is ``False``.  This validation fires before
        :class:`OffGridBuffer` is constructed so no ``.lock`` file is written when the
        exception propagates.
    :raises BaseException: If any exception is raised after :class:`OffGridBuffer` is
        constructed but before ``__init__`` completes (e.g., key directory creation,
        ``chmod``, or :class:`~sovereign_core.crypto.SovereignKeyManager` initialization),
        :meth:`~sovereign_edge.buffer.OffGridBuffer.close` is called unconditionally to
        terminate the background writer thread and release the ``.lock`` file before the
        exception propagates.  This prevents a stranded daemon thread and an orphaned
        lock from blocking any subsequent construction attempt on the same buffer path.
    """

    def __init__(
        self,
        ledger: SovereignLedger,
        signing_key: str = ".keys/edge_identity.pem",
        buffer_path: str = ".edge_buffer.jsonl",
        sensor_secret: str | bytes = b"",
        allow_unauthenticated: bool = False,
    ) -> None:
        _sensor_secret: bytes = (
            sensor_secret.encode("utf-8") if isinstance(sensor_secret, str) else sensor_secret
        )
        if not _sensor_secret and not allow_unauthenticated:
            raise SovereignConfigurationError(
                "EdgePipeline requires a non-empty sensor_secret for HMAC-SHA256 inbound "
                "frame verification.  To deliberately run without authentication pass "
                "allow_unauthenticated=True."
            )
        self._ledger: SovereignLedger = ledger
        self._buffer: OffGridBuffer = OffGridBuffer(buffer_path)
        self._retry_path: Path = Path(buffer_path + ".retry")
        try:
            key_path: Path = Path(signing_key).resolve()
            key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            key_path.parent.chmod(0o700)
            self._key_manager: SovereignKeyManager = SovereignKeyManager(key_dir=key_path.parent)
            self._key_manager.private_key_path = key_path
            self._key_manager.public_key_path = key_path.with_suffix(".pub")
            self._sensor_secret: bytes = _sensor_secret
        except BaseException:
            try:
                self._buffer.close()
            except Exception:
                pass
            raise

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
           :meth:`~sovereign_ledger.SovereignLedger.append_receipt`.  On any
           :exc:`Exception` (including :exc:`~sovereign_ledger.SovereignStorageError`,
           ``sqlite3.Error``, and application-level validation faults such as
           :exc:`ValueError`), the receipt is written to the off-grid buffer and
           ``buffered=True`` is set in the returned :class:`EdgeResult`.  The only
           exception not routed to the buffer is ``sqlite3.IntegrityError``, which
           signals a duplicate ``payload_hash`` and is silently evicted (see below).

        HMAC-SHA256 preimage canonicalization: the ``d``-payload segment of the
        preimage is produced via :meth:`SensorFrame.text_content`, which applies
        float-to-int normalization and sort-keyed JSON serialization, guaranteeing
        that the edge-side digest string matches the sensor-side canonical form
        regardless of runtime numeric type variance (MicroPython ``1.0`` vs CPython
        ``1``).

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
        :raises SovereignDoubleFaultError: If ``append_receipt`` raises any
            :exc:`Exception` other than ``sqlite3.IntegrityError`` *and* the subsequent
            :meth:`~sovereign_edge.buffer.OffGridBuffer.push` also raises.  The signed
            receipt dict is attached to the exception via :attr:`~SovereignDoubleFaultError.receipt`.

        When the ledger raises ``sqlite3.IntegrityError`` (duplicate ``payload_hash``),
        the receipt is silently evicted — ``buffered`` is set to ``False`` and the receipt
        is not routed to the off-grid buffer.  This matches the silent-eviction contract
        used during :meth:`drain_buffer` replay loops, where an already-committed receipt
        triggers ``IntegrityError`` and must not be re-queued to prevent infinite replay.
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
            canonical: str = frame.text_content()
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
        except Exception:
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
        except sqlite3.IntegrityError:
            payload_hash = receipt_dict["payload_hash"]
        except Exception as ledger_err:
            try:
                self._buffer.push(receipt_dict, sieve_result.text)
                payload_hash = receipt_dict["payload_hash"]
                buffered = True
            except Exception as push_err:
                raise SovereignDoubleFaultError(
                    "Ledger unavailable and off-grid buffer rejected payload; "
                    "the signed receipt is attached to this exception for host-level recovery",
                    receipt=receipt_dict,
                    ledger_error=ledger_err,
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
        advanced.  Each entry is dispatched through a three-tier inner exception
        hierarchy: ``sqlite3.IntegrityError`` → silent duplicate eviction;
        :exc:`~sovereign_ledger.SovereignStorageError` / ``sqlite3.Error`` /
        :exc:`ValueError` / :exc:`TypeError` → transient or format fault, entry
        re-queued to match the direct ingestion buffering profile; all other
        :exc:`Exception` subclasses → unhandled, propagate to the outer
        ``except`` block which aborts the replay loop.  Treating
        :exc:`ValueError` and :exc:`TypeError` as retryable prevents accidental
        permanent eviction when transient processing faults overlap with generic
        exception types, while still allowing operational failures such as
        :exc:`RuntimeError` to abort the replay and preserve the staging file for
        manual recovery.  If an unexpected exception escapes the per-entry
        handlers entirely and aborts the replay loop mid-iteration, the outer
        ``except`` branch appends every un-processed entry
        (``drained[processed:]``) to the requeue list before the exception is
        re-raised, closing the data-loss window where those entries would
        otherwise be held only in local scope.

        Successfully committed entries are returned as ``payload_hash`` strings.
        Any entry that fails due to a transient :exc:`~sovereign_ledger.SovereignStorageError`
        or ``sqlite3.Error`` is re-queued to the buffer so that no receipt is
        discarded on a recoverable storage fault.

        **Crash-recovery deduplication**: entries that were already committed to the
        ledger before a prior crash (e.g., the first entry in a mid-replay abort) will
        trigger ``sqlite3.IntegrityError`` on re-submission because their
        ``payload_hash`` already occupies a ``UNIQUE`` ledger slot.  The inner replay
        loop catches ``sqlite3.IntegrityError`` explicitly — before the broader
        ``sqlite3.Error`` guard — and silently evicts the duplicate without re-queuing
        it, preventing infinite replay loops where an already-persisted receipt is
        perpetually re-submitted on every drain-buffer invocation.  After a
        crash-restart cycle where :meth:`~sovereign_edge.buffer.OffGridBuffer._recover_staging`
        merges a staging file back into the active buffer, this eviction mechanism
        guarantees that each receipt is committed to the ledger exactly once regardless
        of how many times it appears in the merged active file.

        A ``try/finally`` block guarantees that ``drained[processed:]`` — the exact
        slice of entries that had not yet been resolved when an exception aborted the
        loop — is always appended to the re-queue list before control leaves the loop
        body.  This eliminates the data-loss window where those entries were held only
        in local scope with no recovery path.

        The re-queue pass always iterates to completion regardless of
        :exc:`RuntimeError` from individual :meth:`~sovereign_edge.buffer.OffGridBuffer.push`
        calls so that every remaining item is attempted; failed items are collected in a
        local list rather than just counted.  Each push failure additionally appends the
        entry as a JSONL line to ``{buffer_path}.retry`` (``self._retry_path``) so the
        receipt is durable even when the buffer worker has terminated; :exc:`OSError` from
        the retry-file write is silently swallowed to avoid masking the primary failure
        signal.  If the replay loop crashed AND some entries could not be re-queued,
        :exc:`SovereignDoubleFaultError` is raised with the unrecoverable receipt dicts
        attached via :attr:`~SovereignDoubleFaultError.uncommitted_receipts` and the crash
        exception preserved as :attr:`~SovereignDoubleFaultError.ledger_error`.

        Two-phase commit: :meth:`~sovereign_edge.buffer.OffGridBuffer.drain` atomically
        renames the active buffer to a staging file and returns all entries.  The staging
        file is preserved on disk until this method confirms that every entry is either
        committed to the ledger or re-queued.  After the re-queue pass completes,
        :meth:`~sovereign_edge.buffer.OffGridBuffer.flush` is called to block until every
        re-queued entry has been fsync'd to the active buffer file by the background
        writer thread.  This guarantees that no re-queued receipt exists only in the
        in-memory queue at the point when :meth:`~sovereign_edge.buffer.OffGridBuffer.commit_drain`
        deletes the staging file — the staging file is never removed while its counterpart
        re-queued entries are still pending a durable write.  Only when neither the replay
        loop nor the re-queue pass raises does this method invoke
        :meth:`~sovereign_edge.buffer.OffGridBuffer.commit_drain` to delete the staging
        file.  If any exception is raised (crash, push failure, OSError) the staging file
        survives, providing a byte-exact recovery artefact for the next
        :meth:`drain_buffer` pass or the next process boot via
        :meth:`~sovereign_edge.buffer.OffGridBuffer._recover_staging`.

        :return: ``payload_hash`` strings for every receipt successfully committed to
            the ledger on this drain pass.  Entries that could not be committed are
            re-queued and excluded from the returned list.
        :rtype: list[str]
        :raises RuntimeError: If the off-grid buffer file raises :exc:`OSError` on its
            read or atomic rotation (chained from the :exc:`OSError`), or if one or more
            entries cannot be re-queued after a ledger failure without a concurrent replay
            crash.  The exception is raised only after all requeue items have been attempted.
        :raises SovereignDoubleFaultError: If the replay loop is interrupted by an
            unhandled exception AND one or more entries cannot be re-queued to the buffer
            (i.e., both the ledger replay path and the buffer recovery path fail
            simultaneously).  The unrecoverable receipt dicts are attached via
            :attr:`~SovereignDoubleFaultError.uncommitted_receipts`; the replay exception
            is preserved as :attr:`~SovereignDoubleFaultError.ledger_error`.
        """
        committed: list[str] = []
        requeue: list[tuple[dict[str, Any], str]] = []

        try:
            drained: list[tuple[dict[str, Any], str]] = list(self._buffer.drain())
        except OSError as drain_err:
            raise RuntimeError(
                "drain_buffer() cannot replay buffered receipts: the off-grid buffer file "
                "operation failed — verify that the storage tier is accessible"
            ) from drain_err
        crash_exc: Exception | None = None
        processed: int = 0

        try:
            for receipt_dict, sieved_content in drained:
                try:
                    payload_hash: str = self._ledger.append_receipt(receipt_dict, sieved_content)
                    committed.append(payload_hash)
                except sqlite3.IntegrityError:
                    pass
                except (SovereignStorageError, sqlite3.Error, ValueError, TypeError):
                    requeue.append((receipt_dict, sieved_content))
                processed += 1
        except Exception as exc:
            crash_exc = exc
        finally:
            requeue.extend(drained[processed:])

        failed_requeue_entries: list[tuple[dict[str, Any], str]] = []
        push_failure_count: int = 0
        for receipt_dict, sieved_content in requeue:
            try:
                self._buffer.push(receipt_dict, sieved_content)
            except RuntimeError:
                push_failure_count += 1
                failed_requeue_entries.append((receipt_dict, sieved_content))
                try:
                    with open(self._retry_path, "a", encoding="utf-8") as _rf:
                        _rf.write(
                            json.dumps(
                                {"receipt": receipt_dict, "sieved_content": sieved_content},
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        _rf.flush()
                except OSError:
                    pass

        if crash_exc is not None:
            if push_failure_count:
                raise SovereignDoubleFaultError(
                    f"drain_buffer() suffered a cascading failure: the ledger replay loop "
                    f"raised and {push_failure_count} "
                    f"receipt{'s' if push_failure_count != 1 else ''} could not be re-queued "
                    f"to the off-grid buffer; unrecoverable receipts are attached via "
                    f"uncommitted_receipts for host-level recovery",
                    uncommitted_receipts=[r for r, _ in failed_requeue_entries],
                    ledger_error=crash_exc,
                ) from crash_exc
            raise crash_exc

        if push_failure_count:
            raise RuntimeError(
                f"drain_buffer() could not re-queue {push_failure_count} "
                f"receipt{'s' if push_failure_count != 1 else ''} after ledger failure: "
                "buffer is closed or its background writer has terminated; "
                "call drain() on the buffer to recover pending entries"
            )

        self._buffer.flush()
        self._buffer.commit_drain()
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
