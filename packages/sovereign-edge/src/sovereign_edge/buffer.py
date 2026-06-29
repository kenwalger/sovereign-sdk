# packages/sovereign-edge/src/sovereign_edge/buffer.py
"""Off-grid JSONL receipt buffer for sovereign-edge."""
import json
import os
import queue as _queue
import sys
import tempfile
import threading
import uuid as _uuid
from pathlib import Path
from typing import Any

from sovereign_ledger import SovereignStorageError

_DEAD_LETTER_MAX: int = 100


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
      inspection; :attr:`dead_letter_count` exposes the accumulated count.  The list is
      capped at ``_DEAD_LETTER_MAX`` (100) entries: when the ceiling is reached, the oldest
      entry is evicted before the new one is appended, bounding memory consumption when a
      rogue sensor continuously floods the buffer with malformed payloads.

    A ``_drain_lock`` provides mutual exclusion across three operations: :meth:`push`
    (``_pending`` increment + :meth:`queue.Queue.put`), the entire :meth:`drain` critical
    section (flush → read → atomic replace), and the sentinel placement in :meth:`close`
    (``_closed = True`` + ``queue.put(None)``).  Holding the same lock for both the
    ``push()`` enqueue window and the ``close()`` sentinel placement guarantees that the
    sentinel is always the last item in the queue: a ``push()`` in progress when ``close()``
    is called either completes its :meth:`queue.Queue.put` before the sentinel is placed
    (because it already held ``_drain_lock``), or observes ``_closed = True`` after
    acquiring ``_drain_lock`` and raises without enqueuing.  The background worker never
    acquires ``_drain_lock``, and :meth:`close` calls :meth:`threading.Thread.join` outside
    the lock window, so no deadlock is possible.

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

    _instance_registry: set[str] = set()
    _instance_registry_lock: threading.Lock = threading.Lock()

    def __init__(self, path: str) -> None:
        self._instance_id: str = str(_uuid.uuid4())
        self._path: Path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._staging_path: Path = Path(str(path) + ".staging")
        self._quarantine_path: Path = Path(str(path) + ".quarantine")
        self._quarantine_staging_path: Path = Path(str(path) + ".quarantine.staging")
        self._lock_path: Path = Path(str(path) + ".lock")
        self._write_queue: _queue.Queue[str | None] = _queue.Queue()
        self._pending: int = 0
        self._committed: int = 0
        self._write_errors: list[tuple[dict[str, Any], str]] = []
        self._dead_letter: list[str] = []
        self._closed: bool = False
        self._worker_failed: bool = False
        self._worker_running: bool = True
        self._drain_read_failed: bool = False
        self._count_lock: threading.Lock = threading.Lock()
        self._drain_lock: threading.Lock = threading.Lock()
        self._drain_write_error_snapshot: int = 0
        self._acquire_buffer_lock()
        try:
            self._recover_staging()
        except BaseException:
            try:
                self._lock_path.unlink()
            except OSError:
                pass
            with OffGridBuffer._instance_registry_lock:
                OffGridBuffer._instance_registry.discard(self._instance_id)
            raise
        self._load_quarantine()
        if self._path.exists():
            try:
                self._committed = sum(
                    1
                    for _ln in self._path.read_text(encoding="utf-8").splitlines()
                    if _ln.strip()
                )
            except OSError:
                pass
        self._worker_thread: threading.Thread = threading.Thread(
            target=self._disk_writer,
            daemon=True,
            name="sovereign-edge-buffer-writer",
        )
        self._worker_thread.start()

    def _acquire_buffer_lock(self) -> None:
        """Acquire an exclusive instance lock for the buffer file path.

        Uses :func:`os.open` with ``O_CREAT | O_EXCL | O_WRONLY`` to atomically
        create the ``.lock`` file in a single kernel call, writing ``{pid}\\n{uuid}``
        into it.  The OS rejects a concurrent creation attempt from any other process
        or thread — two :class:`OffGridBuffer` instances racing on the same path
        cannot both receive a successful ``O_EXCL`` creation.

        If the lock file already exists, the owning PID and UUID are read and the
        PID is tested for liveness via :func:`os.kill` with signal ``0``.  A stale
        lock whose owner process no longer exists is overwritten.  If the owner PID
        is alive, the action taken depends strictly on whether the PID belongs to this
        process or an external one:

        * **Same process** (``held_pid == os.getpid()``): the UUID is cross-verified
          against ``_instance_registry``.  If the UUID is absent, the instance that
          wrote the lock has already exited without calling :meth:`close`; the lock is
          safely overtaken.  If the UUID is present (live sibling) or the lock file
          contains no UUID field, :exc:`RuntimeError` is raised.

        * **External process** (``held_pid != os.getpid()``): the lock is respected
          unconditionally.  :exc:`SovereignStorageError` is raised regardless of
          whether a UUID field is present, because the UUID registry is process-local
          and cannot speak to the liveness of instances in another process.  Overtaking
          an external process's lock would allow two writers on the same JSONL file,
          producing interleaved lines and corrupting the journal.

        Old-format lock files that contain only a PID (no ``\\n{uuid}`` second line)
        follow the same PID-origin rule: same-process triggers :exc:`RuntimeError`;
        external-process triggers :exc:`SovereignStorageError`.  The lock is released
        by :meth:`close`.

        :return: None
        :rtype: None
        :raises RuntimeError: If the buffer path is already held by a live
            :class:`OffGridBuffer` instance within this process.
        :raises SovereignStorageError: If the lock file cannot be created or read due
            to a :exc:`PermissionError` or unexpected :exc:`OSError`, or if the lock
            is held by a live external process.
        """
        _lock_payload: str = f"{os.getpid()}\n{self._instance_id}"
        try:
            _lock_fd: int = os.open(
                str(self._lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            try:
                os.write(_lock_fd, _lock_payload.encode("utf-8"))
            finally:
                os.close(_lock_fd)
            with OffGridBuffer._instance_registry_lock:
                OffGridBuffer._instance_registry.add(self._instance_id)
            return
        except FileExistsError:
            pass
        except OSError as exc:
            raise SovereignStorageError(
                "Lock file acquisition failed due to permission or system boundaries"
            ) from exc
        try:
            _lock_content: str = self._lock_path.read_text(encoding="utf-8").strip()
            _lock_parts: list[str] = _lock_content.split("\n", 1)
            held_pid: int = int(_lock_parts[0].strip())
            held_uuid: str = _lock_parts[1].strip() if len(_lock_parts) > 1 else ""
        except PermissionError as exc:
            raise SovereignStorageError(
                "Lock file acquisition failed due to permission or system boundaries"
            ) from exc
        except (OSError, ValueError):
            self._lock_path.write_text(_lock_payload, encoding="utf-8")
            with OffGridBuffer._instance_registry_lock:
                OffGridBuffer._instance_registry.add(self._instance_id)
            return
        try:
            os.kill(held_pid, 0)
        except ProcessLookupError:
            self._lock_path.write_text(_lock_payload, encoding="utf-8")
            with OffGridBuffer._instance_registry_lock:
                OffGridBuffer._instance_registry.add(self._instance_id)
            return
        except OSError as exc:
            raise SovereignStorageError(
                "Lock file acquisition failed due to permission or system boundaries"
            ) from exc
        if held_pid == os.getpid():
            if held_uuid:
                with OffGridBuffer._instance_registry_lock:
                    _uuid_is_ours: bool = held_uuid in OffGridBuffer._instance_registry
                if not _uuid_is_ours:
                    self._lock_path.write_text(_lock_payload, encoding="utf-8")
                    with OffGridBuffer._instance_registry_lock:
                        OffGridBuffer._instance_registry.add(self._instance_id)
                    return
            raise RuntimeError(
                f"OffGridBuffer path '{self._path}' is already held by process {held_pid}; "
                "each instance must use a distinct buffer_path"
            )
        raise SovereignStorageError(
            f"Lock file acquisition failed: buffer path '{self._path}' is already "
            f"held by external process {held_pid} and cannot be overtaken across "
            "process boundaries"
        )

    def _recover_staging(self) -> None:
        """Recover a staging file left by a prior drain cycle that did not complete.

        A ``.staging`` file is created by :meth:`drain` when the active buffer is
        atomically rotated out for replay.  Under normal operation, the calling
        pipeline deletes it via :meth:`commit_drain` after confirming all entries are
        committed.  If the process exits before :meth:`commit_drain` is called (power
        loss, SIGKILL, unhandled exception), the staging file survives on disk.

        This method restores the staged entries into the active buffer path before the
        background writer thread starts:

        * **Only staging exists** (active absent): ``os.replace`` promotes it atomically
          to the active path.
        * **Both exist**: staging content (older entries) is prepended to the active
          content (newer entries) via a temp-file atomic merge; the staging file is then
          unlinked.

        The staging file is always read first — even in the promote-only branch — to
        detect corruption at boot time.  If the read raises :exc:`OSError` or
        :exc:`UnicodeDecodeError`, the staging file is quarantined by renaming it to
        ``{staging_path}.corrupt`` and :exc:`~sovereign_ledger.SovereignStorageError`
        is raised immediately so the host process is notified before the background
        writer thread starts.  Silently skipping a corrupt read would allow an
        undetected data-loss condition to masquerade as a clean boot.  If the
        quarantine rename itself fails the original staging file is left in place;
        the :exc:`~sovereign_ledger.SovereignStorageError` is re-raised in either case.

        The same quarantine-and-raise pattern applies when :func:`os.replace` or the
        merge temp-file operations raise :exc:`OSError` after the staging content has
        been confirmed readable.

        :return: None
        :rtype: None
        :raises SovereignStorageError: If the staging file cannot be read due to
            :exc:`OSError` or :exc:`UnicodeDecodeError`, or if the active-file
            promotion or merge operation raises :exc:`OSError`.  The staging file is
            quarantined as ``{staging_path}.corrupt`` before raising in all cases.
            :meth:`__init__` catches any exception propagating from this method and
            unconditionally unlinks the ``.lock`` file before re-raising, so a
            subsequent construction attempt on the same path can acquire the lock and
            succeed once the quarantined staging file is resolved.
        """
        if not self._staging_path.exists():
            return
        corrupt_path: Path = Path(str(self._staging_path) + ".corrupt")
        try:
            staging_text: str = self._staging_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            try:
                os.replace(self._staging_path, corrupt_path)
            except OSError:
                pass
            raise SovereignStorageError(
                f"OffGridBuffer boot-time staging recovery failed: cannot read "
                f"'{self._staging_path}'; file quarantined as '{corrupt_path}'"
            ) from exc
        if not self._path.exists():
            try:
                os.replace(self._staging_path, self._path)
            except OSError as exc:
                try:
                    os.replace(self._staging_path, corrupt_path)
                except OSError:
                    pass
                raise SovereignStorageError(
                    f"OffGridBuffer boot-time staging recovery failed: cannot promote "
                    f"'{self._staging_path}' to '{self._path}'; "
                    f"file quarantined as '{corrupt_path}'"
                ) from exc
            return
        tmp_path: str = ""
        try:
            active_text: str = self._path.read_text(encoding="utf-8")
            if staging_text and not staging_text.endswith("\n"):
                staging_text += "\n"
            with tempfile.NamedTemporaryFile(
                dir=self._path.parent,
                delete=False,
                suffix=".tmp",
                mode="w",
                encoding="utf-8",
            ) as tmp_fh:
                tmp_path = tmp_fh.name
                tmp_fh.write(staging_text + active_text)
            os.replace(tmp_path, self._path)
            tmp_path = ""
            try:
                self._staging_path.unlink()
            except OSError:
                pass
        except OSError as exc:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            try:
                os.replace(self._staging_path, corrupt_path)
            except OSError:
                pass
            raise SovereignStorageError(
                f"OffGridBuffer boot-time staging recovery failed: merge operation "
                f"raised; staging file quarantined as '{corrupt_path}'"
            ) from exc

    def _load_quarantine(self) -> None:
        """Load write-error entries persisted to the quarantine file by a prior run.

        Called once during :meth:`__init__` after :meth:`_recover_staging` completes,
        before the background writer thread starts.  Entries in the quarantine file are
        those that raised :exc:`OSError` during a prior background write and were
        appended to ``{path}.quarantine`` for crash durability.  Loading them into
        ``_write_errors`` ensures they appear in the next :meth:`drain` call and are
        not silently abandoned across a process restart.

        Malformed quarantine lines that cannot be deserialized are quarantined in
        ``_dead_letter`` rather than silently dropped.  If the quarantine file itself
        cannot be read, this method returns without raising — the absence of recovered
        entries is safer than aborting construction for a non-critical recovery file.

        If ``{path}.quarantine.staging`` also exists (a snapshot rotated by :meth:`drain`
        during an interrupted drain cycle where :meth:`commit_drain` was never called),
        its entries are loaded into ``_write_errors`` as well.  The staging snapshot's
        content may already be present in the active buffer via :meth:`_recover_staging`,
        but loading it here too is safe: any resulting duplicates are silently evicted by
        the ``sqlite3.IntegrityError`` handler in :meth:`~sovereign_edge.pipeline.EdgePipeline.drain_buffer`.

        :return: None
        :rtype: None
        """
        for _qpath in (self._quarantine_path, self._quarantine_staging_path):
            if not _qpath.exists():
                continue
            try:
                _qlines: list[str] = _qpath.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for _qline in _qlines:
                _qs: str = _qline.strip()
                if not _qs:
                    continue
                try:
                    _obj: dict[str, Any] = json.loads(_qs)
                    self._write_errors.append((_obj["receipt"], _obj["sieved_content"]))
                except (json.JSONDecodeError, KeyError):
                    if len(self._dead_letter) >= _DEAD_LETTER_MAX:
                        del self._dead_letter[0]
                    self._dead_letter.append(_qs)

    def _disk_writer(self) -> None:
        """Background daemon worker that serializes JSONL writes to disk.

        When a write or ``fsync`` raises :exc:`OSError`, the serialized entry is first
        appended to the disk-backed quarantine file (``{path}.quarantine``) for crash
        durability, then deserialized and appended to ``_write_errors`` under the count
        lock so that :meth:`drain` can surface and recover it on the next pass.  If the
        quarantine write itself raises any :exc:`Exception` (a double write-fault: both
        the primary JSONL file and the quarantine file are unwritable), the raw entry
        string is written to :data:`sys.stderr` with an unbuffered flush so that a
        container or process supervisor can capture and recover the payload out-of-band;
        ``_worker_failed`` is then set to ``True`` via the local ``worker_failed`` flag
        so the thread halts after the current item's ``finally`` block completes and
        evacuates remaining queued items.  The entry is still appended to
        ``_write_errors`` even on a double-fault so that a :meth:`drain` call issued
        before the process exits can surface it.  If the entry cannot be deserialized
        due to :exc:`json.JSONDecodeError` or :exc:`KeyError`, the raw string is
        quarantined in ``_dead_letter`` rather than silently discarded.

        If an unexpected non-:exc:`OSError` exception is raised during a write, the thread
        sets ``_worker_failed`` under the count lock **before** any other state update in
        the ``finally`` block, then evacuates all remaining queue items under a single
        ``_count_lock`` acquisition that spans the entire :meth:`queue.Queue.get_nowait`
        drain loop — calling ``task_done()`` for each item within the same lock scope —
        without acquiring ``_drain_lock``.  Acquiring ``_drain_lock`` in the evacuation
        path would deadlock when :meth:`drain` holds it and is blocked in
        :meth:`queue.Queue.join` waiting for those same ``task_done()`` calls.  Holding
        ``_count_lock`` across the entire evacuation sweep closes the TOCTOU gap in
        :meth:`push`: a concurrent :meth:`push` is either forced to block on
        ``_count_lock`` until the sweep completes (after which it observes
        ``_worker_failed = True`` and raises), or it completed its
        :meth:`queue.Queue.put` while it held ``_count_lock`` before the evacuation
        acquired it, ensuring every enqueued item is collected by the sweep.

        Immediately before returning on either the normal-stop or crash-evacuation path,
        the thread clears ``_worker_running`` to ``False`` under ``_count_lock``.
        :meth:`close` evaluates ``_worker_running`` rather than
        :meth:`threading.Thread.is_alive` when deciding whether to inject the shutdown
        sentinel.  Because both the flag write (here) and the flag read (in
        :meth:`close`) are serialized under the same lock, there is no gap: a
        :meth:`close` call that races with the thread's final OS-level exit either
        reads ``False`` and skips sentinel injection entirely, or acquires ``_count_lock``
        first and places the sentinel before this flag-clear runs — in which case the
        sentinel is consumed by the evacuation sweep or the normal loop iteration and
        ``_pending`` is correctly decremented.

        After the main loop exits (via ``return`` on either path), an outer ``try/finally``
        block performs a non-blocking sweep of the write queue.  Under the
        ``_count_lock``-atomic sentinel placement guarantee in :meth:`close`, this sweep
        is always a no-op; it defends against any future regression where
        :meth:`queue.Queue.put` could race after the evacuation ``finally`` block has
        already checked the queue.

        :return: None
        :rtype: None
        """
        try:
            while True:
                entry: str | None = self._write_queue.get()
                stop: bool = entry is None
                written: bool = False
                error_entry: tuple[dict[str, Any], str] | None = None
                dead_letter_entry: str | None = None
                worker_failed: bool = False
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
                            with open(self._quarantine_path, "a", encoding="utf-8") as _qf:
                                _qf.write(entry + "\n")
                                _qf.flush()
                                os.fsync(_qf.fileno())
                        except Exception:
                            sys.stderr.write(
                                f"SOVEREIGN-EDGE CRITICAL: quarantine write failed; "
                                f"entry emitted to stderr for supervisor recovery: {entry}\n"
                            )
                            sys.stderr.flush()
                            worker_failed = True
                        try:
                            _obj: dict[str, Any] = json.loads(entry)
                            error_entry = (_obj["receipt"], _obj["sieved_content"])
                        except (json.JSONDecodeError, KeyError):
                            dead_letter_entry = entry
                except Exception:
                    worker_failed = True
                    if entry is not None:
                        try:
                            _obj = json.loads(entry)
                            error_entry = (_obj["receipt"], _obj["sieved_content"])
                        except (json.JSONDecodeError, KeyError):
                            dead_letter_entry = entry
                finally:
                    with self._count_lock:
                        if worker_failed:
                            self._worker_failed = True
                        self._pending -= 1
                        if written:
                            self._committed += 1
                        elif error_entry is not None:
                            self._write_errors.append(error_entry)
                        elif dead_letter_entry is not None:
                            if len(self._dead_letter) >= _DEAD_LETTER_MAX:
                                del self._dead_letter[0]
                            self._dead_letter.append(dead_letter_entry)
                    self._write_queue.task_done()
                if stop or worker_failed:
                    if worker_failed:
                        try:
                            with self._count_lock:
                                while True:
                                    try:
                                        orphan: str | None = self._write_queue.get_nowait()
                                        if orphan is not None:
                                            try:
                                                _orphan_obj: dict[str, Any] = json.loads(orphan)
                                                orphan_pair: tuple[dict[str, Any], str] = (
                                                    _orphan_obj["receipt"],
                                                    _orphan_obj["sieved_content"],
                                                )
                                                self._pending -= 1
                                                self._write_errors.append(orphan_pair)
                                            except (json.JSONDecodeError, KeyError):
                                                self._pending -= 1
                                                if len(self._dead_letter) >= _DEAD_LETTER_MAX:
                                                    del self._dead_letter[0]
                                                self._dead_letter.append(orphan)
                                        else:
                                            self._pending -= 1
                                        self._write_queue.task_done()
                                    except _queue.Empty:
                                        break
                        finally:
                            with self._count_lock:
                                if self._closed:
                                    try:
                                        while True:
                                            self._write_queue.get_nowait()
                                            self._pending -= 1
                                            self._write_queue.task_done()
                                    except _queue.Empty:
                                        pass
                                self._worker_running = False
                    else:
                        with self._count_lock:
                            self._worker_running = False
                    return
        finally:
            try:
                while True:
                    _stray: str | None = self._write_queue.get_nowait()
                    with self._count_lock:
                        self._pending -= 1
                    self._write_queue.task_done()
            except _queue.Empty:
                pass

    def push(self, receipt: dict[str, Any], sieved_content: str) -> None:
        """Enqueue a receipt entry for asynchronous JSONL persistence.

        Returns immediately after enqueueing — the background daemon thread performs
        the actual disk write and ``fsync`` without blocking the caller.  The entry is
        reflected in :attr:`size` immediately via ``_pending`` so that
        :attr:`~sovereign_edge.pipeline.EdgePipeline.buffer_depth` is accurate without
        requiring a :meth:`flush` call.

        Acquires ``_drain_lock`` to prevent a concurrent :meth:`drain` from sweeping the
        file between the enqueue and the background write, which would silently discard
        the entry.  Within that scope, ``_count_lock`` is held for the entire critical
        section: the ``_closed`` / ``_worker_failed`` liveness gate checks, the
        ``_pending`` increment, and the :meth:`queue.Queue.put` call are all executed
        atomically under the same lock acquisition.  This closes the TOCTOU gap where a
        thread could pass the liveness gate, release ``_count_lock``, and then call
        ``put()`` after the background worker's crash-evacuation loop had already swept
        the queue clean, leaving an orphaned entry with no consumer to call
        ``task_done()``.

        Raises :exc:`RuntimeError` immediately if :meth:`close` has already been called
        or if the background worker thread has terminated due to an unexpected exception
        (``_worker_failed`` is ``True``).  In either case the queue has no active consumer;
        enqueueing would increment ``_pending`` and place an entry into a queue that never
        calls ``task_done()``, causing any subsequent :meth:`flush` call to block
        indefinitely.  When ``_worker_failed`` is detected, call :meth:`drain` to recover
        any preserved receipts from ``_write_errors`` and :meth:`close` to shut down.

        :param receipt: A :class:`~sovereign_core.crypto.ForensicReceipt`-compatible
            dict to queue for later ledger submission.
        :type receipt: dict[str, Any]
        :param sieved_content: The Prose-Tax-minimized string payload associated with
            ``receipt``.
        :type sieved_content: str
        :raises RuntimeError: If the buffer has been closed via :meth:`close` or if the
            background writer thread terminated unexpectedly (``_worker_failed`` is
            ``True``).
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
                if self._worker_failed:
                    raise RuntimeError(
                        "OffGridBuffer background writer thread terminated unexpectedly; "
                        "new entries cannot be enqueued — call drain() to recover pending "
                        "receipts and close() to shut down"
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
        """Flush, read, sort, and atomically stage all buffered entries.

        Acquires ``_drain_lock`` for the entire critical section so that no concurrent
        :meth:`push` can enqueue to the background worker between :meth:`flush` and the
        atomic file promotion, preventing an in-flight write from landing in the file
        that is about to be replaced and being silently discarded.

        Calls :meth:`flush` to ensure all in-flight background writes are committed
        before reading the file.  Any entries preserved in ``_write_errors`` from prior
        background write failures are merged with the on-disk entries.  Any entries
        recovered from the pre-drain quarantine snapshot (``{path}.quarantine`` rotated to
        ``{path}.quarantine.staging``) are also parsed and included in the returned list
        so that ``drain_buffer()`` replays them against the ledger in the same pass rather
        than silently deferring them to the next boot cycle.  Malformed quarantine lines
        that cannot be deserialized are quarantined in ``_dead_letter`` rather than
        silently dropped.  The combined list of on-disk entries, write-error entries, and
        quarantine entries is sorted in ascending order by ``receipt["metadata"]["sequence"]``
        to protect the ledger's linear hash chain from out-of-order replay when entries
        arrive from concurrent push paths.  The sort key is evaluated inside a guarded
        helper that catches :exc:`TypeError` and :exc:`ValueError` so a non-numeric
        sequence value in a corrupted entry falls back to ``0`` rather than aborting the
        entire drain pass.

        **Two-phase commit scope**: the active buffer file is atomically renamed to the
        staging path (``{path}.staging``) via :func:`os.replace`.  Before that rename,
        any existing ``{path}.quarantine`` file is atomically rotated to
        ``{path}.quarantine.staging`` via :func:`os.replace`.  This rotation isolates
        the pre-drain quarantine snapshot from concurrent write failures: after the
        rotation, the background writer opens a fresh ``{path}.quarantine`` for any new
        :exc:`OSError` that occurs during the caller's replay pass, leaving it completely
        insulated from the current commit cycle.  The rotated snapshot
        (``{path}.quarantine.staging``) is then merged into the staging file so that
        staging is a self-contained recovery artefact containing the active buffer
        entries and the pre-drain quarantine entries.  A process exit at any point
        between :meth:`drain` and :meth:`commit_drain` leaves all pre-drain entries
        inside ``{path}.staging``; :meth:`_recover_staging` on the next boot merges
        them back without loss.  Any post-drain ``{path}.quarantine`` entries written by
        concurrent background-write faults survive independently: :meth:`commit_drain`
        deletes only ``{path}.quarantine.staging``, never the live
        ``{path}.quarantine``.

        ``_write_errors`` is not modified by this method; it is cleared exclusively by
        :meth:`commit_drain` up to the snapshot boundary captured at flush time.
        ``_drain_write_error_snapshot`` is set (under ``_count_lock``) to the
        ``_write_errors`` length captured at flush time so that :meth:`commit_drain`
        knows precisely how many entries to evict from the front of the list.

        ``_committed`` is decremented by the total count of non-blank disk lines (valid
        entries plus dead-letter lines) so that every byte footprint removed from the
        active file is reflected in the counter.  If :func:`os.replace` raises
        :exc:`OSError`, the active file is unchanged, the exception re-raises, and no
        counter is modified.

        Disk lines that cannot be parsed as valid JSON are handled contextually: if the
        failing line is the final non-blank line of the file AND the file lacks a trailing
        newline, it is treated as a partial write from an OS crash that truncated the line
        mid-character — the fragment is written to ``{path}.panic`` and
        :exc:`~sovereign_ledger.SovereignStorageError` is raised immediately so the active
        buffer file is preserved intact for operator inspection.  All other malformed disk
        lines (structurally complete but unparseable, or missing ``receipt`` /
        ``sieved_content`` keys) and malformed quarantine lines are quarantined in
        ``_dead_letter`` under the count lock; :attr:`dead_letter_count` reflects the
        accumulated count.  Returns an empty list when the buffer file does not exist, no
        write-error entries are pending, and no quarantine entries were recovered.

        :return: List of ``(receipt_dict, sieved_content)`` tuples sorted by sequence,
            combining on-disk JSONL entries, ``_write_errors`` entries, and pre-drain
            quarantine snapshot entries.
        :rtype: list[tuple[dict[str, Any], str]]
        :raises SovereignStorageError: If the final non-blank line of the buffer file fails
            to parse as valid JSON and the file lacks a trailing newline — the signature of
            an OS crash that truncated the last JSONL write mid-line.  The partial fragment
            is written to ``{path}.panic`` before raising; the active buffer file is left
            intact so the operator can inspect, truncate, or repair it before retrying
            :meth:`drain`.  This exception is deliberately distinct from the dead-letter
            path (which handles complete-but-corrupt lines such as ``{bad_json}\\n``) to
            ensure a crash-truncation is never silently absorbed.
        :raises OSError: If the buffer file exists but :meth:`pathlib.Path.read_text`
            raises :exc:`OSError` (e.g., permissions change, device removal, filesystem
            error after existence was confirmed) — :attr:`drain_read_failed` is set to
            ``True`` under the count lock before re-raising; or if :func:`os.replace`
            raises :exc:`OSError` during the active-to-staging rename (e.g., cross-device
            link, full disk, filesystem unmount mid-drain) — the active file is unchanged
            and ``_write_errors`` / ``_committed`` are preserved so the caller can retry.
        """
        with self._drain_lock:
            self.flush()

            def _seq_key(entry: tuple[dict[str, Any], str]) -> int:
                try:
                    return int(entry[0].get("metadata", {}).get("sequence", 0))
                except (TypeError, ValueError):
                    return 0

            with self._count_lock:
                _error_snapshot_count: int = len(self._write_errors)
                pending_error_entries: list[tuple[dict[str, Any], str]] = list(self._write_errors)

            _quarantine_text: str = ""
            if self._quarantine_path.exists():
                try:
                    os.replace(self._quarantine_path, self._quarantine_staging_path)
                    try:
                        _quarantine_text = self._quarantine_staging_path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        _quarantine_text = ""
                except OSError:
                    try:
                        _quarantine_text = self._quarantine_path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        _quarantine_text = ""

            quarantine_entries: list[tuple[dict[str, Any], str]] = []
            for _ql in _quarantine_text.splitlines():
                _qs: str = _ql.strip()
                if not _qs:
                    continue
                try:
                    _qobj: dict[str, Any] = json.loads(_qs)
                    quarantine_entries.append((_qobj["receipt"], _qobj["sieved_content"]))
                except (json.JSONDecodeError, KeyError):
                    with self._count_lock:
                        if len(self._dead_letter) >= _DEAD_LETTER_MAX:
                            del self._dead_letter[0]
                        self._dead_letter.append(_qs)

            if not self._path.exists():
                _err_lines: str = "".join(
                    json.dumps({"receipt": _er, "sieved_content": _ec}, ensure_ascii=False) + "\n"
                    for _er, _ec in pending_error_entries
                )
                _absent_stg_content: str = _quarantine_text
                if _absent_stg_content and not _absent_stg_content.endswith("\n"):
                    _absent_stg_content += "\n"
                _absent_stg_content += _err_lines
                if _absent_stg_content.strip():
                    _stg_tmp: str = ""
                    try:
                        with tempfile.NamedTemporaryFile(
                            dir=self._path.parent,
                            delete=False,
                            suffix=".tmp",
                            mode="w",
                            encoding="utf-8",
                        ) as _tmp_fh:
                            _stg_tmp = _tmp_fh.name
                            _tmp_fh.write(_absent_stg_content)
                        os.replace(_stg_tmp, self._staging_path)
                        _stg_tmp = ""
                    except OSError:
                        if _stg_tmp:
                            try:
                                os.remove(_stg_tmp)
                            except OSError:
                                pass
                with self._count_lock:
                    self._committed = 0
                    self._drain_write_error_snapshot = _error_snapshot_count
                all_entries: list[tuple[dict[str, Any], str]] = (
                    pending_error_entries + quarantine_entries
                )
                all_entries.sort(key=_seq_key)
                return all_entries

            with self._count_lock:
                self._drain_read_failed = False
            try:
                _raw_text: str = self._path.read_text(encoding="utf-8")
                raw_lines: list[str] = _raw_text.splitlines()
            except OSError:
                with self._count_lock:
                    self._drain_read_failed = True
                raise

            _file_lacks_trailing_newline: bool = bool(_raw_text) and not _raw_text.endswith("\n")
            _last_nonempty_idx: int = -1
            for _ri in range(len(raw_lines) - 1, -1, -1):
                if raw_lines[_ri].strip():
                    _last_nonempty_idx = _ri
                    break

            entries: list[tuple[dict[str, Any], str]] = []
            disk_line_count: int = 0
            for _li, line in enumerate(raw_lines):
                stripped: str = line.strip()
                if not stripped:
                    continue
                disk_line_count += 1
                try:
                    obj: dict[str, Any] = json.loads(stripped)
                    entries.append((obj["receipt"], obj["sieved_content"]))
                except json.JSONDecodeError:
                    if _li == _last_nonempty_idx and _file_lacks_trailing_newline:
                        _panic_path: Path = Path(str(self._path) + ".panic")
                        try:
                            with open(_panic_path, "w", encoding="utf-8") as _pf:
                                _pf.write(stripped + "\n")
                                _pf.flush()
                        except OSError:
                            pass
                        raise SovereignStorageError(
                            f"Partial write detected in buffer file '{self._path}': "
                            "the final line is a truncated JSON fragment (no trailing "
                            "newline), consistent with an OS crash mid-write.  "
                            f"The fragment has been written to '{_panic_path}' for "
                            "forensic recovery; the active buffer file is preserved "
                            "intact — repair or remove the partial tail line before "
                            "retrying drain()."
                        )
                    with self._count_lock:
                        if len(self._dead_letter) >= _DEAD_LETTER_MAX:
                            del self._dead_letter[0]
                        self._dead_letter.append(stripped)
                except KeyError:
                    with self._count_lock:
                        if len(self._dead_letter) >= _DEAD_LETTER_MAX:
                            del self._dead_letter[0]
                        self._dead_letter.append(stripped)

            entries.extend(pending_error_entries)
            entries.extend(quarantine_entries)
            entries.sort(key=_seq_key)

            file_entry_count: int = disk_line_count

            os.replace(self._path, self._staging_path)

            if _quarantine_text.strip():
                _merge_tmp: str = ""
                try:
                    _stg_content: str = self._staging_path.read_text(encoding="utf-8")
                    if _stg_content and not _stg_content.endswith("\n"):
                        _stg_content += "\n"
                    with tempfile.NamedTemporaryFile(
                        dir=self._path.parent,
                        delete=False,
                        suffix=".tmp",
                        mode="w",
                        encoding="utf-8",
                    ) as _mfh:
                        _merge_tmp = _mfh.name
                        _mfh.write(_stg_content + _quarantine_text)
                    os.replace(_merge_tmp, self._staging_path)
                    _merge_tmp = ""
                except OSError:
                    if _merge_tmp:
                        try:
                            os.remove(_merge_tmp)
                        except OSError:
                            pass

            with self._count_lock:
                self._committed = max(0, self._committed - file_entry_count)
                self._drain_write_error_snapshot = _error_snapshot_count

            return entries

    def commit_drain(self) -> None:
        """Complete the two-phase drain protocol by deleting all transient artefacts.

        Must be called after every successful :meth:`drain` cycle — once the caller has
        confirmed that every drained entry is either committed to the ledger or
        re-queued to the buffer.  Performs three cleanup operations atomically from the
        caller's perspective:

        1. **Staging file**: unlinks ``{path}.staging``.  If the file does not exist
           (active buffer was empty when :meth:`drain` was called, or a prior call
           already deleted it), the unlink is silently skipped.

        2. **Quarantine staging snapshot**: unlinks ``{path}.quarantine.staging`` — the
           pre-drain quarantine snapshot rotated by :meth:`drain`.  Its content was merged
           into the staging file by :meth:`drain`, making it redundant once the caller
           confirms all entries are committed.  The live ``{path}.quarantine`` is
           intentionally left untouched: any write failures that occurred during the
           replay pass (between :meth:`drain` and this call) were appended to a fresh
           ``{path}.quarantine`` and must not be discarded.

        3. **Write-error list**: removes the leading ``_drain_write_error_snapshot``
           entries from ``_write_errors`` under ``_count_lock``.  Only the entries
           captured at :meth:`drain` time are evicted; entries that arrived in
           ``_write_errors`` after the snapshot boundary are left intact for the next
           drain cycle.

        Calling this method without a preceding :meth:`drain` call is safe but a no-op:
        ``_drain_write_error_snapshot`` is ``0`` and the staging / quarantine files may
        not exist.

        :return: None
        :rtype: None
        """
        try:
            self._staging_path.unlink()
        except FileNotFoundError:
            pass
        try:
            self._quarantine_staging_path.unlink()
        except (FileNotFoundError, OSError):
            pass
        with self._count_lock:
            _snap: int = self._drain_write_error_snapshot
            if _snap:
                del self._write_errors[:_snap]
                self._drain_write_error_snapshot = 0

    def close(self) -> None:
        """Flush all pending writes and terminate the background worker thread.

        The ``_worker_running`` flag check, ``_closed = True`` update, ``_pending``
        increment, and ``None`` sentinel dispatch form a single atomic operation under
        ``_drain_lock → _count_lock``.  This eliminates two distinct races:

        *Concurrent teardown*: if two callers invoke :meth:`close` simultaneously, the
        first to acquire both locks observes ``_closed = False`` and places the sentinel;
        the second observes ``_closed = True`` inside the same lock scope and skips the
        placement entirely.  Without this atomicity, both callers could observe
        ``_worker_running == True`` before either had set ``_closed``, resulting in two
        ``_pending`` increments, two sentinels, and a counter that can never reach zero.

        *Post-evacuation OS-exit gap*: the background worker clears ``_worker_running``
        to ``False`` under ``_count_lock`` immediately before its final ``return``.
        Because :meth:`close` reads ``_worker_running`` under the same lock, there is
        no window in which :meth:`close` can inject a sentinel into a dead queue.
        The OS-level thread liveness check (:meth:`threading.Thread.is_alive`) has a
        gap between the Python thread function returning and the OS marking the thread
        dead; during that gap, ``is_alive()`` still returns ``True``.  Evaluating
        ``_worker_running`` instead eliminates the gap: the flag is ``False`` before the
        thread unwinds its call stack, so any concurrent :meth:`close` that acquires
        ``_count_lock`` after the flag-clear reads ``False`` and skips sentinel injection,
        preventing a :meth:`queue.Queue.put` that increments ``unfinished_tasks`` without
        a consumer and stalls :meth:`flush` → :meth:`queue.Queue.join` indefinitely.

        ``_worker_thread.join()`` is called **outside** the lock window so that the
        caller does not hold ``_drain_lock`` while waiting for the worker to drain the
        queue, which would prevent any concurrent :meth:`drain` call from completing.
        Callers that did not place the sentinel (``sentinel_placed = False``) skip the
        join; the caller that placed the sentinel guarantees the worker has exited.

        After the worker terminates, ``_write_errors`` is inspected under the count lock;
        if any receipt entries that failed to reach disk are still unresolved, a
        :exc:`RuntimeError` is raised so the host application is forced to acknowledge
        the un-journaled receipts rather than allowing them to vaporize silently.
        Callers must invoke :meth:`drain` before :meth:`close` whenever there is any
        possibility of prior disk write failures; :meth:`drain` surfaces and clears
        ``_write_errors`` so the subsequent :meth:`close` completes without error.

        The ``.lock`` file is unlinked in an unconditional ``finally`` block that executes
        after the write-error check regardless of whether that check raises.  This
        guarantees that the lock is always released — even when ``_write_errors`` causes
        a :exc:`RuntimeError` to propagate — so that a subsequent construction attempt on
        the same path is never blocked by a stale lock left behind by a faulted shutdown.

        This method is idempotent across both sequential and concurrent teardown paths:
        sequential second calls observe ``_closed = True`` and skip the sentinel; concurrent
        calls are serialized by ``_drain_lock`` so only the first acquirer ever places one.

        :return: None
        :rtype: None
        :raises RuntimeError: If one or more receipt entries are preserved in
            ``_write_errors`` at shutdown time, indicating that they failed to reach
            disk and have not been recovered via :meth:`drain`.  The ``.lock`` file is
            still unlinked before the exception propagates.
        """
        sentinel_placed: bool = False
        with self._drain_lock:
            with self._count_lock:
                if not self._closed and self._worker_running:
                    self._closed = True
                    self._pending += 1
                    self._write_queue.put(None)
                    sentinel_placed = True
        if sentinel_placed:
            self._worker_thread.join()
        try:
            with self._count_lock:
                error_count: int = len(self._write_errors)
            if error_count:
                raise RuntimeError(
                    f"OffGridBuffer closed with {error_count} un-journaled "
                    f"receipt{'s' if error_count != 1 else ''} in _write_errors; "
                    "call drain() before close() to recover pending entries"
                )
        finally:
            try:
                self._lock_path.unlink()
            except OSError:
                pass
            with OffGridBuffer._instance_registry_lock:
                OffGridBuffer._instance_registry.discard(self._instance_id)

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

    @property
    def worker_failed(self) -> bool:
        """True if the background writer thread terminated due to an unexpected exception.

        Set to ``True`` under the count lock by the worker thread when a non-:exc:`OSError`
        exception escapes the write path.  Once set, :meth:`push` raises :exc:`RuntimeError`
        immediately rather than enqueueing into a dead queue.  Call :meth:`drain` to recover
        any receipts preserved in ``_write_errors``, then :meth:`close` to shut down.

        :return: True if the worker thread exited due to an unhandled non-OSError exception.
        :rtype: bool
        """
        with self._count_lock:
            return self._worker_failed

    def has_write_errors(self) -> bool:
        """Return ``True`` if the buffer is in a volatile write-failure state.

        Atomically inspects both ``_write_errors`` and ``_worker_failed`` under a
        single ``_count_lock`` acquisition so the combined check is race-free.
        Returns ``True`` when either of the following conditions holds:

        - **``_write_errors`` non-empty**: at least one receipt entry failed to
          reach disk (:exc:`OSError` during the background write or ``fsync``) and is
          held in memory awaiting recovery via :meth:`drain`.
        - **``_worker_failed``**: the background writer thread terminated due to a
          non-:exc:`OSError` exception; future :meth:`push` calls raise immediately
          and no further writes will reach disk.

        When this method returns ``True`` after a re-queue :meth:`flush`, the caller
        must not invoke :meth:`commit_drain` — doing so would delete the staging file
        while failed re-queued entries remain only in memory, making them permanently
        irrecoverable on a subsequent process crash.

        :return: ``True`` if ``_write_errors`` is non-empty or the background writer
            thread has failed; ``False`` if the buffer is in a clean durable state.
        :rtype: bool
        """
        with self._count_lock:
            return bool(self._write_errors) or self._worker_failed

    @property
    def drain_read_failed(self) -> bool:
        """True if :meth:`drain` encountered an :exc:`OSError` while reading the JSONL file.

        Set to ``True`` under the count lock at the point where
        :meth:`pathlib.Path.read_text` raises :exc:`OSError` (e.g., a permissions
        change, device removal, or filesystem error that occurred after the buffer file
        was confirmed to exist).  After setting the flag, :meth:`drain` re-raises the
        :exc:`OSError` so the failure propagates to the caller; the on-disk file and
        ``_write_errors`` are not modified, so no entry is discarded.  Polling this
        property allows callers that catch the :exc:`OSError` to confirm that the flag
        was set and take explicit recovery action (alert, retry, or surface the filesystem
        error).

        :return: True if any :meth:`drain` call failed to read the JSONL file.
        :rtype: bool
        """
        with self._count_lock:
            return self._drain_read_failed

    @property
    def quarantine_path(self) -> Path:
        """Absolute path to the durable write-error quarantine file adjacent to the JSONL buffer.

        The file at this path (``{path}.quarantine``) accumulates entries that raised
        :exc:`OSError` during background disk writes and entries that triggered
        permanent format faults (:exc:`ValueError` / :exc:`TypeError`) during a
        :meth:`~sovereign_edge.pipeline.EdgePipeline.drain_buffer` replay pass.
        :meth:`_load_quarantine` reads it at boot time to restore any write-error
        entries from a prior run into ``_write_errors``.

        :return: Absolute path to ``{path}.quarantine``.
        :rtype: Path
        """
        return self._quarantine_path
