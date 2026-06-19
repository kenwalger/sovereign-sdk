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
      ``fsync``; reset to zero by :meth:`drain` after the atomic file replace succeeds.

    :attr:`size` returns ``_pending + _committed`` without reading the file, so no
    transient queue state can cause an item to be counted twice or omitted.

    :meth:`drain` calls :meth:`flush` before reading the file, sorts entries in ascending
    order by ``receipt["metadata"]["sequence"]`` to preserve the ledger's linear hash
    chain, and atomically clears the buffer via a ``tempfile`` → ``os.replace`` promotion.

    :param path: Filesystem path to the JSONL buffer file.  The file is created on the
        first background write if it does not already exist.
    :type path: str
    """

    def __init__(self, path: str) -> None:
        self._path: Path = Path(path)
        self._write_queue: _queue.Queue[str | None] = _queue.Queue()
        self._pending: int = 0
        self._committed: int = 0
        self._count_lock: threading.Lock = threading.Lock()
        self._worker_thread: threading.Thread = threading.Thread(
            target=self._disk_writer,
            daemon=True,
            name="sovereign-edge-buffer-writer",
        )
        self._worker_thread.start()

    def _disk_writer(self) -> None:
        """Background daemon worker that serializes JSONL writes to disk.

        :return: None
        :rtype: None
        """
        while True:
            entry: str | None = self._write_queue.get()
            stop: bool = entry is None
            written: bool = False
            try:
                if not stop:
                    with open(self._path, "a", encoding="utf-8") as fh:
                        fh.write(entry + "\n")
                        fh.flush()
                        os.fsync(fh.fileno())
                    written = True
            except OSError:
                pass
            finally:
                with self._count_lock:
                    self._pending -= 1
                    if written:
                        self._committed += 1
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

        Calls :meth:`flush` to ensure all in-flight background writes are committed
        before reading the file.  Entries are sorted in ascending order by
        ``receipt["metadata"]["sequence"]`` to protect the ledger's linear hash chain
        from out-of-order replay when entries arrive from concurrent push paths.
        The buffer file is then replaced with an empty staging file via
        ``tempfile`` → ``os.replace`` to close the double-replay window, and
        ``_committed`` is reset to zero under the count lock so :attr:`size` reflects
        the cleared state immediately.  Malformed JSON lines are silently skipped.
        Returns an empty list when the buffer file does not exist.

        :return: List of ``(receipt_dict, sieved_content)`` tuples sorted by sequence.
        :rtype: list[tuple[dict[str, Any], str]]
        """
        self.flush()

        if not self._path.exists():
            return []

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

        entries.sort(
            key=lambda e: int(e[0].get("metadata", {}).get("sequence", 0))
        )

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
                self._committed = 0

        return entries

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
        """Current count of buffered entries, including in-flight writes.

        Returns ``_pending + _committed`` under the count lock — no file read is
        performed, so no background-worker transition can cause an item to be counted
        in both the pending snapshot and the on-disk line count simultaneously.
        ``_pending`` covers items enqueued but not yet fsync'd; ``_committed`` covers
        items fsync'd but not yet drained.

        :return: Total number of buffered receipt entries awaiting drain.
        :rtype: int
        """
        with self._count_lock:
            return self._pending + self._committed
