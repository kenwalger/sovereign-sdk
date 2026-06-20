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

    :param path: Filesystem path to the JSONL buffer file.  The file is created on the
        first background write if it does not already exist.
    :type path: str
    """

    def __init__(self, path: str) -> None:
        self._path: Path = Path(path)
        self._write_queue: _queue.Queue[str | None] = _queue.Queue()
        self._pending: int = 0
        self._committed: int = 0
        self._write_errors: list[tuple[dict[str, Any], str]] = []
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
        discarding the receipt.

        :return: None
        :rtype: None
        """
        while True:
            entry: str | None = self._write_queue.get()
            stop: bool = entry is None
            written: bool = False
            error_entry: tuple[dict[str, Any], str] | None = None
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
                        pass
            finally:
                with self._count_lock:
                    self._pending -= 1
                    if written:
                        self._committed += 1
                    elif error_entry is not None:
                        self._write_errors.append(error_entry)
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
        :meth:`queue.Queue.put` to prevent a concurrent :meth:`drain` from sweeping the
        file between the enqueue and the background write, which would silently discard
        the entry.

        :param receipt: A :class:`~sovereign_core.crypto.ForensicReceipt`-compatible
            dict to queue for later ledger submission.
        :type receipt: dict[str, Any]
        :param sieved_content: The Prose-Tax-minimized string payload associated with
            ``receipt``.
        :type sieved_content: str
        """
        entry: str = json.dumps(
            {"receipt": receipt, "sieved_content": sieved_content},
            sort_keys=True,
            ensure_ascii=False,
        )
        with self._drain_lock:
            with self._count_lock:
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
        promotion succeeds, ``_committed`` is decremented by the number of file entries
        drained and ``_write_errors`` is cleared; write-error entries never accumulated
        in ``_committed`` so they need no counter adjustment.  If the promotion fails, an
        empty list is returned and ``_write_errors`` is left intact for the next drain
        pass so that no entry is permanently discarded.  Malformed JSON lines are
        silently skipped.  Returns an empty list when the buffer file does not exist and
        no write-error entries are pending.

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
                    self._write_errors.clear()
                pending_error_entries.sort(key=_seq_key)
                return pending_error_entries

            try:
                raw_lines: list[str] = self._path.read_text(encoding="utf-8").splitlines()
            except OSError:
                return []

            entries: list[tuple[dict[str, Any], str]] = []
            for line in raw_lines:
                stripped: str = line.strip()
                if not stripped:
                    continue
                try:
                    obj: dict[str, Any] = json.loads(stripped)
                    entries.append((obj["receipt"], obj["sieved_content"]))
                except (json.JSONDecodeError, KeyError):
                    continue

            entries.extend(pending_error_entries)
            entries.sort(key=_seq_key)

            file_entry_count: int = len(entries) - len(pending_error_entries)

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

        Sends the ``None`` sentinel through the write queue.  The worker processes
        all prior entries before it encounters the sentinel, then exits.

        :return: None
        :rtype: None
        """
        with self._count_lock:
            self._pending += 1
        self._write_queue.put(None)
        self._worker_thread.join()

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
