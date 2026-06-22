# packages/sovereign-edge/src/sovereign_edge/buffer.py
"""Off-grid JSONL receipt buffer for sovereign-edge."""
import json
import os
import queue as _queue
import tempfile
import threading
from pathlib import Path
from typing import Any


class OffGridBuffer:
    """Durable JSONL-backed queue that absorbs ForensicReceipt payloads when the ledger
    is temporarily unreachable.

    Each entry is serialized as a single JSON line containing the receipt dict and the
    associated sieved content string.  Writes are dispatched to a daemon background
    thread so that :meth:`push` never blocks the calling thread on disk-bound I/O.

    Size accounting uses two counters updated atomically under a single lock in the
    worker's ``finally`` block, eliminating the double-count race that arises when a
    file-read-based approach snapshots ``_in_flight`` under the lock and then reads the
    file outside it:

    - ``_pending``: incremented in :meth:`push` before the queue put; decremented in the
      worker ``finally`` — covers items waiting in the queue *and* items currently being
      written to disk.
    - ``_committed``: incremented in the worker ``finally`` only after a successful
      ``fsync``; decremented by :meth:`drain` by exactly the number of file entries
      removed from disk after the atomic replace succeeds.
    - ``_write_errors``: a list of ``(receipt_dict, sieved_content)`` tuples preserved
      under the count lock when a background write raises :exc:`OSError`.  These entries
      never reached disk and are surfaced by :meth:`drain` so that no receipt is silently
      discarded on a full disk or read-only filesystem.
    - ``_dead_letter``: a list of raw JSON strings quarantined under the count lock when
      a write-failed entry cannot be deserialized (``json.JSONDecodeError`` or ``KeyError``
      in the background worker), or when a disk line cannot be mapped to the canonical
      ``(receipt_dict, sieved_content)`` tuple during :meth:`drain`.  These strings are
      structurally unrecoverable as typed receipt pairs but are retained for out-of-band
      inspection; :attr:`dead_letter_count` exposes the accumulated count.

    A ``_drain_lock`` provides mutual exclusion between :meth:`push` and the entire
    :meth:`drain` critical section (flush → read → atomic replace).  :meth:`push` holds
    the lock for the duration of the ``_pending`` increment and :meth:`queue.Queue.put`,
    ensuring no new item can be enqueued while :meth:`drain` holds the file open.
    The background worker never acquires ``_drain_lock``, so no deadlock is possible.

    :attr:`size` returns ``_pending + _committed + len(_write_errors)`` without reading
    the file, so no transient queue state can cause an item to be counted twice or omitted,
    and disk write failures remain visible in the depth counter.

    :meth:`drain` calls :meth:`flush` before reading the file, merges any pending write-
    error entries with the on-disk entries, sorts the combined list in ascending order by
    ``receipt["metadata"]["sequence"]`` to preserve the ledger's linear hash chain, and
    atomically clears the buffer via a ``tempfile`` → ``os.replace`` promotion.  If the
    atomic promotion fails, :meth:`drain` returns an empty list so that no downstream
    ledger commits are made against an uncleared buffer; write-error entries are retained
    in ``_write_errors`` for the next drain pass.

    The parent directory of the buffer file is created (with all intermediate components)
    at construction time via :meth:`pathlib.Path.mkdir` so that both the background
    append path and the ``tempfile`` → ``os.replace`` atomic swap in :meth:`drain` are
    guaranteed a valid directory regardless of how deeply nested the supplied path is.

    :param path: Filesystem path to the JSONL buffer file.  The parent directory is
        created at construction time if it does not already exist.
    :type path: str
    """

    def __init__(self, path: str) -> None:
        self._path: Path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._write_queue: _queue.Queue[str | None] = _queue.Queue()
        self._pending: int = 0
        self._committed: int = 0
        self._write_errors: list[tuple[dict[str, Any], str]] = []
        self._dead_letter: list[str] = []
        self._closed: bool = False
        self._count_lock: threading.Lock = threading.Lock()
        self._drain_lock: threading.Lock = threading.Lock()
        self._worker_thread: threading.Thread = threading.Thread(
            target=self._disk_writer,
            daemon=True,
            name="sovereign-edge-buffer-writer",
        )
        self._worker_thread.start()

    def _disk_writer(self) -> None:
        """Background daemon worker that serializes JSONL writes to disk.

        When a write or ``fsync`` raises :exc:`OSError`, the serialized entry is
        deserialized and appended to ``_write_errors`` under the count lock so that
        :meth:`drain` can surface and recover it on the next pass instead of silently
        discarding the receipt.  If the entry cannot be deserialized due to
        :exc:`json.JSONDecodeError` or :exc:`KeyError`, the raw string is quarantined in
        ``_dead_letter`` under the count lock so that the corrupt payload is not silently
        discarded and remains available for out-of-band inspection.

        :return: None
        :rtype: None
        """
        while True:
            entry: str | None = self._write_queue.get()
            stop: bool = entry is None
            written: bool = False
            error_entry: tuple[dict[str, Any], str] | None = None
            dead_letter_entry: str | None = None
            try:
                if not stop:
                    with open(self._path, "a", encoding="utf-8") as fh:
                        fh.write(entry + "\n")
                        fh.flush()
                        os.fsync(fh.fileno())
                    written = True
            except OSError:
                if entry is not None:
                    try:
                        _obj: dict[str, Any] = json.loads(entry)
                        error_entry = (_obj["receipt"], _obj["sieved_content"])
                    except (json.JSONDecodeError, KeyError):
                        dead_letter_entry = entry
            finally:
                with self._count_lock:
                    self._pending -= 1
                    if written:
                        self._committed += 1
                    elif error_entry is not None:
                        self._write_errors.append(error_entry)
                    elif dead_letter_entry is not None:
                        self._dead_letter.append(dead_letter_entry)
                self._write_queue.task_done()
            if stop:
                return

    def push(self, receipt: dict[str, Any], sieved_content: str) -> None:
        """Enqueue a receipt entry for asynchronous JSONL persistence.

        Returns immediately after enqueueing — the background daemon thread performs
        the actual disk write and ``fsync`` without blocking the caller.  The entry is
        reflected in :attr:`size` immediately via ``_pending`` so that
        :attr:`~sovereign_edge.pipeline.EdgePipeline.buffer_depth` is accurate without
        requiring a :meth:`flush` call.

        Acquires ``_drain_lock`` for the duration of the ``_pending`` increment and
        :meth:`queue.Queue.put` to protect counter modifications and prevent a concurrent
        :meth:`drain` from sweeping the file between the enqueue and the background write,
        which would silently discard the entry.  Under active, heavy :meth:`drain`
        playback operations — where :meth:`drain` holds ``_drain_lock`` across the full
        flush → read → atomic-replace critical section — :meth:`push` callers will
        experience brief lock contention and block until the active drain batch
        transaction yields the lock.

        Raises :exc:`RuntimeError` immediately if :meth:`close` has already been called.
        After :meth:`close` the background worker thread has exited and the internal queue
        has no consumer; enqueueing would increment ``_pending`` and place an entry into a
        dead queue that never calls ``task_done()``, causing any subsequent :meth:`flush`
        call to block indefinitely.

        :param receipt: A :class:`~sovereign_core.crypto.ForensicReceipt`-compatible
            dict to queue for later ledger submission.
        :type receipt: dict[str, Any]
        :param sieved_content: The Prose-Tax-minimized string payload associated with
            ``receipt``.
        :type sieved_content: str
        :raises RuntimeError: If the buffer has been closed via :meth:`close`.
        """
        entry: str = json.dumps(
            {"receipt": receipt, "sieved_content": sieved_content},
            sort_keys=True,
            ensure_ascii=False,
        )
        with self._drain_lock:
            with self._count_lock:
                if self._closed:
                    raise RuntimeError(
                        "OffGridBuffer is closed; push() cannot enqueue new entries "
                        "after close() has been called"
                    )
                self._pending += 1
            self._write_queue.put(entry)

    def flush(self) -> None:
        """Block until all enqueued entries have been committed to disk.

        Uses :meth:`queue.Queue.join` to wait for the background worker to call
        ``task_done()`` on every pending item before returning.

        :return: None
        :rtype: None
        """
        self._write_queue.join()

    def drain(self) -> list[tuple[dict[str, Any], str]]:
        """Flush, read, sort, and atomically clear all buffered entries.

        Acquires ``_drain_lock`` for the entire critical section so that no concurrent
        :meth:`push` can enqueue to the background worker between :meth:`flush` and the
        atomic file promotion, preventing an in-flight write from landing in the file
        that is about to be replaced and being silently discarded.

        Calls :meth:`flush` to ensure all in-flight background writes are committed
        before reading the file.  Any entries preserved in ``_write_errors`` from prior
        background write failures are merged with the on-disk entries.  The combined list
        is sorted in ascending order by ``receipt["metadata"]["sequence"]`` to protect
        the ledger's linear hash chain from out-of-order replay when entries arrive from
        concurrent push paths.  The sort key is evaluated inside a guarded helper that
        catches :exc:`TypeError` and :exc:`ValueError` so a non-numeric sequence value
        in a corrupted entry falls back to ``0`` rather than aborting the entire drain
        pass.
        The buffer file is then replaced with an empty staging file via
        ``tempfile`` → ``os.replace`` to close the double-replay window.  If the atomic
        promotion succeeds, ``_committed`` is decremented by the total count of non-blank
        disk lines (valid entries plus dead-letter lines) so that every byte footprint
        removed from disk is reflected in the counter — including lines whose payload was
        successfully fsync'd (incrementing ``_committed``) but subsequently corrupted on
        disk; basing the decrement on valid-only parse count would leave ``_committed``
        above zero for an empty file.  ``_write_errors`` is also cleared on success;
        write-error entries never accumulated in ``_committed`` so they need no counter
        adjustment.  If the promotion fails, an empty list is returned and ``_write_errors``
        is left intact for the next drain pass so that no entry is permanently discarded.
        Disk lines that cannot be parsed as valid JSON or that are missing the
        ``receipt`` / ``sieved_content`` keys are quarantined in ``_dead_letter`` under
        the count lock rather than silently dropped; :attr:`dead_letter_count` reflects
        the accumulated quarantine count.  Returns an empty list when the buffer file does
        not exist and no write-error entries are pending.

        :return: List of ``(receipt_dict, sieved_content)`` tuples sorted by sequence,
            or an empty list if the buffer file could not be atomically cleared.
        :rtype: list[tuple[dict[str, Any], str]]
        """
        with self._drain_lock:
            self.flush()

            def _seq_key(entry: tuple[dict[str, Any], str]) -> int:
                try:
                    return int(entry[0].get("metadata", {}).get("sequence", 0))
                except (TypeError, ValueError):
                    return 0

            with self._count_lock:
                pending_error_entries: list[tuple[dict[str, Any], str]] = list(self._write_errors)

            if not self._path.exists():
                with self._count_lock:
                    self._committed = 0
                    self._write_errors.clear()
                pending_error_entries.sort(key=_seq_key)
                return pending_error_entries

            try:
                raw_lines: list[str] = self._path.read_text(encoding="utf-8").splitlines()
            except OSError:
                return []

            entries: list[tuple[dict[str, Any], str]] = []
            disk_line_count: int = 0
            for line in raw_lines:
                stripped: str = line.strip()
                if not stripped:
                    continue
                disk_line_count += 1
                try:
                    obj: dict[str, Any] = json.loads(stripped)
                    entries.append((obj["receipt"], obj["sieved_content"]))
                except (json.JSONDecodeError, KeyError):
                    with self._count_lock:
                        self._dead_letter.append(stripped)
                    continue

            entries.extend(pending_error_entries)
            entries.sort(key=_seq_key)

            file_entry_count: int = disk_line_count

            tmp_path: str = ""
            replaced: bool = False
            try:
                with tempfile.NamedTemporaryFile(
                    dir=self._path.parent,
                    delete=False,
                    suffix=".tmp",
                    mode="w",
                    encoding="utf-8",
                ) as tmp_fh:
                    tmp_path = tmp_fh.name
                os.replace(tmp_path, self._path)
                tmp_path = ""
                replaced = True
            except OSError:
                if tmp_path:
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

            if replaced:
                with self._count_lock:
                    self._committed = max(0, self._committed - file_entry_count)
                    self._write_errors.clear()

            return entries if replaced else []

    def close(self) -> None:
        """Flush all pending writes and terminate the background worker thread.

        Sets ``_closed = True`` and increments ``_pending`` atomically under the count
        lock before dispatching the ``None`` sentinel.  Setting the flag inside the lock
        guarantees that any concurrent :meth:`push` call that has not yet entered its own
        ``_count_lock`` block will observe ``_closed = True`` and raise immediately rather
        than enqueuing an entry into a queue that has no living consumer.  Sends the
        ``None`` sentinel through the write queue.  The worker processes all prior entries
        before it encounters the sentinel, then exits.  After the worker terminates,
        ``_write_errors`` is inspected under the count lock; if any receipt entries that
        failed to reach disk are still unresolved, a :exc:`RuntimeError` is raised so the
        host application is forced to acknowledge the un-journaled receipts rather than
        allowing them to vaporize silently.  Callers must invoke :meth:`drain` before
        :meth:`close` whenever there is any possibility of prior disk write failures;
        :meth:`drain` surfaces and clears ``_write_errors`` so the subsequent :meth:`close`
        completes without error.

        :return: None
        :rtype: None
        :raises RuntimeError: If one or more receipt entries are preserved in
            ``_write_errors`` at shutdown time, indicating that they failed to reach
            disk and have not been recovered via :meth:`drain`.
        """
        with self._count_lock:
            self._closed = True
            self._pending += 1
        self._write_queue.put(None)
        self._worker_thread.join()
        with self._count_lock:
            error_count: int = len(self._write_errors)
        if error_count:
            raise RuntimeError(
                f"OffGridBuffer closed with {error_count} un-journaled "
                f"receipt{'s' if error_count != 1 else ''} in _write_errors; "
                "call drain() before close() to recover pending entries"
            )

    @property
    def size(self) -> int:
        """Current count of buffered entries, including in-flight writes and write errors.

        Returns ``_pending + _committed + len(_write_errors)`` under the count lock — no
        file read is performed, so no background-worker transition can cause an item to be
        counted in both the pending snapshot and the on-disk line count simultaneously.
        ``_pending`` covers items enqueued but not yet fsync'd; ``_committed`` covers
        items fsync'd but not yet drained; ``_write_errors`` covers items whose disk write
        failed and that are awaiting recovery via :meth:`drain`.

        :return: Total number of buffered receipt entries awaiting drain.
        :rtype: int
        """
        with self._count_lock:
            return self._pending + self._committed + len(self._write_errors)

    @property
    def write_error_count(self) -> int:
        """Count of receipt entries preserved after background disk write failures.

        Entries in this count were never written to the JSONL file (due to
        :exc:`OSError` during the background write or ``fsync``) but are retained
        in memory and will be included in the next :meth:`drain` call.  A non-zero
        value indicates a disk condition (full disk, read-only filesystem) that
        prevented durable persistence.

        :return: Number of entries awaiting recovery via :meth:`drain`.
        :rtype: int
        """
        with self._count_lock:
            return len(self._write_errors)

    @property
    def dead_letter_count(self) -> int:
        """Count of raw strings quarantined after deserialization failures.

        Entries in this count are raw JSONL strings that could not be mapped to a
        valid ``(receipt_dict, sieved_content)`` pair — either because the background
        worker encountered a :exc:`json.JSONDecodeError` or :exc:`KeyError` while
        attempting to recover a write-failed entry, or because a disk line in
        :meth:`drain` was structurally malformed.  These strings are not recoverable
        as typed receipt data but are retained in ``_dead_letter`` for out-of-band
        inspection.

        :return: Number of quarantined raw strings accumulated since construction.
        :rtype: int
        """
        with self._count_lock:
            return len(self._dead_letter)
