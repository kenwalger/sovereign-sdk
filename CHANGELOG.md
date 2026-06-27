# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Phase 9.5 — `sovereign-edge` sensor ingestion bridge** (new workspace member
  `packages/sovereign-edge/`): Introduces the middleware pipeline that intercepts
  sealed sensor wire frames from `sovereign-sensor`, applies the `sovereign-sieve`
  Prose Tax transformation to produce a verified, minimized payload, and dispatches
  a signed `ForensicReceipt` to `sovereign-ledger`.  An off-grid JSONL buffer absorbs
  receipts when the ledger is temporarily unreachable.  Zero network dependencies;
  all operations are strictly local-first.

  - **`SensorFrame` dataclass** (`models.py`): Deserializes the seven-key
    sovereign-sensor wire envelope (`v`, `n`, `t`, `q`, `alg`, `d`, `s`) from
    UTF-8 JSON bytes.  `from_bytes(raw: bytes) -> SensorFrame` classmethod decodes and
    maps all keys with strict type annotations.  `text_content() -> str` produces a
    deterministic, `sort_keys=True`, `ensure_ascii=False` JSON serialization of `d`
    for use as the canonical sieve input.

  - **`OffGridBuffer`** (`buffer.py`): Durable JSONL-backed queue that absorbs
    `ForensicReceipt` payloads when the ledger is unreachable.  `push()` enqueues
    entries to a background daemon `threading.Thread` worker and returns immediately,
    decoupling the sensor ingestion loop from disk-bound `os.fsync` latency.  The
    worker opens the buffer file in append mode, writes one JSON line, and
    `fsync`-commits before signalling completion via `queue.Queue.task_done()`.
    `flush()` blocks via `Queue.join()` until all pending writes are committed.
    `size` returns `_pending + _committed` — two atomic counters updated under a single
    lock acquisition in the worker `finally` block — so no file read is performed and
    no transient queue state can cause an item to be counted twice.  `_pending` covers
    items enqueued but not yet fsync'd; `_committed` covers items fsync'd but not yet
    drained.  `drain()` calls `flush()` first, reads the JSONL file, sorts entries in
    ascending order by `receipt["metadata"]["sequence"]` through a guarded `_seq_key`
    helper that catches `TypeError` / `ValueError` so a non-numeric sequence value falls
    back to sort key `0` rather than aborting the drain (stable sort preserves FIFO for
    equal keys), atomically clears the buffer via `tempfile` → `os.replace`, and
    decrements `_committed` by exactly the number of entries drained rather than zeroing
    it unconditionally, preserving counter increments for concurrent writes that arrive
    after `flush()` returns but before the file swap completes.  `close()` sends a
    `None` sentinel to terminate the worker.

  - **`EdgePipeline`** (`pipeline.py`): Four-stage orchestrator
    (deserialize → sieve → sign → commit).  Applies `sieve_with_metrics()` to the
    canonical observation payload within a `try/except Exception` guard: on sieve
    failure the pipeline falls back to the raw `text_content()` string,
    sets `tax_savings_percentage=0.0`, and stamps `sieve_fault=True` in the receipt
    metadata so downstream auditors can distinguish fault-path entries.  The receipt
    `metadata` carries `"sequence": frame.q` to provide the sort key used by
    `OffGridBuffer.drain()` for chronologically ordered ledger replay.  Mints an
    Ed25519 `ForensicReceipt` via `SovereignKeyManager` with embedded Prose Tax
    summary, then calls `append_receipt()`.  On `SovereignStorageError` or
    `sqlite3.Error`, the receipt is queued to the off-grid buffer.
    `drain_buffer()` re-queues entries that still cannot reach the ledger so no
    receipt is silently discarded.

  - **`EdgeResult` dataclass** (`models.py`): Structured return type from
    `EdgePipeline.process()` carrying `payload_hash`, `receipt`, `sieved_content`,
    `raw_token_count`, `optimized_token_count`, `tax_savings_percentage`, and
    `buffered` flag.

  - **`packages/sovereign-edge/pyproject.toml`**: `sovereign-edge` registered as
    workspace member at version `0.1.0` with workspace-source dependencies on
    `sovereign-core`, `sovereign-ledger`, and `sovereign-sieve`.

  - **`packages/sovereign-edge/tests/test_edge.py`** — 72 test cases across eight
    classes (`TestSensorFrame`: 13 cases; `TestOffGridBuffer`: 10 cases;
    `TestEdgePipelineProcess`: 18 cases; `TestEdgePipelineBuffering`: 6 cases;
    `TestEdgePipelineDrainBuffer`: 6 cases; `TestOffGridBufferAsync`: 8 cases;
    `TestEdgePipelineSieveFault`: 4 cases; `TestOffGridBufferWriteErrors`: 7 cases)
    verifying: wire frame deserialization, sort-keyed `text_content()` determinism,
    in-flight `size` accounting, `flush()` disk-commit guarantee, ascending-sequence sort
    in `drain()`, FIFO stable-sort preservation for equal sequence keys, non-integer
    sequence value tolerance, `_committed` counter accuracy after drain (including
    post-write disk corruption producing zero drift), happy-path ledger commit, receipt
    signature verifiability, `sieve_fault` metadata marking, raw-text fallback on sieve
    failure, zero savings percentage on fault path, fault-path ledger commit, buffering
    on closed ledger, buffer depth increment, drain-on-recovery, re-queue on persistent
    failure, post-drain ledger integrity, disk write error tracking via `write_error_count`,
    `size` accuracy under disk failure, full `drain()` recovery of write-error entries,
    `close()` raising `RuntimeError` when un-journaled entries remain, `push()` raising
    `RuntimeError` when called after `close()`, `EdgePipeline.close()` propagating
    `RuntimeError` from `OffGridBuffer.close()` when un-journaled write errors survive the
    internal `drain_buffer()` pass, 20-thread concurrent `push()`-vs-`close()` race
    producing zero orphaned queue entries, HMAC-SHA256 inbound signature rejection of
    forged frames before the sieve or ledger is reached, algorithm-gate rejection of any
    non-`hmac-sha256` `alg` field when `sensor_secret` is provisioned, and `try/finally`
    teardown on all 16 previously unclosed `OffGridBuffer` instances in `TestOffGridBuffer`
    and `TestOffGridBufferAsync`, idempotent `OffGridBuffer.close()` guarded by
    `_worker_thread.is_alive()`, HMAC hex case normalisation via `frame.s.lower()`,
    broadened sieve-fault fallback to `except Exception:` (naturally excludes
    ``BaseException`` subclasses), and `test_close_is_idempotent` confirming three
    consecutive `pipeline.close()` calls complete without deadlock,
    `SensorFrame.from_bytes()` protocol version gate rejecting ``v != 1``,
    `OffGridBuffer._dead_letter` capped at 100 entries with oldest-first eviction,
    `OffGridBuffer` background writer thread failure detection via ``worker_failed``
    property with lock-free orphan evacuation loop (``_drain_lock`` removed from
    evacuation path to eliminate circular-wait deadlock with concurrent ``drain()``),
    strict runtime type validation in `SensorFrame.from_bytes()` blocking wrong-type
    fields at the deserialization boundary, and three new tests covering non-OSError
    worker failure, field type anomaly rejection, drain-vs-crash deadlock regression,
    ``drain_buffer()`` requeue-loop push-failure survivability, concurrent ``close()``
    counter-drift elimination, dual-failure exception chaining in pipeline teardown,
    ``drain_read_failed`` flag observability for filesystem-blocked drain passes, and
    ``SensorFrame`` frozen-dataclass mutation guard.
    **75 passed, 0 failed.**

### Changed

- **`OffGridBuffer.drain()` — `_committed` decrement based on total disk line count, not valid-only count**
  (`buffer.py`): `file_entry_count` was derived as `len(entries) - len(pending_error_entries)`,
  counting only successfully parsed disk lines.  If a line was written by the background worker
  (incrementing ``_committed``) but subsequently corrupted on disk, it would parse into
  ``_dead_letter`` and be excluded from the decrement, leaving ``_committed = 1`` after a drain that
  cleared the file — permanent counter drift surfacing as ``size > 0`` on an empty buffer.  A new
  ``disk_line_count`` variable increments for every non-blank line regardless of parse outcome;
  ``file_entry_count`` is now set to ``disk_line_count`` so the ``_committed`` decrement covers the
  exact byte footprint removed from disk.  The ``max(0, …)`` floor prevents the counter going
  negative in cross-instance drain scenarios.  A new regression test
  (``test_drain_size_zero_after_committed_line_corrupted_on_disk``) reproduces the drift by overwriting
  a fsync'd buffer file with corrupt JSON and asserting ``size == 0`` and ``dead_letter_count == 1``
  after drain.

- **`OffGridBuffer` — `_closed` lifecycle guard on `push()` and `close()`** (`buffer.py`): A new
  ``_closed: bool = False`` flag is set to ``True`` atomically with the ``_pending += 1`` sentinel
  increment inside ``close()``'s count-lock block, ensuring any concurrent ``push()`` that has not
  yet entered its own ``_count_lock`` section will observe the flag and raise immediately.  ``push()``
  checks ``self._closed`` inside its ``_drain_lock → _count_lock`` critical section and raises
  :exc:`RuntimeError` with the message ``"OffGridBuffer is closed"`` before incrementing ``_pending``
  or calling ``queue.Queue.put``.  Without this guard, a post-close ``push()`` would increment
  ``_pending`` and enqueue to a dead queue, causing any subsequent ``flush()`` to block indefinitely
  because no worker ever calls ``task_done()``.  A new regression test
  (``test_push_raises_after_close``) validates the guard by calling ``push()`` on a cleanly closed
  buffer and asserting :exc:`RuntimeError` with ``"closed"`` in the message.

- **`OffGridBuffer.drain()` — fail-safe file rotation: empty list returned on `os.replace` failure**
  (`buffer.py`): `drain()` previously returned the parsed entries list regardless of whether the
  atomic `tempfile` → `os.replace` promotion succeeded.  If `os.replace` raised `OSError`, the
  buffer file was not cleared, yet `drain_buffer()` would still commit every entry to the ledger.
  On the next drain the same entries would be read again, yielding double-replay corruption in the
  ledger's append-only hash chain.  The return statement is now `return entries if replaced else []`
  so that no downstream commits are made unless the buffer file has been provably cleared from disk.

- **`OffGridBuffer.__init__()` — parent directory guaranteed at construction** (`buffer.py`):
  Adds `self._path.parent.mkdir(parents=True, exist_ok=True)` as the first action after
  resolving the buffer path.  Previously, both the background append path
  (`open(self._path, "a")`) and the `tempfile.NamedTemporaryFile(dir=self._path.parent)`
  call inside `drain()` assumed the parent directory existed without ensuring it.  A
  custom nested buffer path such as ``"/opt/sovereign/buffers/node-01/.edge_buffer.jsonl"``
  would fail at first write rather than at construction, leaving errors invisible until the
  first receipt arrived.  The explicit `mkdir` call surfaces the failure at object
  creation time and eliminates the entire class of parent-directory-absent write errors
  on the happy path.

- **`OffGridBuffer.close()` — `RuntimeError` raised on un-journaled entries** (`buffer.py`):
  After joining the background worker thread, `close()` inspects ``_write_errors`` under
  the count lock and raises :exc:`RuntimeError` if any entries remain unresolved.  The
  previous implementation joined the thread and returned silently regardless of
  ``_write_errors`` state, allowing the host application to shut down without ever learning
  that receipts had failed to reach disk.  The raise forces the caller to invoke
  :meth:`drain` before :meth:`close` when disk failures have occurred; ``drain`` surfaces
  and clears ``_write_errors``, after which ``close`` completes without error.  One new
  test (``test_close_raises_when_write_errors_remain``) validates this contract using a
  ``patch("builtins.open", side_effect=OSError("ENOSPC"))`` mock to simulate a full-disk
  condition.

- **`TestOffGridBufferWriteErrors` — trigger mechanism hardened to `rmdir` pattern**
  (`test_edge.py`): The three pre-existing write-error tests previously used a
  ``no_such_dir`` path to force ``FileNotFoundError``; because ``__init__`` now creates the
  parent directory, that path would succeed and the write errors would not occur.  All
  three tests are updated to call ``buf_dir.rmdir()`` immediately after constructing the
  buffer so the directory is removed from under the writer thread.  Tests
  ``test_disk_write_error_increments_write_error_count`` and
  ``test_size_includes_write_error_entries`` also add a ``buf.drain()`` call before
  ``buf.close()`` to clear ``_write_errors`` so that the new hardened ``close()`` does not
  raise during test teardown.

- **`EdgePipeline.__init__()` — explicit `chmod(0o700)` after `mkdir`** (`pipeline.py`):
  Adds `key_path.parent.chmod(0o700)` immediately after `key_path.parent.mkdir(...)`.  The
  `mkdir` call sets the permissions bitmask only when creating a new directory; if the
  directory already existed (e.g. a prior run used a lax default umask), the permissions
  were never corrected.  The explicit `chmod` call re-enforces owner-only access regardless
  of the directory's prior state, closing the window where a pre-existing key directory
  with world-readable permissions could expose private key material.  On Windows the call
  is silently ignored by the OS, matching the established cross-platform behaviour of
  `mode=0o700` in `mkdir`.

- **`OffGridBuffer` — disk write error tracking and recovery** (`buffer.py`): Replaces the
  silent `except OSError: pass` in `_disk_writer` with structured failure preservation.
  When a write or ``fsync`` raises :exc:`OSError` (full disk, read-only filesystem), the
  serialized entry is deserialized and appended to a new ``_write_errors`` list under the
  count lock so the receipt is not silently discarded.  The ``_pending`` decrement and
  ``_write_errors`` append now happen in a single ``with self._count_lock:`` block inside
  the worker's ``finally`` path to eliminate any transient over-count.  ``size`` is updated
  to return ``_pending + _committed + len(_write_errors)`` so ``buffer_depth`` remains
  accurate after a disk failure.  ``drain()`` snapshots ``_write_errors`` after ``flush()``
  returns (when the queue is idle and no concurrent push can occur), merges the error
  entries with the on-disk entries, sorts the combined list by sequence, and clears
  ``_write_errors`` atomically with the ``_committed`` decrement on successful file
  rotation.  If the file rotation fails, ``_write_errors`` is left intact for the next
  drain pass.  A new ``write_error_count: int`` property provides an explicit diagnostic
  indicator.  Three new tests in ``TestOffGridBufferWriteErrors`` cover: error count
  increment, ``size`` accuracy under disk failure, and full ``drain()`` recovery.

- **`OffGridBuffer` — `_drain_lock` critical section concurrency guard** (`buffer.py`): A new
  `_drain_lock: threading.Lock` protects the entire `drain()` critical section
  (`flush()` → file read → `os.replace` → counter decrement) and the `push()` enqueue window
  (`_pending` increment + `Queue.put`).  Without this lock a concurrent `push()` arriving after
  `flush()` returned but before `os.replace` completed could enqueue a new entry to the background
  worker; if the worker opened the buffer file in append mode before the atomic replace closed
  the inode, the write would land in the old file and be silently discarded after the replace.
  Because the background worker never acquires `_drain_lock`, and `push()` holds the lock only
  for the brief enqueue window (not for any blocking I/O), no deadlock is possible.

- **`OffGridBuffer.close()` — sentinel placement moved inside `_drain_lock`** (`buffer.py`):
  The ``_count_lock`` state mutation (``_closed = True``, ``_pending += 1``) and the sentinel
  ``queue.put(None)`` are now wrapped in ``with self._drain_lock:`` before delegating to
  ``_worker_thread.join()``.  Without this change, a ``push()`` call that had already observed
  ``_closed = False`` and incremented ``_pending`` — but had not yet called ``Queue.put`` —
  could enqueue its payload *after* ``close()`` placed the sentinel, because ``close()``
  previously held only ``_count_lock`` (not ``_drain_lock``) when calling ``queue.put(None)``.
  The worker would exit on the sentinel, leaving the payload with no ``task_done()`` — an
  orphaned entry that causes any subsequent ``Queue.join()`` to block indefinitely.  Serializing
  both operations under ``_drain_lock`` eliminates the window: either ``push()`` holds the lock
  and completes its enqueue before the sentinel is placed, or ``close()`` holds the lock first
  and ``push()`` observes ``_closed = True`` and raises.  ``_worker_thread.join()`` is called
  outside the lock so that ``drain()`` can still make progress concurrently during a long worker
  flush.  A new 20-thread stress test (``test_concurrent_push_vs_close_no_orphan_entries``)
  asserts that ``queue.unfinished_tasks == 0`` after the worker joins, directly verifying the
  absence of orphan entries.

- **`packages/sovereign-edge/pyproject.toml` — explicit minimum version constraints**: Each
  ecosystem dependency now carries a `>=1.1.0` floor:
  `sovereign-core>=1.1.0`, `sovereign-ledger>=1.1.0`, `sovereign-sieve>=1.1.0`.  The bare
  package names previously allowed the build backend to resolve any version, including
  pre-fortification releases that lack the `_pending`/`_committed` counter API and
  `SovereignStorageError` behaviour required by `EdgePipeline`.

- **`OffGridBuffer.drain()` — sort key guarded against non-numeric sequence values**
  (`buffer.py`): The sequence-sort lambda `int(e[0].get("metadata", {}).get("sequence", 0))`
  is replaced with a local `_seq_key` helper that wraps the cast in
  `try/except (TypeError, ValueError)`.  A corrupted or non-numeric sequence value
  (e.g. `"not-a-number"`, `None`) previously raised `ValueError` inside `list.sort()`,
  aborting the entire drain pass and leaving every buffered receipt stranded on disk
  with no exception visible to the caller — a silent, total data-loss path.  The guard
  falls back to sort key `0`, keeping every valid entry in the returned list regardless
  of what sits in a corrupted neighbour's metadata.

- **`OffGridBuffer.drain()` — `_committed` decremented by drained count, not zeroed**
  (`buffer.py`): `self._committed = 0` is replaced with
  `self._committed = max(0, self._committed - drained)` under the count lock, where
  `drained = len(entries)` is the number of valid entries actually removed from disk.
  Zeroing unconditionally erased the accounting for any write whose `fsync` completed
  between `flush()` returning and the `os.replace` closing — a real window when the
  sensor stream continues running during a recovery drain.  Subtracting only the drained
  count preserves those increments so `buffer_depth` remains accurate without a follow-up
  `flush()`.  `max(0, …)` prevents the counter going negative in cross-instance drain
  scenarios where the file contains entries written by a different `OffGridBuffer`
  instance that are not tracked by this instance's `_committed`.

- **`EdgePipeline.__init__()` — key directory created with `mode=0o700`** (`pipeline.py`):
  `key_path.parent.mkdir(parents=True, exist_ok=True)` now passes `mode=0o700`,
  restricting automatically generated edge node credential directories to owner-only
  access on POSIX targets from the moment of creation.  Mode is ignored safely on
  Windows.

- **`OffGridBuffer.size` — file read eliminated; race-free atomic counter pair**
  (`buffer.py`): The previous `size` implementation snapshotted `_in_flight` under
  the count lock, released the lock, then read the file.  The background worker
  decrements `_in_flight` in its `finally` block *after* the `fsync` completes, so
  between the lock release and the file read the item could exist simultaneously in
  both the `_in_flight` snapshot (not yet decremented) and the file (already written),
  yielding `size = 2` for a single buffered entry.  `size` now returns
  `_pending + _committed` under a single lock acquisition — no file read, no race
  window.  `_pending` and `_committed` are both updated atomically inside the same
  `with self._count_lock:` block in the worker `finally`, so their sum is always
  exact.

- **`EdgePipeline` — `close()` lifecycle method** (`pipeline.py`): Adds a `close()` method
  that performs a best-effort :meth:`drain_buffer` pass before delegating to
  :meth:`OffGridBuffer.close` to join the background daemon writer thread.  Without this
  method the pipeline leaked the daemon thread on every process exit and left the
  `OffGridBuffer` lifecycle guard unreachable; any un-journaled write errors at shutdown
  would be silently abandoned.  The drain-before-close sequence commits any buffered
  receipts if the ledger has recovered since they were queued, then enforces the
  un-journaled-entry invariant via ``OffGridBuffer.close()``.  ``RuntimeError`` raised by
  ``OffGridBuffer.close()`` propagates unmodified so the caller can distinguish clean
  teardown from a dirty shutdown carrying unrecoverable receipts.  One new test
  (``test_close_propagates_buffer_write_error_as_runtime_error``) validates the invariant by
  simulating a full-disk condition with ``patch("builtins.open", side_effect=OSError)``
  and asserting that ``pipeline.close()`` raises ``RuntimeError`` matching ``"un-journaled"``.

- **`EdgePipeline.close()` — `drain_buffer()` wrapped in `try/finally`** (`pipeline.py`):
  ``drain_buffer()`` is now executed inside a ``try`` block with ``self._buffer.close()``
  in the corresponding ``finally`` block.  Previously, an unhandled exception propagating
  from the drain pass (for example, an unexpected error from the ledger layer that bypasses
  ``drain_buffer()``'s internal ``except`` clauses) would abort ``close()`` before
  ``OffGridBuffer.close()`` was reached, leaving the background daemon writer thread
  running indefinitely and the buffer file handle unclosed.  The ``finally`` guarantee means
  the daemon thread is joined and resources are reclaimed regardless of what the drain
  pass raises.

- **`edge_pipeline` test fixture — `yield` teardown with `pipeline.close()`**
  (`test_edge.py`): The fixture previously used a bare ``return``, leaving the
  ``OffGridBuffer`` background writer thread alive for the entire pytest process lifetime
  after each test completed.  Refactored to a ``yield pipeline`` / ``pipeline.close()``
  pattern so the thread is joined immediately after each test's teardown phase.  Pytest
  honors fixture dependency order and tears down ``edge_pipeline`` before ``mem_ledger``,
  ensuring the ledger is still open when ``pipeline.close()`` calls ``drain_buffer()``.

- **`TestEdgePipelineBuffering` — `try/finally` teardown on ad-hoc pipeline instances**
  (`test_edge.py`): The three test methods that construct a local ``EdgePipeline`` directly
  (``test_process_sets_buffered_true_when_ledger_closed``,
  ``test_process_increments_buffer_depth_on_ledger_error``, and
  ``test_process_returns_payload_hash_even_when_buffered``) previously exited without calling
  ``pipeline.close()``, leaving a live daemon thread after each test.  Each method now wraps
  its body in ``try/finally`` with ``pipeline.close()`` in the ``finally`` block so the
  background writer thread is joined even when an assertion fails mid-test.

- **`TestEdgePipelineDrainBuffer` — `flush()` sync points and `try/finally` teardown**
  (`test_edge.py`): All four drain-buffer tests that write via ``pipeline_a`` and then read
  via ``pipeline_b`` now call ``pipeline_a._buffer.flush()`` immediately after each
  ``process()`` call.  Without the explicit flush the background writer thread may not have
  fsync-committed the JSONL line to disk before ``pipeline_b.drain_buffer()`` calls
  ``drain()`` → ``flush()``; on a loaded scheduler the two flush calls can race and
  ``pipeline_b`` may read an empty file.  All seven local pipeline instances across the four
  methods are now closed in ``try/finally`` blocks; ``pipeline_b.close()`` is always called
  before the recovery ledger is closed so the ``drain_buffer()`` pass inside ``close()``
  still has an open ledger to commit against.

- **`EdgePipeline.process()` — sieve-fault fallback calls `frame.text_content()` once**
  (`pipeline.py`): The ``except Exception`` fallback block previously called
  ``frame.text_content()`` a second time (the ``try`` branch called it once inside
  ``sieve_with_metrics(frame.text_content())``, the ``except`` branch called it again to
  obtain ``raw_text``).  ``text_content()`` performs a ``json.dumps`` on the observation
  payload on every invocation; calling it twice on the fault path is redundant and
  inconsistent with the non-redundant optimization principle.  ``raw_text`` is now assigned
  from a single ``frame.text_content()`` call before the ``try`` block; both the happy path
  (``sieve_with_metrics(raw_text)``) and the fault path (``SieveOutput(text=raw_text, …)``)
  consume the same string reference.

- **`EdgePipeline` — HMAC-SHA256 inbound signature verification** (`pipeline.py`): A new
  ``sensor_secret: str | bytes = b""`` parameter is added to ``EdgePipeline.__init__()``.
  When non-empty, ``process()`` immediately reconstructs the exact HMAC-SHA256 preimage
  from the deserialized ``SensorFrame`` fields — matching the format produced by
  ``SovereignEnvelope.seal()`` in ``sovereign-sensor``:
  ``"1|{len(n_bytes)}:{n}|{len(t_bytes)}:{t}|{q}|{len(alg_bytes)}:{alg}|{canonical_d}"``
  where ``canonical_d = json.dumps(d, separators=(",", ":"), sort_keys=True,
  ensure_ascii=False)`` — and compares the resulting digest against ``frame.s`` via
  ``hmac.compare_digest`` to prevent timing-oracle leakage.  A digest mismatch raises
  :exc:`ValueError` with the message ``"Sensor frame signature verification failed for
  node '{n}' sequence {q}: HMAC-SHA256 digest mismatch"`` before the payload reaches the
  sieve or ledger.  Verification is skipped when ``sensor_secret`` is empty or when
  ``frame.alg != "hmac-sha256"``, preserving backwards compatibility with unauthenticated
  deployments.  The secret is stored as ``bytes`` on the instance; ``str`` inputs are
  UTF-8-encoded at assignment time.

- **`TestEdgePipelineProcess` — cryptographic rejection test** (`test_edge.py`): New test
  ``test_process_rejects_forged_sensor_signature`` verifies that ``process()`` raises
  :exc:`ValueError` matching ``"signature verification failed"`` when the ``s`` field of the
  wire frame is replaced with an all-zero hex string of the same length.  The test confirms
  the rejection occurs before any sieve or ledger interaction: the fixture pipeline is
  provisioned with ``_SENSOR_SECRET = SoftwareFallbackDriver._MOCK_KEY`` so a genuine
  frame passes but the forged frame is deterministically rejected.  ``TestEdgePipelineProcess``
  grows from 16 to 17 cases.

- **`SensorFrame.from_bytes()` — protocol version gate** (`models.py`): After decoding the
  JSON dict, an explicit ``if frame["v"] != 1:`` check now raises :exc:`ValueError` with
  message ``"Unsupported wire format version {v!r}: sovereign-edge requires protocol version 1"``
  before constructing the dataclass.  The previous implementation silently accepted any integer
  value in the ``v`` field, which would allow a future or malformed wire envelope (where key
  positions may carry different semantics) to be deserialized without error and passed to the
  HMAC verifier, sieve, and ledger with structurally wrong field bindings.  A new test
  (``test_from_bytes_raises_on_unsupported_version``) validates the gate by submitting a frame
  with ``"v": 2`` and asserting :exc:`ValueError` matching ``"Unsupported wire format version"``.
  ``TestSensorFrame`` grows from 11 to 12 cases.

- **`OffGridBuffer._dead_letter` — 100-entry eviction cap** (`buffer.py`): A module-level
  constant ``_DEAD_LETTER_MAX = 100`` is introduced.  Both append sites — the ``_disk_writer``
  OSError recovery path and the ``drain()`` malformed-line path — now check
  ``if len(self._dead_letter) >= _DEAD_LETTER_MAX: del self._dead_letter[0]`` under the count
  lock before appending.  Without this cap, a rogue sensor emitting a continuous stream of
  malformed payloads would grow ``_dead_letter`` without bound, consuming heap memory
  proportional to the number of corrupt lines ever received.  The eviction drops the oldest
  entry (index 0) first so the most recent quarantined strings are always retained for
  out-of-band inspection.  The ``dead_letter_count`` property continues to reflect the current
  list length (bounded at 100 under sustained fault injection).

- **`OffGridBuffer.close()` — idempotent guard via `_worker_thread.is_alive()`** (`buffer.py`):
  The previous implementation unconditionally acquired ``_drain_lock``, set ``_closed = True``,
  incremented ``_pending``, and enqueued the ``None`` sentinel regardless of whether the worker
  thread was already terminated.  A second ``close()`` call would corrupt ``_pending`` with an
  orphan increment (the sentinel placed in the dead queue is never processed, so
  ``task_done()`` is never called and the counter never decremented) and would block indefinitely
  on ``Queue.join()`` if ``flush()`` was subsequently called.  The shutdown sequence is now
  guarded by ``if self._worker_thread.is_alive():``: when the thread has already been joined
  (because a prior ``close()`` completed), the sentinel placement and join are skipped entirely
  and only the ``_write_errors`` invariant check is repeated, which is safe and cheap.  This
  makes ``close()`` safe to call from overlapping teardown paths — a ``try/finally`` in the
  caller, a ``yield``-based pytest fixture, and ``EdgePipeline.close()`` — without deadlocking
  or inflating counter state.

- **`EdgePipeline.process()` — HMAC hex case normalisation** (`pipeline.py`): The comparison
  ``_hmac.compare_digest(expected_sig, frame.s)`` assumed the incoming signature was lowercase
  hex.  ``binascii.hexlify`` always produces lowercase output, but raw bare-metal hardware
  drivers (e.g. an ESP32 HMAC peripheral) may emit uppercase hex.  ``frame.s.lower()`` is
  now applied before passing to ``compare_digest`` so that any mix of upper- and lower-case
  hex characters in ``frame.s`` is accepted without a spurious mismatch.  ``compare_digest``
  retains its constant-time guarantee because the normalised strings have the same length.

- **`EdgePipeline.process()` — sieve-fault fallback guard broadened to `except Exception:`** (`pipeline.py`):
  The narrow ``except (ValueError, KeyError, RuntimeError, AttributeError, TypeError):``
  tuple around ``sieve_with_metrics(raw_text)`` is replaced with ``except Exception:``.
  ``SystemExit`` and ``KeyboardInterrupt`` both inherit from ``BaseException`` — not
  ``Exception`` — so the broader guard naturally excludes all host-level abort signals
  without an explicit re-raise.  The previous tuple also propagated ``ArithmeticError``,
  ``LookupError``, ``IndexError``, and any other ``Exception`` subclass raised by an
  anomalous third-party sieve plugin, causing the calling frame to be dropped rather
  than engaging the ``sieve_fault=True`` fallback sign/commit path.  ``except Exception:``
  closes this gap: every ``Exception`` subclass triggers the safe fallback; every
  ``BaseException`` that is not also an ``Exception`` propagates unobstructed.

- **`EdgePipeline.process()` — algorithm-gate hard-block when `sensor_secret` is provisioned**
  (`pipeline.py`): The previous condition ``if self._sensor_secret and frame.alg ==
  "hmac-sha256"`` silently skipped verification when ``frame.alg`` was any value other than
  ``"hmac-sha256"``.  A sensor frame spoofing ``"alg": "none"`` or ``"alg": "ecdsa-p256"``
  bypassed the HMAC check entirely and was admitted to the sieve and ledger stages without any
  cryptographic validation.  The condition is restructured to ``if self._sensor_secret:`` with
  an inner ``if frame.alg != "hmac-sha256": raise ValueError(...)`` guard placed before the
  digest computation.  When ``sensor_secret`` is provisioned, the only accepted algorithm is
  ``"hmac-sha256"``; any other ``alg`` value raises :exc:`ValueError` with message
  ``"Unsupported or unauthenticated algorithm '{alg}' for node '{n}' sequence {q}:
  sensor_secret requires hmac-sha256"`` before the payload reaches the sieve or ledger.
  A new test (``test_process_rejects_unsupported_algorithm``) validates the gate by submitting
  a frame with ``"alg": "ecdsa-p256"`` and asserting :exc:`ValueError` matching
  ``"Unsupported or unauthenticated algorithm"``.  ``TestEdgePipelineProcess`` grows from
  17 to 18 cases.

- **`TestOffGridBuffer` and `TestOffGridBufferAsync` — `try/finally` teardown on all buffer
  instances** (`test_edge.py`): All 16 test methods across the two classes that construct an
  ``OffGridBuffer`` directly previously exited without calling ``buf.close()``, leaving the
  background daemon writer thread live for the remainder of the pytest process.  Each method
  now wraps its body in ``try/finally`` with ``buf.close()`` in the ``finally`` block, joining
  the worker thread immediately after the test regardless of assertion outcome.

- **`README.md` — `EdgePipeline` example updated with `sensor_secret` and `try/finally`
  teardown**: The code snippet in the ``sovereign-edge`` section is extended with the
  ``sensor_secret`` parameter and a ``try/finally`` block that calls ``pipeline.close()``
  and ``ledger.close()`` in the ``finally`` branch, demonstrating correct resource management
  to integrators.

- **Test suite — private attribute access eliminated** (`test_edge.py`):
  Three ``mem_ledger._conn.execute(...)`` direct-SQL queries are replaced with the
  public ``SovereignLedger.verify_ledger_integrity(expected_tip_hash=result.payload_hash)``
  call: ``test_process_commits_receipt_to_ledger`` (was ``COUNT(*) = 1``),
  ``test_process_ledger_row_matches_result_payload_hash`` (was ``SELECT payload_hash WHERE
  payload_hash = ?``), and ``test_sieve_fault_still_commits_to_ledger`` (was
  ``COUNT(*) = 1``).  The ``if not ledger._closed: ledger.close()`` guard in the
  ``mem_ledger`` fixture is simplified to an unconditional ``ledger.close()`` because
  ``SovereignLedger.close()`` is idempotent.  All 13 ``EdgePipeline`` constructions across
  the suite now pass ``sensor_secret=_SENSOR_SECRET`` so every test exercises inbound
  verification on the happy path.

- **`OffGridBuffer._disk_writer` — non-OSError exception caught; `_worker_failed` flag set**
  (`buffer.py`): An ``except Exception:`` clause is added after the ``except OSError:`` handler
  in the background writer loop.  When a non-:exc:`OSError` exception escapes the write path
  (e.g. a corrupted file descriptor or unexpected runtime error), the affected entry is preserved
  in ``_write_errors`` or ``_dead_letter`` (same logic as the ``OSError`` path), ``_worker_failed``
  is set to ``True`` under the count lock in the ``finally`` block, and the thread enters a drain
  loop that acquires ``_drain_lock`` and exhausts all remaining queue items — calling ``task_done()``
  for each — before returning.  This prevents :meth:`flush` from blocking indefinitely (all
  ``task_done()`` calls are made before the thread exits) and ensures no queued receipt is silently
  discarded when the worker terminates abnormally.  A new ``worker_failed: bool`` read-only property
  exposes the flag under the count lock.  :meth:`push` checks ``_worker_failed`` inside its
  ``_drain_lock → _count_lock`` critical section and raises :exc:`RuntimeError` immediately when the
  flag is set, preventing new entries from being enqueued into a dead queue.

- **`SensorFrame.from_bytes()` — strict runtime type validation on all seven wire fields**
  (`models.py`): Immediately after JSON decoding and before the protocol version check, each field
  is validated against its expected runtime type.  String fields (``n``, ``t``, ``alg``, ``s``) and
  the dict field (``d``) are checked via :func:`isinstance`; integer fields (``v``, ``q``) require
  ``isinstance(_val, int) and not isinstance(_val, bool)`` to exclude JSON booleans that Python's
  ``isinstance(True, int)`` would otherwise accept.  A type mismatch raises :exc:`TypeError` with a
  precise message naming the field, its expected type, and the received type before the frame can
  reach the version gate, HMAC verifier, sieve, or ledger.

- **`TestSensorFrame` — field type anomaly rejection test** (`test_edge.py`): New test
  ``test_from_bytes_raises_on_wrong_field_type`` submits a frame with ``"q": "not-an-int"`` and
  asserts :exc:`TypeError`, verifying the type gate blocks structurally invalid frames at the
  deserialization boundary.  ``TestSensorFrame`` grows from 12 to 13 cases.

- **`TestOffGridBufferWriteErrors` — non-OSError worker failure test** (`test_edge.py`): New test
  ``test_worker_non_oserror_failure_does_not_hang`` patches ``builtins.open`` with
  ``side_effect=RuntimeError("unexpected worker crash")`` to simulate a non-:exc:`OSError` exception
  in the background writer.  The test asserts that ``flush()`` returns without hanging, that
  ``worker_failed is True``, that a subsequent ``push()`` raises :exc:`RuntimeError` matching
  ``"background writer"``, and that ``drain()`` followed by ``close()`` complete normally.
  ``TestOffGridBufferWriteErrors`` grows from 5 to 6 cases.

- **`OffGridBuffer._disk_writer` — evacuation loop deadlock eliminated** (`buffer.py`): The
  evacuation path triggered by a non-:exc:`OSError` worker failure previously wrapped its
  ``get_nowait()`` loop in ``with self._drain_lock:``.  When :meth:`drain` holds ``_drain_lock``
  and is blocked in :meth:`queue.Queue.join` waiting for ``task_done()`` signals, the evacuation
  loop could never acquire the lock — a circular wait.  ``_drain_lock`` is removed from the
  evacuation path entirely; orphan items are now consumed and ``task_done()``-signalled under
  ``_count_lock`` per item only, which :meth:`drain` never holds during :meth:`queue.Queue.join`.
  Additionally, ``self._worker_failed = True`` is moved to the **first** statement inside the
  ``finally`` block's ``_count_lock`` section so that concurrent :meth:`push` callers observe
  the flag and raise before the counter updates complete, preventing any new enqueue into a
  terminating queue.

- **`TestOffGridBufferWriteErrors` — concurrent drain-vs-crash deadlock regression test**
  (`test_edge.py`): New test ``test_drain_concurrent_with_worker_crash_no_deadlock`` pushes two
  items under a ``builtins.open`` mock that fails on every append-mode write, then starts
  ``drain()`` in a daemon thread and asserts it joins within five seconds.  A regression that
  re-introduces ``_drain_lock`` in the evacuation path would cause the thread to hang
  indefinitely, making this test the authoritative guard against the cyclic-dependency deadlock.
  ``TestOffGridBufferWriteErrors`` grows from 6 to 7 cases.

- **`EdgePipeline.drain_buffer()` — requeue loop hardened against cascading push failures**
  (`pipeline.py`): The re-queue iteration loop previously called ``self._buffer.push()`` bare;
  a single :exc:`RuntimeError` from a closed or worker-failed buffer caused the exception to
  propagate immediately, abandoning every remaining item in the ``requeue`` local list without
  attempting them.  Each ``push()`` call is now wrapped in ``except RuntimeError:``; all items
  are iterated to completion and a ``push_failure_count`` is accumulated.  After the loop, if any
  pushes failed, a single :exc:`RuntimeError` is raised naming the count and advising recovery via
  ``drain()``.  No item is orphaned mid-iteration: every entry is either successfully re-queued or
  explicitly counted in the failure total.

- **`OffGridBuffer.close()` — concurrent-teardown race eliminated via atomic sentinel gate**
  (`buffer.py`): The previous implementation checked ``self._worker_thread.is_alive()`` outside
  ``_drain_lock``, then acquired ``_drain_lock`` to place the sentinel.  Two concurrent ``close()``
  callers could both observe ``is_alive() == True`` before either acquired the lock, then both
  increment ``_pending`` and place a ``None`` sentinel.  The worker processes the first sentinel
  and exits; the second sentinel is never consumed, leaving ``_pending`` permanently inflated by
  one.  The fix moves the ``is_alive()`` check inside ``_drain_lock → _count_lock`` and gates it
  additionally on ``not self._closed``: the first thread to acquire both locks sets ``_closed =
  True`` and places exactly one sentinel; every subsequent thread observes ``_closed = True`` and
  skips the placement, ensuring ``_pending`` is incremented exactly once regardless of concurrent
  teardown fan-out.

- **`TestEdgePipelineDrainBuffer` — requeue push-failure survivability test** (`test_edge.py`):
  New test ``test_drain_buffer_survives_push_failure_on_requeue`` patches ``append_receipt`` to
  raise :exc:`SovereignStorageError` (forcing both items to requeue) and patches ``_buffer.push``
  with a counting mock that always raises :exc:`RuntimeError`.  Asserts that ``push()`` is called
  exactly twice (all items attempted) and that :exc:`RuntimeError` matching ``"could not re-queue"``
  is raised.  With the broken code the second item was never attempted; the assertion
  ``push_call_count[0] == 2`` is the authoritative regression guard.
  ``TestEdgePipelineDrainBuffer`` grows from 5 to 6 cases.

- **`TestOffGridBufferAsync` — concurrent close counter-drift test** (`test_edge.py`): New test
  ``test_concurrent_close_no_counter_drift`` launches 8 threads that each call ``buf.close()``
  simultaneously after a push/flush/drain cycle.  Asserts all 8 threads join within 5 seconds
  (timeout = deadlock) and that ``buf.size() == 0`` after all threads complete.  A regression that
  re-exposes the non-atomic sentinel gate would produce ``size() == 1`` (or cause a hang) because
  multiple sentinels inflate ``_pending`` without matching decrements.
  ``TestOffGridBufferAsync`` grows from 7 to 8 cases.

- **`EdgePipeline.close()` — dual-failure exception chaining** (`pipeline.py`): Replaced the
  ``try/finally`` pattern with a captured-exception model.  If ``drain_buffer()`` raises, the
  exception is stored in ``drain_exc``; ``self._buffer.close()`` is always attempted regardless.
  If ``buffer.close()`` subsequently also raises, the buffer :exc:`RuntimeError` is chained via
  ``raise buf_exc from drain_exc`` so that the drain root cause is preserved in the traceback and
  not silently discarded by the ``finally`` swallowing rule.  If only one of the two raises,
  that exception propagates normally.  A new test
  (``test_close_chains_buffer_exc_from_drain_exc``) patches ``drain_buffer`` to raise and wraps
  the real ``_buffer.close()`` with a closure that calls the original then raises a second
  ``RuntimeError``; the test asserts ``exc_info.value.__cause__ is drain_error``, verifying
  the chaining invariant.  ``TestEdgePipelineBuffering`` grows from 6 to 7 cases.

- **`OffGridBuffer.drain()` — `drain_read_failed` observable flag** (`buffer.py`): The silent
  ``except OSError: return []`` block inside ``drain()`` that handles a ``Path.read_text``
  failure now also sets ``self._drain_read_failed = True`` under ``_count_lock`` before returning.
  Previously, a filesystem-level read failure (e.g. a permission change or device removal after
  the file-existence check) was indistinguishable from a genuine empty drain at the caller level:
  both returned ``[]``.  The new ``drain_read_failed: bool`` property allows the pipeline
  orchestrator to poll the flag and take explicit recovery action (alert, retry, or surface the
  error) rather than silently advancing the drain lifecycle on a stalled filesystem.  On-disk
  entries are preserved; the atomic replace is never reached, so no data is discarded.  A new test
  (``test_drain_read_failed_flag_set_on_oserror``) patches ``pathlib.Path.read_text`` to raise
  ``OSError("Permission denied")`` after a push/flush, asserts ``drain()`` returns ``[]`` and
  ``buf.drain_read_failed is True``, and then verifies that a subsequent unpatch drain recovers
  the on-disk entry cleanly.  ``TestOffGridBufferWriteErrors`` grows from 7 to 8 cases.

- **`SensorFrame` — frozen dataclass** (`models.py`): ``@dataclass`` upgraded to
  ``@dataclass(frozen=True)``.  Post-construction field assignment on any ``SensorFrame`` instance
  now raises ``dataclasses.FrozenInstanceError``.  The ``d: dict[str, Any]`` field retains a
  mutable reference (inner dict contents are not deep-frozen), but the reference itself is immutable
  so no pipeline stage can accidentally rebind ``frame.d`` to a different object after
  deserialization.  A new test (``test_sensor_frame_is_immutable``) attempts ``frame.n =
  "mutated-node-id"`` and asserts ``dataclasses.FrozenInstanceError``, guarding the frozen
  invariant against future reversion to a mutable dataclass.  ``TestSensorFrame`` grows from 13
  to 14 cases.

- **`EdgePipeline.drain_buffer()` — exhaustive re-queue on unexpected replay crash**
  (`pipeline.py`): The drain entries are now materialised into a ``drained`` list before
  the replay loop, and a ``processed`` counter advances only after each entry is fully
  resolved (committed or requeued).  An outer ``except Exception`` block captures any
  exception not handled by the inner ``(SovereignStorageError, sqlite3.Error)`` guard,
  appends ``drained[processed:]`` to the requeue list (all un-processed entries including
  the failing one), and re-raises the captured exception after the requeue pass completes.
  Without this fix, a ``ValueError`` or ``RuntimeError`` escaping ``append_receipt`` would
  abort the ``for`` loop immediately, abandoning every subsequent entry in local function
  scope with no path to recovery.  When both the replay crash and requeue-push failures
  occur simultaneously, the requeue ``RuntimeError`` is raised chained from the crash
  exception via ``__cause__`` so both failure causes are visible in the traceback.

- **`SensorFrame.d` — deep immutability via `MappingProxyType`** (`models.py`):
  ``from_bytes()`` now wraps ``frame["d"]`` in ``types.MappingProxyType`` before
  constructing the frozen dataclass instance.  ``@dataclass(frozen=True)`` prevents
  rebinding ``frame.d`` to a different object, but a raw ``dict`` reference would still
  allow in-place key mutation (``frame.d["k"] = v``), bypassing the shallow freeze.
  ``MappingProxyType`` closes this gap by raising ``TypeError`` on any attempted mutation
  through the reference.  The ``d`` field annotation is updated from ``dict[str, Any]`` to
  ``MappingProxyType[str, Any]``.  ``text_content()`` and the HMAC preimage computation in
  ``pipeline.py`` are updated to pass ``dict(self.d)`` / ``dict(frame.d)`` to
  ``json.dumps``, whose C encoder requires a native ``dict`` (``isinstance(o, dict)``
  returns ``False`` for ``MappingProxyType``, which would raise ``TypeError`` in the default
  encoder without conversion).

- **`test_drain_buffer_requeues_all_items_on_unexpected_exception`** (`TestEdgePipelineDrainBuffer`,
  `test_edge.py`): Buffers 3 receipts via a closed ledger; on the recovery pass, a mock
  succeeds on the 1st ``append_receipt`` call and raises ``ValueError`` on the 2nd.  After
  ``drain_buffer()`` raises, asserts ``pipeline._buffer.size == 2`` — verifying that both
  the crashing entry (index 1) and the un-reached entry (index 2) were re-queued to the
  buffer.  Without the fix, ``size == 1`` because the loop aborted at index 1 and the entry
  at index 2 was permanently lost.  ``TestEdgePipelineDrainBuffer`` grows from 6 to 7 cases.

- **`test_from_bytes_d_field_is_immutable_mapping`** (`TestSensorFrame`, `test_edge.py`):
  Constructs a live ``SensorFrame`` and asserts that ``frame.d["injected_key"] =
  "malicious_value"`` raises ``TypeError``, verifying the ``MappingProxyType`` write-block
  is in effect.  ``TestSensorFrame`` grows from 14 to 15 cases.

- **`SovereignDoubleFaultError` — total persistence failure sentinel** (`pipeline.py`,
  `__init__.py`): New ``RuntimeError`` subclass with a ``receipt: dict[str, Any]``
  attribute that encapsulates the catastrophic case where the ledger raises
  ``SovereignStorageError`` / ``sqlite3.Error`` and the subsequent off-grid buffer push
  also raises.  Previously, a push failure inside the ledger-fallback ``except`` block
  propagated as an uncaught ``RuntimeError`` with no reference to the signed receipt,
  making the payload unrecoverable at the host level.  The fix wraps
  ``self._buffer.push()`` in a nested ``try/except Exception as push_err:`` and raises
  ``SovereignDoubleFaultError(..., receipt=receipt_dict) from push_err`` so the signed
  receipt dict remains accessible through ``exception.receipt`` for out-of-band routing.
  ``SovereignDoubleFaultError`` is exported from ``sovereign_edge.__init__`` via the
  ``__all__`` list.  The ``process()`` docstring is updated with a
  ``:raises SovereignDoubleFaultError:`` entry.

- **`OffGridBuffer.drain()` — active OSError propagation** (`buffer.py`): The
  ``except OSError: ... return []`` block that handled ``Path.read_text`` failures was
  replaced with ``except OSError: ... raise``.  Returning ``[]`` in total silence meant
  that a filesystem-level read failure (permissions change, device removal) was
  completely indistinguishable from a successful empty drain at the caller level; the
  pipeline would advance the drain lifecycle with no committed receipts and no operator
  alert.  The flag ``self._drain_read_failed = True`` is still set under ``_count_lock``
  before re-raising so callers that catch the ``OSError`` can confirm the flag is set.
  On-disk entries and ``_write_errors`` are untouched; the atomic replace is never
  reached, so no data is discarded.  The ``drain()`` and ``drain_read_failed`` docstrings
  are updated to document the new raise-on-read-failure behaviour.

- **`EdgePipeline.drain_buffer()` — OSError propagation from buffer read** (`pipeline.py`):
  ``list(self._buffer.drain())`` is now wrapped in a ``try/except OSError as read_err:``
  block.  On ``OSError``, a descriptive ``RuntimeError`` is raised chained from the
  ``OSError`` via ``__cause__``, halting the replay pass immediately and surfacing a
  human-readable message directing operators to verify storage-tier accessibility.  The
  ``drain_buffer()`` docstring's ``:raises RuntimeError:`` entry is updated to cover
  this new propagation path.

- **`test_process_raises_sovereign_double_fault_error_on_double_failure`**
  (`TestEdgePipelineBuffering`, `test_edge.py`): Closes the ledger to force the
  ``SovereignStorageError`` path, then patches ``pipeline._buffer.push`` to raise
  ``RuntimeError``.  Asserts that ``process()`` raises ``SovereignDoubleFaultError``,
  that ``exception.receipt`` is a ``dict`` containing a 64-character ``payload_hash``,
  and that ``exception.__cause__`` is the ``RuntimeError`` from ``push()``.
  ``TestEdgePipelineBuffering`` grows from 7 to 8 cases.

- **`test_drain_buffer_raises_runtime_error_on_buffer_read_failure`**
  (`TestEdgePipelineDrainBuffer`, `test_edge.py`): Buffers one receipt via a closed
  ledger, then opens a recovery pipeline and patches ``pathlib.Path.read_text`` to raise
  ``OSError``.  Asserts that ``drain_buffer()`` raises ``RuntimeError`` with the
  message ``"off-grid buffer file could not be read"`` and that ``exception.__cause__``
  is an ``OSError``.  ``TestEdgePipelineDrainBuffer`` grows from 7 to 8 cases.

- **`test_drain_read_failed_flag_set_on_oserror`** (`TestOffGridBufferWriteErrors`,
  `test_edge.py`): Updated to assert ``pytest.raises(OSError)`` around ``buf.drain()``
  rather than asserting ``result == []``, reflecting the new ``raise`` behaviour in
  ``OffGridBuffer.drain()``.  The post-patch ``buf.drain()`` cleanup call still confirms
  that a subsequent unpatch drain recovers the on-disk entry cleanly.

- **`SovereignDoubleFaultError` — full two-tier exception chain** (`pipeline.py`):
  ``SovereignDoubleFaultError.__init__`` gains a required ``ledger_error: Exception``
  keyword parameter.  The original ledger exception (root cause of the fallback sequence)
  is now stored as ``self.ledger_error`` alongside ``self.receipt``.  Previously, raising
  ``from push_err`` set ``__cause__ = push_err`` and ``__suppress_context__ = True``,
  which hid ``ledger_err`` from the default traceback display.  Storing ``ledger_err``
  explicitly as a named attribute makes the full two-tier failure (ledger error → push
  error) inspectable without relying on implicit ``__context__`` chain traversal.  The
  raise site in ``process()`` is updated to pass ``ledger_error=ledger_err``.

- **``test_concurrent_push_vs_close_no_orphan_entries`` — remove ``queue.Queue`` internal
  coupling** (`test_edge.py`): The assertion ``buf._write_queue.unfinished_tasks == 0``
  was replaced with ``buf._pending == 0``.  ``unfinished_tasks`` is an undocumented
  implementation attribute of the standard-library ``queue.Queue`` class; coupling to it
  ties the test to a CPython internals contract.  ``_pending`` is our own
  ``OffGridBuffer`` counter that is decremented in the worker's ``finally`` block for
  every item (including the sentinel), so after ``close()`` calls ``Queue.join()`` and
  returns, ``_pending == 0`` guarantees the same sentinel-last invariant using only the
  buffer's own state.

- **``test_process_raises_sovereign_double_fault_error_on_double_failure`` — assert
  ``ledger_error`` attribute** (`test_edge.py`): An ``isinstance(dfe.ledger_error,
  (SovereignStorageError, sqlite3.Error))`` assertion verifies that the root-cause ledger
  exception is preserved on the ``SovereignDoubleFaultError`` instance, exercising the
  full two-tier failure chain.  ``import sqlite3`` added to the test-file stdlib imports.
  **Suite: 81 edge tests, 370 workspace tests passed, 1 skipped (POSIX fchmod).**

- **`OffGridBuffer.close()` — OS-exit-gap sentinel guard: `_worker_running` flag replaces
  `is_alive()` in the liveness check** (`buffer.py`): After a non-``OSError`` worker crash
  the Python thread function returns but the OS may not immediately unregister the thread;
  ``is_alive()`` can still return ``True`` in this gap.  A concurrent ``close()`` observing
  the stale ``True`` injects a ``None`` sentinel into the abandoned queue: ``_pending`` is
  incremented but no consumer ever calls ``task_done()``, stalling any subsequent
  ``flush()`` → ``queue.join()`` indefinitely.  The fix introduces a ``_worker_running:
  bool`` flag, set to ``True`` in ``__init__`` and cleared to ``False`` as the first
  statement inside the worker's ``finally`` block under ``_count_lock`` (on both the
  normal stop path and the crash-evacuation path), before the OS-thread teardown begins.
  ``close()`` reads ``self._worker_running`` under the same lock, so it observes ``False``
  before the OS marks the thread dead and skips sentinel injection entirely.  A new test
  (``test_close_skips_sentinel_during_worker_os_exit_gap``) forces a non-``OSError`` crash,
  joins the worker thread to guarantee ``_worker_running`` is ``False``, patches
  ``is_alive()`` to return ``True`` (reproducing the OS-exit gap deterministically), calls
  ``close()`` in a background thread, and asserts both ``close()`` and a subsequent
  ``flush()`` complete within timeout — confirming no sentinel was injected and
  ``_pending == 0``.  ``TestOffGridBufferWriteErrors`` grows from 8 to 9 cases.

- **`OffGridBuffer.push()` — TOCTOU liveness gate: `_worker_failed` check and `queue.put()`
  share a single `_count_lock` acquisition** (`buffer.py`): The previous implementation
  checked ``_worker_failed`` inside ``_count_lock``, released the lock, then called
  ``queue.put()`` outside.  A ``push()`` racer that passed the check before the worker
  crashed could then race the evacuation loop: after releasing ``_count_lock``, if the
  evacuation loop drained the queue to empty before the racer's ``queue.put()`` fired,
  the new item had no ``task_done()`` caller, leaving ``_pending`` permanently non-zero
  and stalling any subsequent ``queue.join()``.  The fix moves ``queue.put()`` inside the
  same ``_count_lock`` section as the ``_worker_failed`` check: either the racer enqueues
  atomically before the evacuation starts, or it observes ``_worker_failed = True`` after
  the evacuation holds the lock and raises immediately.  A 16-thread stress test
  (``test_push_toctou_worker_crash_no_dangling_items``) releases all racers into the
  liveness-gate window via ``threading.Barrier`` and asserts ``buf._pending == 0`` after
  all threads resolve; without the fix, an orphaned item leaves ``_pending > 0`` and
  causes any subsequent ``flush()`` to block indefinitely.
  ``TestOffGridBufferWriteErrors`` grows from 9 to 10 cases.

- **`EdgePipeline.process()` — sieve-fault fallback guard broadened to `except Exception:`**
  (`pipeline.py`): See the earlier Changed entry for full rationale.  Removes the narrow
  exception tuple and restores ``except Exception:`` so ``ArithmeticError``, ``LookupError``,
  and all other ``Exception`` subclasses from anomalous sieve plugins engage the safe
  fallback path rather than propagating to the caller.

- **`SensorFrame.d` docstring — shallow-freeze precision** (`models.py`): The Sphinx
  ``:param d:`` entry is updated to state that ``MappingProxyType`` "enforces shallow
  read-only protection on the top-level envelope dictionary keys; nested mutable values
  are not frozen."  The previous wording ("exposed as a read-only MappingProxyType")
  implied deep immutability that ``MappingProxyType`` does not provide.

- **`test_close_is_idempotent` docstring — correct implementation reference** (`test_edge.py`):
  The inline docstring previously stated that ``OffGridBuffer.close()`` "detects the
  already-terminated worker thread via ``is_alive()``."  Updated to reference the internal
  ``_worker_running`` state flag, matching the OS-exit-gap fix introduced in the preceding
  round where the ``is_alive()`` check was replaced by the flag precisely to close the
  race window this test exercises.

- **`EdgePipeline.__init__()` — secure-by-default initialization: `SovereignConfigurationError`
  and `allow_unauthenticated` parameter** (`pipeline.py`, `__init__.py`): A new
  ``SovereignConfigurationError(ValueError)`` class is introduced before ``EdgePipeline``.
  ``EdgePipeline.__init__`` gains a new ``allow_unauthenticated: bool = False`` keyword
  parameter.  When ``sensor_secret`` is empty or ``None`` and ``allow_unauthenticated`` is
  ``False``, the constructor raises ``SovereignConfigurationError`` immediately — before any
  key-directory creation, buffer construction, or key-manager wiring — so that accidental
  unauthenticated deployments are surfaced at object construction rather than silently
  passing every inbound frame without verification.  Callers that deliberately require no
  authentication must pass ``allow_unauthenticated=True`` to acknowledge the trade-off
  explicitly.  ``SovereignConfigurationError`` inherits from ``ValueError`` so existing
  callers that catch ``ValueError`` from the construction phase continue to handle it
  without modification.  The class and its new ``raises`` contract are exported from
  ``sovereign_edge.__init__`` via the ``__all__`` list.  The class docstring and
  ``EdgePipeline`` class docstring ``:param sensor_secret:`` and new
  ``:param allow_unauthenticated:`` / ``:raises SovereignConfigurationError:`` entries are
  updated accordingly.

- **`OffGridBuffer.drain()` — stale read-failure flag reset** (`buffer.py`): A
  ``with self._count_lock: self._drain_read_failed = False`` statement is added immediately
  before the ``try: raw_lines = self._path.read_text(...)`` block.  Without this reset, a
  prior ``OSError`` on ``read_text`` set ``_drain_read_failed = True`` permanently; once the
  filesystem recovered and subsequent drains succeeded, the flag remained ``True``
  indefinitely, causing any diagnostic poller to see a stale fault signal long after the
  storage tier had returned to a healthy state.  The reset is placed under ``_count_lock``
  for thread safety, co-located with the ``OSError`` handler that sets it to ``True``.
  Because the flag is only reached after ``_path.exists()`` returns ``True``, a
  non-existent buffer file path continues to return the prior flag state unchanged (no reset
  fires for an empty drain).

- **`TestEdgePipelineSecureInit` — four-case secure-init validation class** (`test_edge.py`):
  New top-level class ``TestEdgePipelineSecureInit`` adds four cases: (1)
  ``test_pipeline_raises_configuration_error_without_secret`` asserts
  ``SovereignConfigurationError`` matching ``"allow_unauthenticated=True"`` when no
  ``sensor_secret`` is passed; (2)
  ``test_pipeline_raises_configuration_error_with_empty_string_secret`` asserts the same for
  ``sensor_secret=""``, confirming that an empty string is treated identically to omission;
  (3) ``test_pipeline_construction_succeeds_with_allow_unauthenticated`` asserts construction
  succeeds and the pipeline can be cleanly closed when ``allow_unauthenticated=True`` is
  passed without a secret; (4)
  ``test_sovereign_configuration_error_is_value_error_subclass`` asserts that constructing
  without a secret raises as ``ValueError``, verifying the inheritance invariant.

- **`TestOffGridBufferWriteErrors` — `test_drain_read_failed_flag_resets_on_successful_drain`**
  (`test_edge.py`): Simulates a transient filesystem failure (``OSError`` from
  ``pathlib.Path.read_text``) so ``drain_read_failed`` becomes ``True``.  After the patch
  context exits, re-pushes an entry, flushes, and drains successfully.  Asserts
  ``drain_read_failed is False`` after the successful drain, verifying the reset path and
  guarding against future regression where stale ``True`` persists after storage recovery.
  ``TestOffGridBufferWriteErrors`` grows from 10 to 11 cases.

- **`OffGridBuffer.drain()` — atomic rotation failure now raises `OSError` (not `[]`)**
  (`buffer.py`): The ``except OSError:`` block surrounding the ``tempfile`` + ``os.replace``
  rotation previously caught the exception, cleaned up the staging file, and fell through
  to ``return entries if replaced else []``.  A rotation failure (full disk, cross-device
  rename, filesystem unmount mid-drain) returned ``[]`` silently: callers could not
  distinguish a genuinely empty buffer from a filesystem-blocked rotation.
  ``drain_buffer()`` in the pipeline layer would see ``[]``, commit nothing, and return
  without error — treating a storage fault as a healthy no-op and losing the operator's
  only signal that entries were not cleared.  The fix adds a bare ``raise`` at the end of
  the ``except OSError:`` block so the exception propagates to the caller after the
  temp-file cleanup completes.  ``_committed`` and ``_write_errors`` are left intact so a
  subsequent ``drain()`` call recovers all entries once the filesystem is repaired.  The
  ``drain()`` ``:return:`` docstring is updated to remove the ``or an empty list`` clause
  and a ``:raises OSError:`` entry is added covering both the read-failure and
  rotation-failure paths.

- **`SovereignDoubleFaultError` — `uncommitted_receipts` attribute for batch drain faults**
  (`pipeline.py`): The constructor gains a new optional keyword parameter
  ``uncommitted_receipts: list[dict[str, Any]] | None = None``.  The existing ``receipt``
  parameter is made optional (default ``None``) to reflect that the two fault contexts
  are mutually exclusive: a single-receipt fault from :meth:`process` sets ``receipt`` and
  leaves ``uncommitted_receipts = None``; a batch-drain double fault from
  :meth:`drain_buffer` sets ``uncommitted_receipts`` and leaves ``receipt = None``.
  The class docstring is updated with a second usage context (batch-receipt drain fault)
  and updated ``:param:`` / ``:type:`` entries for both attributes.

- **`EdgePipeline.drain_buffer()` — `try/finally` guarantees full capture; cascading
  double fault raises `SovereignDoubleFaultError`** (`pipeline.py`):

  *`try/finally` for unprocessed entry capture*: The
  ``except Exception as exc: crash_exc = exc; requeue.extend(drained[processed:])`` block
  is restructured to ``except Exception as exc: crash_exc = exc`` followed by
  ``finally: requeue.extend(drained[processed:])``.  The ``finally`` clause runs
  unconditionally regardless of whether an exception was raised or the loop completed
  normally; on normal completion ``drained[processed:] == []`` so no item is added.
  This closes the theoretical data-loss gap where a future refactor moves or wraps the
  ``except`` clause without preserving the unprocessed-entry extension.

  *Tracked failed-requeue entries*: The requeue loop now accumulates
  ``failed_requeue_entries: list[tuple[dict[str, Any], str]]`` alongside
  ``push_failure_count: int`` so the exact set of unrecoverable receipts is available
  after the loop, not just a count.

  *`SovereignDoubleFaultError` on double fault*: When the replay loop crashes
  (``crash_exc is not None``) AND one or more entries cannot be re-queued
  (``push_failure_count > 0``), the previous code raised ``RuntimeError(...)`` chained
  from ``crash_exc``, surfacing only a count and discarding the receipt content
  irreversibly.  The fix raises ``SovereignDoubleFaultError`` with
  ``uncommitted_receipts=[r for r, _ in failed_requeue_entries]`` and
  ``ledger_error=crash_exc``, attaching the exact receipt dicts so the host application
  can route them via an alternative channel rather than permanently losing them.

  *OSError message generalised*: The ``except OSError`` handler around
  ``list(self._buffer.drain())`` now raises ``RuntimeError("... the off-grid buffer file
  operation failed …")`` to cover both read-path and rotation-path ``OSError`` propagation
  from the updated ``drain()``.  The previous message ("could not be read from disk")
  was inaccurate for rotation failures.

- **`TestEdgePipelineDrainBuffer` — `test_drain_buffer_raises_on_rotation_failure`**
  (`test_edge.py`): Buffers one receipt via a closed ledger; then, under a
  ``patch("sovereign_edge.buffer.os.replace", side_effect=OSError("Simulated rotation
  crash"))`` context, calls ``drain_buffer()`` and asserts :exc:`RuntimeError` matching
  ``"off-grid buffer file operation failed"`` whose ``__cause__`` is an :exc:`OSError`.
  After the patch exits, asserts that a second ``drain_buffer()`` call commits exactly
  one receipt — verifying that the rotation crash did not silently discard the buffered
  entry.  The existing test ``test_drain_buffer_raises_runtime_error_on_buffer_read_failure``
  is updated to match ``"off-grid buffer file operation failed"`` to align with the new
  message.  ``TestEdgePipelineDrainBuffer`` grows from 8 to 9 cases.
  **Suite: 87 edge tests, 376 workspace tests passed, 1 skipped (POSIX fchmod).**

- **`SensorFrame.text_content()` — float canonicalization via `_canonicalize_payload`**
  (`models.py`): A new module-level helper ``_canonicalize_payload(obj: Any) -> Any``
  traverses any arbitrarily nested JSON-compatible structure and replaces every
  :class:`float` whose :meth:`~float.is_integer` returns ``True`` with its :func:`int`
  equivalent.  Non-finite floats (``nan``, ``±inf``) pass through unchanged because
  :meth:`~float.is_integer` returns ``False`` for them, so no :func:`math.isfinite`
  guard is required.  ``text_content()`` now calls
  ``json.dumps(_canonicalize_payload(dict(self.d)), sort_keys=True, ensure_ascii=False)``
  instead of ``json.dumps(dict(self.d), ...)``.  Without this normalization, a
  MicroPython sensor serializing an integer payload field as ``1.0`` (a common JSON
  emitter difference between bare-metal runtimes and CPython) produces a canonical string
  containing ``"value": 1.0`` while the CPython edge node would produce ``"value": 1``,
  causing divergent sieve-layer inputs and breaking sieve-output determinism across
  runtime boundaries.  The HMAC-SHA256 preimage computation in ``pipeline.py`` is
  intentionally not touched: the sensor constructs its own HMAC digest from its own JSON
  serialization, so applying normalization only on the edge side would cause every
  float-bearing frame to fail verification.  The ``text_content()`` docstring is updated
  to document the float normalization contract and the cross-runtime motivation.

- **`EdgePipeline.drain_buffer()` — duplicate eviction on `sqlite3.IntegrityError`**
  (`pipeline.py`): An ``except sqlite3.IntegrityError: pass`` clause is inserted
  immediately before the existing ``except (SovereignStorageError, sqlite3.Error):
  requeue.append(...)`` handler in the inner replay loop.  When the ledger's
  ``append_receipt()`` raises :exc:`sqlite3.IntegrityError` (``UNIQUE constraint
  failed`` on the ``payload_hash`` column), the receipt has already been committed in a
  prior drain pass; re-buffering it produces an infinite replay loop where the same
  duplicate is drained, rejected, re-queued, and drained again on every subsequent
  ``drain_buffer()`` call, growing ``buffer_depth`` without bound.  The new handler
  silently evicts the duplicate (``pass``) so it is neither counted in ``committed``
  nor re-queued; ``buffer_depth`` returns to zero after a drain that encounters only
  duplicates.  :exc:`sqlite3.IntegrityError` is a subclass of :exc:`sqlite3.Error`, so
  it must be caught before the broader ``(SovereignStorageError, sqlite3.Error)`` guard
  to avoid being swallowed into the requeue path by the parent-class match.

- **`OffGridBuffer._disk_writer` — close-phase evacuation guarded by `try/finally`**
  (`buffer.py`): The ``if worker_failed:`` evacuation block that drains orphan queue
  items on worker crash previously cleared ``self._worker_running = False`` as the last
  statement inside ``with self._count_lock:`` at the end of the evacuation loop.  If the
  loop body raised an unhandled exception before reaching that statement, ``_worker_running``
  would remain ``True``; a subsequent ``close()`` call would then inject a ``None``
  sentinel into the abandoned queue (the ``_worker_running`` liveness gate passes), but no
  consumer processes it, causing any later ``queue.join()`` to block indefinitely.  The
  evacuation ``with self._count_lock:`` block is now wrapped in ``try/finally``; the
  ``finally`` clause acquires ``_count_lock`` separately, checks ``self._closed`` to drain
  any sentinel placed in the race window between the evacuation completing and
  ``_worker_running`` being cleared, and unconditionally sets ``self._worker_running =
  False``.  This guarantees the flag is cleared even when the evacuation loop raises,
  and drains any racing sentinel so ``queue.join()`` is never stalled.

- **`TestSensorFrame` — `test_text_content_normalizes_integer_valued_floats`**
  (`test_edge.py`): Constructs two ``SensorFrame`` instances from wire bytes that differ
  only in whether the ``d["value"]`` field is serialized as ``1`` (int) or ``1.0``
  (float).  Asserts that ``frame_int.text_content() == frame_float.text_content()``,
  verifying that the canonicalization helper eliminates the runtime-serialization
  discrepancy.  ``TestSensorFrame`` grows from 15 to 16 cases.

- **`TestEdgePipelineDrainBuffer` — `test_drain_buffer_evicts_duplicate_receipt_on_integrity_error`**
  (`test_edge.py`): Buffers one receipt via a closed ledger on ``pipeline_a``, then
  opens a recovery pipeline ``pipeline_b`` and patches ``append_receipt`` to raise
  :exc:`sqlite3.IntegrityError`.  After ``drain_buffer()`` returns ``[]``, asserts
  ``pipeline_b.buffer_depth == 0`` — verifying the duplicate was evicted and not
  re-queued.  Without the fix, ``buffer_depth == 1`` and the receipt re-enters the
  buffer for the next drain pass.  ``TestEdgePipelineDrainBuffer`` grows from 9 to
  10 cases.
  **Suite: 89 edge tests, 378 workspace tests passed, 1 skipped (POSIX fchmod).**

- **`OffGridBuffer` — two-phase drain with non-destructive staging rotation**
  (`buffer.py`): ``drain()`` previously atomically replaced the active buffer file
  with an empty temp file (``tempfile`` + ``os.replace``), clearing all entries from
  disk before returning them to the caller.  If the calling ``drain_buffer()`` crashed
  mid-replay (SIGKILL, power loss, unhandled exception), any entries that had been
  returned from ``drain()`` but not yet committed to the ledger were permanently lost
  — the only surviving copy was in local Python variables that were destroyed with the
  process.  The fix replaces the destructive clear with a non-destructive rename:
  ``os.replace(self._path, self._staging_path)`` atomically promotes the active buffer
  to ``{path}.staging``.  A new :meth:`commit_drain` method deletes the staging file
  once the caller confirms all entries are committed or re-queued.  If ``commit_drain``
  is never called, the staging file survives process exit and is automatically recovered
  by :meth:`_recover_staging` on the next :meth:`__init__` call (rename to active if
  only staging exists; temp-file merge if both exist).  The ``tempfile`` import is no
  longer used in ``drain()`` (only in ``_recover_staging`` for the merge branch).

- **`OffGridBuffer` — exclusive instance lock prevents concurrent path collisions**
  (`buffer.py`): A new :meth:`_acquire_buffer_lock` method, called at the end of
  ``__init__`` before the background writer starts, creates a ``.lock`` file adjacent
  to the buffer path containing the current process PID via ``open(lock_path, 'x')``
  (exclusive create).  If the lock file already exists, the owning PID is read and
  tested for liveness via ``os.kill(pid, 0)``: a stale lock (dead process) is
  overwritten; a live lock raises :exc:`RuntimeError` immediately so the caller
  discovers the path collision at construction time rather than silently interleaving
  writes and corrupting the journal.  The lock file is deleted in :meth:`close` on a
  clean shutdown (no un-journaled ``_write_errors``).  Existing write-error tests that
  removed the buffer directory via ``buf_dir.rmdir()`` are updated to
  ``(buf_dir / "buffer.jsonl.lock").unlink()`` first, since ``__init__`` now writes the
  lock file into the directory.

- **`EdgePipeline.drain_buffer()` — two-phase commit via `commit_drain()`**
  (`pipeline.py`): :meth:`commit_drain` is called on ``self._buffer`` only when the
  replay loop and re-queue pass both complete without raising.  Any exception
  propagating out of ``drain_buffer()`` — including a mid-replay crash, push failure,
  or ``OSError`` from ``drain()`` — leaves the staging file intact.  A subsequent
  ``drain_buffer()`` call (or the next process boot via ``_recover_staging``) can
  therefore recover all entries that were not yet confirmed without data loss.

- **`TestEdgePipelineDrainBuffer` — `test_drain_buffer_preserves_staging_file_on_mid_replay_crash`**
  (`test_edge.py`): Buffers two receipts via a closed ledger on ``pipeline_a``, then
  opens a recovery pipeline ``pipeline_b`` and patches ``append_receipt`` to succeed on
  the first entry and raise ``RuntimeError`` on the second.  After ``drain_buffer()``
  raises, asserts that the ``.staging`` file exists and contains both original JSONL
  lines — verifying that ``commit_drain()`` was not called on the error path.  A third
  phase calls ``drain_buffer()`` again (no patch) and asserts exactly one committed
  receipt is returned and the staging file is subsequently deleted.
  ``TestEdgePipelineDrainBuffer`` grows from 10 to 11 cases.
  **Suite: 90 edge tests, 379 workspace tests passed, 1 skipped (POSIX fchmod).**

- **`OffGridBuffer._recover_staging()` — corrupt-staging quarantine and loud failure**
  (`buffer.py`): The method previously caught ``OSError`` silently (``except OSError: pass``)
  in both the promote-only and merge branches, allowing a staging file that cannot be read
  (corrupted bytes, permission loss) to be skipped entirely.  The process would boot with the
  corrupt staging content invisible, silently losing every entry that had been serialized into
  it before the crash.  The fix restructures the method to always attempt
  ``self._staging_path.read_text(encoding="utf-8")`` first — even in the promote-only branch
  where only a rename would otherwise occur — so corruption is detected at boot time before
  any worker thread starts.  If ``read_text`` raises ``OSError`` or ``UnicodeDecodeError``,
  the staging file is quarantined by renaming it to ``{staging_path}.corrupt`` via
  ``os.replace`` (best-effort; if the rename fails the original staging file is left in place)
  and ``SovereignStorageError`` is raised immediately.  The same quarantine-and-raise pattern
  is applied when the ``os.replace`` promotion or the merge temp-file operation raises
  ``OSError`` after the staging content has been confirmed readable.  An import of
  ``SovereignStorageError`` from ``sovereign_ledger`` is added to ``buffer.py``; the package
  already depends on ``sovereign_ledger`` via ``pipeline.py`` so no new workspace dependency
  is introduced.  The ``_recover_staging`` docstring is updated with the quarantine contract
  and the observation that the process-exclusive lock file is not cleaned up on a staging
  recovery failure.

- **`EdgePipeline.drain_buffer()` — crash-recovery deduplication documented in docstring**
  (`pipeline.py`): A new paragraph is added to the ``drain_buffer()`` docstring explicitly
  documenting the ``sqlite3.IntegrityError`` eviction mechanism as the crash-recovery
  deduplication guarantee.  When ``_recover_staging()`` merges a leftover staging file back
  into the active buffer on boot, the merged active file may contain entries that were
  already committed to the ledger before the crash.  The inner replay loop's
  ``except sqlite3.IntegrityError: pass`` clause catches the ``UNIQUE constraint failed``
  error raised by ``append_receipt()`` for those entries and silently evicts them — neither
  counting them in ``committed`` nor re-queuing them — ensuring each receipt is persisted to
  the ledger exactly once across a crash-restart boundary.  No code change is required; the
  existing ``IntegrityError`` handler already provides this guarantee; the docstring addition
  makes the invariant explicit.

- **`TestOffGridBuffer` — `test_recover_staging_quarantines_corrupt_file`** (`test_edge.py`):
  Writes ``b"\xff\xfe invalid utf-8 \x80\x81"`` (invalid UTF-8) to the ``.staging`` path
  before constructing an ``OffGridBuffer`` at the same base path.  Asserts that
  ``OffGridBuffer.__init__`` raises ``SovereignStorageError``, that the ``.staging.corrupt``
  quarantine file exists at the expected path, and that the original ``.staging`` file no
  longer exists (it was renamed, not left in place).  Without the fix the constructor
  silently swallowed the ``UnicodeDecodeError`` and booted in a state where every staged
  entry was permanently invisible to subsequent drain passes.
  ``TestOffGridBuffer`` grows from 10 to 11 cases.

- **`TestEdgePipelineDrainBuffer` — `test_drain_buffer_deduplicates_on_post_crash_restart`**
  (`test_edge.py`): Full three-phase crash-restart integration test for the ``IntegrityError``
  deduplication path.  Phase 1 buffers two receipts via a closed ledger on ``pipeline_a``.
  Phase 2 opens a recovery pipeline ``pipeline_b`` and patches ``append_receipt`` to succeed on
  entry 1 and raise ``RuntimeError`` on entry 2; after ``drain_buffer()`` raises, entry 2 is
  re-queued to the active buffer and the staging file is confirmed to exist.  ``pipeline_b``'s
  buffer is closed (flushing entry 2 to disk).  Phase 3 constructs ``pipeline_c``: the
  ``__init__`` call triggers ``_recover_staging()`` which merges staging (entries 1+2) into
  active (entry 2), producing three JSONL lines.  ``drain_buffer()`` submits all three to the
  recovery ledger: entry 1 raises ``IntegrityError`` (already committed) → evicted; entry 2
  is committed; the duplicate entry 2 raises ``IntegrityError`` → evicted.  Asserts
  ``len(committed) == 1`` — exactly one new ledger row — confirming that the
  ``IntegrityError`` eviction mechanism prevents a crash-restart cycle from persisting the
  same receipt more than once.  ``TestEdgePipelineDrainBuffer`` grows from 11 to 12 cases.
  **Suite: 92 edge tests, 381 workspace tests passed, 1 skipped (POSIX fchmod).**

- **`OffGridBuffer.__init__()` — lock file cleanup on staging recovery failure** (`buffer.py`):
  After `_acquire_buffer_lock()` writes the ``.lock`` file, ``_recover_staging()`` is now
  called inside a ``try/except BaseException:`` block whose handler unlinks the ``.lock``
  file before re-raising the original exception.  Previously, any exception raised by
  ``_recover_staging()`` — most critically the new ``SovereignStorageError`` quarantine path —
  left the ``.lock`` file on disk holding the current process PID.  A subsequent construction
  attempt on the same path would call ``_acquire_buffer_lock()``, discover a live PID (the
  same process is still running), and raise ``RuntimeError``, permanently locking the buffer
  path for the lifetime of the process.  The ``try/except BaseException:`` scope correctly
  captures ``KeyboardInterrupt`` and other ``BaseException`` subclasses that can occur during
  object construction in addition to ``SovereignStorageError``.  The ``_recover_staging()``
  ``:raises SovereignStorageError:`` docstring is updated to note that ``__init__`` now
  performs unconditional lock cleanup before re-raising.

- **`TestOffGridBuffer` — `test_init_cleans_up_lock_on_staging_recovery_failure`**
  (`test_edge.py`): Writes invalid UTF-8 bytes to the ``.staging`` path (triggering
  ``SovereignStorageError`` from ``_recover_staging()``), asserts that ``OffGridBuffer.__init__``
  raises, and asserts that the ``.lock`` file does not exist after the failed construction.
  Then removes the quarantined ``.staging.corrupt`` file and confirms a second
  ``OffGridBuffer`` construction on the same path succeeds and reports ``size == 0``,
  verifying that the cleaned-up lock actually unblocks future retry attempts.  Without the
  fix, the second construction raises ``RuntimeError("already held by process …")``.
  ``TestOffGridBuffer`` grows from 11 to 12 cases.
  **Suite: 93 edge tests, 382 workspace tests passed, 1 skipped (POSIX fchmod).**

- **`OffGridBuffer._disk_writer` — evacuation `finally` block: `_pending` decremented for
  racing-close sentinel** (`buffer.py`): In the crash-evacuation path, the ``finally`` block
  acquires ``_count_lock`` and checks ``self._closed`` to drain any ``None`` sentinel injected
  by a concurrent ``close()`` call — consuming it via ``get_nowait()`` / ``task_done()``.  The
  drain loop was missing ``self._pending -= 1`` before each ``task_done()``, leaving the counter
  inflated by one for every sentinel consumed in that path.  With the missing decrement, a
  ``close()`` call racing with the crash-evacuation sweep placed a sentinel that was physically
  drained from the queue but not reflected in ``_pending``; the counter remained ``> 0`` after
  the thread exited, causing any subsequent ``queue.join()`` (via ``flush()``) to block
  indefinitely.  The fix adds ``self._pending -= 1`` inside the ``with self._count_lock: if
  self._closed:`` drain loop so each sentinel consumed in the ``finally`` block is fully
  accounted for, matching the decrement semantics of the main evacuation loop above it.

- **`EdgePipeline.process()` — `sqlite3.IntegrityError` evicted as duplicate, not buffered**
  (`pipeline.py`): ``sqlite3.IntegrityError`` is a subclass of ``sqlite3.Error``, so a duplicate
  submission — where ``append_receipt()`` raises ``IntegrityError`` because the ``payload_hash``
  already occupies a ``UNIQUE`` ledger slot — was silently caught by the broader
  ``except (SovereignStorageError, sqlite3.Error):`` guard and routed to the off-grid buffer with
  ``buffered=True``.  This diverged from the ``drain_buffer()`` contract, where ``IntegrityError``
  triggers silent eviction (``except sqlite3.IntegrityError: pass``) rather than re-queuing.  A
  new ``except sqlite3.IntegrityError:`` clause inserted before the broader guard extracts
  ``payload_hash`` from the receipt dict and leaves ``buffered = False``, matching the
  drain-buffer silent-eviction semantics.  ``process()`` docstring updated with an explicit
  paragraph describing the eviction contract.

- **`TestEdgePipelineProcess` — `test_process_evicts_duplicate_submission_without_buffering`**
  (`test_edge.py`): Patches ``mem_ledger.append_receipt`` with ``sqlite3.IntegrityError``; asserts
  ``result.buffered is False`` and ``edge_pipeline.buffer_depth == 0``.  Verifies that a duplicate
  direct submission returns the same non-buffered disposition as the ``drain_buffer()`` replay
  eviction path, closing the contract gap.
  ``TestEdgePipelineProcess`` grows from 18 to 19 cases.

- **`TestOffGridBufferWriteErrors` — `test_crash_evacuation_racing_close_leaves_pending_at_zero`**
  (`test_edge.py`): Triggers a non-OSError worker crash via a patched ``builtins.open`` that raises
  ``RuntimeError`` on append-mode opens; waits for ``worker_failed`` to become ``True``; then
  launches 8 concurrent ``close()`` threads to race with the evacuation ``finally`` block; asserts
  all threads join within 5 seconds (timeout = deadlock sentinel) and ``buf._pending == 0`` after
  completion.  Directly validates that the missing ``self._pending -= 1`` fix prevents counter drift
  from an unaccounted sentinel, confirming ``flush()`` → ``queue.join()`` can complete without
  stalling.
  ``TestOffGridBufferWriteErrors`` grows from 11 to 12 cases.
  **Suite: 95 edge tests, 384 workspace tests passed, 1 skipped (POSIX fchmod).**

- **`EdgePipeline.__init__()` — secret validation moved before `OffGridBuffer` construction**
  (`pipeline.py`): ``self._sensor_secret`` is now computed and the
  ``SovereignConfigurationError`` guard fires as the very first action in ``__init__``,
  before ``OffGridBuffer(buffer_path)`` is called.  Previously, the buffer was constructed
  first; any subsequent ``SovereignConfigurationError`` propagated with the buffer's
  ``.lock`` file already written to disk, permanently blocking every subsequent
  construction attempt on the same path for the process lifetime.  The temporary
  ``_sensor_secret`` local is computed, validated, and then assigned to
  ``self._sensor_secret`` after the buffer and key manager are safely constructed.
  ``:raises SovereignConfigurationError:`` docstring updated to state that the validation
  fires before buffer construction.
  ``test_configuration_error_does_not_create_buffer_lock_file``
  (``TestEdgePipelineSecureInit``): asserts that no ``.lock`` file exists after
  ``SovereignConfigurationError`` propagates from ``__init__``, then confirms a second
  construction attempt on the same path succeeds.
  ``TestEdgePipelineSecureInit`` grows from 4 to 5 cases.

- **`EdgePipeline.drain_buffer()` — `flush()` before `commit_drain()` for re-queue
  durability** (`pipeline.py`): After the re-queue pass pushes entries back into the
  off-grid buffer, ``self._buffer.flush()`` is now called before
  ``self._buffer.commit_drain()``.  This blocks until the background writer thread has
  fsync'd every re-queued entry to the active JSONL file.  Without the flush, there is a
  window between the successful ``push()`` calls (which only enqueue entries) and the
  ``commit_drain()`` that deletes the staging file; a process exit in that window leaves
  re-queued entries only in the in-memory queue with no on-disk copy and no staging file
  to recover from.  The ``drain_buffer()`` two-phase-commit docstring paragraph is updated
  to document the flush guarantee.

- **`SensorFrame.text_content()` — compact separator added; `EdgePipeline.process()` uses
  `frame.text_content()` for HMAC preimage canonical** (`models.py`, `pipeline.py`):
  ``text_content()`` previously called ``json.dumps(...)`` without ``separators=(",",
  ":")``, producing spaced output (``{"sensor": "temperature", ...}``).  The sensor's
  ``SovereignEnvelope.seal()`` uses ``separators=(",", ":")`` for the HMAC preimage
  canonical, producing compact output (``{"sensor":"temperature",...}``).  This divergence
  meant that using ``frame.text_content()`` as the edge-side HMAC canonical would always
  produce a digest mismatch.  ``separators=(",", ":")`` is now added to ``text_content()``
  to align its output with the sensor's canonical form.  ``pipeline.process()`` then
  replaces the inline ``json.dumps(dict(frame.d), ...)`` with ``frame.text_content()``
  so the same normalized, compact canonical function is used for both the HMAC preimage
  and the sieve-layer input, eliminating the dual-path divergence.  The now-unused
  ``import json`` is removed from ``pipeline.py``.  ``text_content()`` docstring updated to
  document compact separators and dual-purpose use.
  **Suite: 96 edge tests, 385 workspace tests passed, 1 skipped (POSIX fchmod).**

- **`SensorFrame.text_content()` — float literal preservation: `_canonicalize_payload` removed**
  (`models.py`): The ``_canonicalize_payload`` helper that coerced every ``float`` whose
  ``is_integer()`` returned ``True`` to its ``int`` equivalent is removed, and its call in
  ``text_content()`` is replaced with a direct ``dict(self.d)`` pass to ``json.dumps``.
  The prior normalization (``1.0 → 1``) produced a canonical string that diverged from the
  literal format the sensor embedded in its own HMAC preimage: a sensor that serialized
  ``{"value": 1.0}`` signed ``'{"value":1.0}'``, but the edge node produced ``'{"value":1}'``,
  causing HMAC-SHA256 verification to fail deterministically for every float-bearing payload.
  Preserving the exact float literal ensures the edge-side preimage is byte-identical to the
  sensor's signed string.  ``test_text_content_normalizes_integer_valued_floats`` is renamed
  ``test_text_content_preserves_float_literal_format`` and its assertion is inverted: it now
  asserts that ``"1.0"`` appears in the float frame's ``text_content()`` output and that
  ``frame_float.text_content() != frame_int.text_content()``, verifying float fidelity rather
  than normalization equality.

- **`OffGridBuffer._disk_writer` — write errors persisted to disk-backed quarantine log**
  (`buffer.py`): When the background writer raises :exc:`OSError` during a file write or
  ``fsync``, the raw JSON entry is now appended to ``{path}.quarantine`` via ``open("a")`` +
  ``fsync`` before the entry is recorded in ``_write_errors``.  The quarantine write is
  best-effort: a nested ``except OSError: pass`` ensures that a quarantine-write failure (e.g.
  the disk is genuinely full) does not mask the original error.  ``_write_errors`` continues to
  serve as the in-session in-memory record, so ``drain()``, ``size``, ``write_error_count``,
  and ``close()`` semantics are unchanged.  A new ``_load_quarantine()`` method is called from
  ``__init__`` after ``_recover_staging()`` returns and before the background writer thread is
  started; it reads ``{path}.quarantine`` if present, deserializes each line into a
  ``(receipt_dict, sieved_content)`` tuple, and appends valid entries to ``_write_errors``
  (malformed lines go to ``_dead_letter``).  ``drain()`` unlinks the quarantine file after
  clearing ``_write_errors`` on both the empty-buffer early-return path and the normal
  completion path, so a successful drain consumes both the in-memory and on-disk write-error
  records atomically.  Together these changes eliminate the data-loss window where a
  write-failed receipt existed only in the in-memory ``_write_errors`` list and was
  permanently lost if the process was killed before ``drain()`` was called.

- **`OffGridBuffer._acquire_buffer_lock()` — atomic OS-level lock creation via `os.open`**
  (`buffer.py`): The ``open(self._lock_path, "x", encoding="utf-8")`` exclusive-create call
  is replaced with
  ``os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)``.  Both are backed
  by the same ``O_CREAT | O_EXCL`` kernel semantics, but the raw ``os.open`` call makes the
  OS-level atomic guarantee explicit: a single syscall either creates the file and returns an
  fd, or raises ``FileExistsError`` — two concurrent ``OffGridBuffer`` constructions on the
  same path cannot both receive a successful fd.  The fd is used to write the PID bytes via
  ``os.write`` and then immediately closed via ``os.close`` in a ``try/finally`` block, so
  external callers (including test teardown code that unlinks the lock file to allow
  ``rmdir``) can access the file without contention.  The stale-lock detection and override
  paths (reading the incumbent PID, probing liveness via ``os.kill``) are unchanged.

- **`EdgePipeline.__init__()` — buffer closed on post-buffer constructor failure**
  (`pipeline.py`): All initialization steps that follow ``OffGridBuffer(buffer_path)``
  (key path resolution, ``mkdir``, ``chmod``, and ``SovereignKeyManager`` construction) are
  now wrapped in a ``try/except BaseException`` block.  On any exception, ``self._buffer.close()``
  is called (with ``except Exception: pass`` suppressing any close error so it cannot mask the
  original exception) before re-raising.  Previously, a failure in any of those steps left the
  ``OffGridBuffer`` daemon thread running and the ``.lock`` file on disk with no owning
  ``EdgePipeline`` instance — every subsequent construction attempt on the same ``buffer_path``
  would then raise ``RuntimeError("already held by process …")`` for the remainder of the
  process lifetime.  ``test_init_failure_after_buffer_creation_closes_worker_thread``
  (``TestEdgePipelineSecureInit``) patches ``sovereign_edge.pipeline.SovereignKeyManager`` to
  raise ``RuntimeError`` and asserts that the ``.lock`` file is absent after the failed
  constructor, confirming the thread was joined and the lock unlinked.
  ``TestEdgePipelineSecureInit`` grows from 5 to 6 cases.

- **`OffGridBuffer._acquire_buffer_lock()` — `PermissionError` and unexpected `OSError` raise `SovereignStorageError`**
  (`buffer.py`): Three permission-boundary guards are added across the lock acquisition sequence:
  (1) an ``except OSError`` clause after the existing ``except FileExistsError`` in the initial
  ``os.open(O_CREAT | O_EXCL | O_WRONLY)`` block converts any kernel rejection that is not a
  simple file-already-exists condition (e.g., ``PermissionError`` on a read-only directory) into
  ``SovereignStorageError`` rather than propagating the raw ``OSError``;
  (2) an ``except PermissionError`` clause placed before ``except (OSError, ValueError)`` in the
  held-PID read block prevents a permission-denied read from being misclassified as a stale lock
  and overwriting a live foreign-owner lock file;
  (3) an ``except PermissionError`` clause placed before ``except OSError`` in the
  ``os.kill(held_pid, 0)`` block prevents a permission-denied kill — which indicates the owner
  process IS alive but belongs to a different user — from being silently treated as a dead
  process and triggering a lock steal.  In all three cases ``SovereignStorageError`` is raised
  with the message ``"Lock file acquisition failed due to permission or system boundaries"`` and
  the original OS exception chained as ``__cause__``.  The ``_acquire_buffer_lock`` docstring
  is updated to declare the new ``:raises SovereignStorageError:`` condition.

- **`OffGridBuffer.drain()` — write-error clear bounded to snapshot count**
  (`buffer.py`): ``drain()`` now records ``_error_snapshot_count = len(self._write_errors)``
  atomically alongside the ``pending_error_entries`` snapshot under ``_count_lock``.  On the
  success path the former ``self._write_errors.clear()`` is replaced with
  ``del self._write_errors[:_error_snapshot_count]`` in both the file-absent early-return branch
  and the normal ``os.replace`` completion branch.  This bounds the clear to exactly the entries
  that were visible at snapshot time: any entry appended to ``_write_errors`` by the background
  writer after the snapshot boundary (theoretically possible between ``flush()`` return and
  ``_count_lock`` re-acquisition) survives into the next drain pass rather than being silently
  discarded.  If ``os.replace`` raises ``OSError``, the early-exit path is taken before any
  ``del`` statement executes, so the full ``_write_errors`` list remains intact — no write-error
  entry is cleared or orphaned by a failed file swap.  The ``drain()`` docstring is updated to
  describe the snapshot-bounded removal contract.

- **`OffGridBuffer.close()` — lock file unlinked unconditionally in `finally` block**
  (`buffer.py`): The ``self._lock_path.unlink()`` call is moved from after the write-error
  check into a ``finally`` block that wraps the entire check-and-raise sequence.  Previously,
  if ``_write_errors`` was non-empty the ``RuntimeError`` propagated before ``unlink()`` was
  reached, leaving the ``.lock`` file on disk after the background thread had already been
  joined and exited.  Any subsequent ``OffGridBuffer`` construction on the same path would then
  raise ``RuntimeError("already held by process …")`` permanently, even though no live worker
  owned the lock.  The ``finally`` guarantee ensures the lock is released whether ``close()``
  returns normally or raises, so the path is immediately available for a new construction
  attempt after a faulted shutdown.  The ``close()`` docstring is updated with a paragraph
  describing this guarantee and the ``:raises RuntimeError:`` entry is annotated to clarify
  that the ``.lock`` file is unlinked before the exception propagates.

- **`packages/sovereign-edge/README.md` — Quick Start snippet updated for secure-by-default contract**
  (`README.md`): The ``EdgePipeline`` constructor call in the Quick Start block previously omitted
  ``sensor_secret``, which now raises ``SovereignConfigurationError`` at runtime under the
  secure-by-default policy.  ``sensor_secret=b"<shared-hmac-secret>"`` is added to the
  constructor call and a preceding comment explains that ``allow_unauthenticated=True`` is the
  explicit opt-out when HMAC-SHA256 frame verification is intentionally not required.

- **Root `README.md` — `sovereign-edge` sensor_secret comment corrected**
  (`README.md`): The inline comment ``# omit to disable inbound verification`` on the
  ``sensor_secret`` parameter is replaced with
  ``# required; pass allow_unauthenticated=True to opt out``, accurately reflecting that
  omitting ``sensor_secret`` without the explicit opt-out flag raises
  ``SovereignConfigurationError`` rather than silently disabling verification.
  **Suite: 97 edge tests, 386 workspace tests passed, 1 skipped (POSIX fchmod).**

- **Phase 9 — `sovereign-sensor` bare-metal Write-Side Custody sensor layer** (new workspace
  member `packages/sovereign-sensor/`): Introduces a MicroPython-compatible HAL for sealing
  sensor observations into versioned, tamper-evident, minified JSON transmission envelopes with
  monotonic replay protection at the exact point of data genesis on bare-metal microcontrollers
  (ESP32, Raspberry Pi Pico).  Zero runtime dependencies; internal library code restricted to
  standard MicroPython built-ins (`json`, `sys`, `machine`, `hashlib`, `hmac`, `binascii`).

  - **`SovereignCryptoDriver`** (`interface.py`): Lightweight HAL base class with explicit
    `NotImplementedError` stubs for `initialize_hardware() -> None`,
    `sign(payload: bytes) -> bytes`, and `algorithm() -> str`.  The `algorithm()` method
    enforces protocol agility by requiring every concrete driver to declare its signing primitive
    as a canonical identifier string (e.g. `"hmac-sha256"`, `"ecdsa-p256"`), which is embedded
    in the authenticated preimage and the wire frame.  Intentionally avoids the standard-library
    `abc` module to remain compatible with constrained MicroPython heap environments.
  - **`SovereignEnvelope`** (`envelope.py`): Accepts a `sequence_file` VFS path (default
    `".sovereign_sequence"`) and restores any previously persisted counter from that file on
    construction, enabling the monotonic sequence to resume across hardware reboots.  Seals
    observations in a seven-step deterministic pipeline: (1) per-instance monotonic sequence
    counter computed transiently as `_sequence + 1` and bound into the preimage; the in-memory
    counter and VFS sequence file are not advanced until `sign()` returns successfully, so a
    `sign()` failure never consumes a sequence position or introduces a gap in the on-disk custody
    timeline (degrades gracefully to RAM-only tracking on VFS write failure); (2) algorithm
    identifier queried from driver via `algorithm()`; (3) payload keys alphabetically sorted and
    serialized via `json.dumps(..., separators=(',', ':'), sort_keys=True, ensure_ascii=False)`,
    guaranteeing byte-identical raw UTF-8 preimage bytes regardless of dict key insertion order or
    non-ASCII payload characters across bare-metal MicroPython targets and desktop gateways;
    (4) versioned preimage constructed as
    `1|{len(node_bytes)}:{node_id}|{len(time_bytes)}:{timestamp}|{seq}|{len(algo_bytes)}:{algorithm}|{canonical}`,
    where each variable-length field is prefixed with its UTF-8 byte count (not Unicode character
    count), binding protocol version, identity, time, ordering, and algorithm into a single
    unambiguous signed surface with no delimiter injection surface; (5) preimage dispatched across
    the driver's `sign()` boundary, returning raw binary bytes; (6) raw bytes hex-encoded via
    `binascii.hexlify`, guaranteeing all values `0x00–0xFF` map safely without `UnicodeDecodeError`
    on MicroPython silicon; (7) seven-key frame `{"v":1, "n", "t", "q", "alg", "d", "s"}`
    serialized to ultra-minified UTF-8 JSON bytes via
    `json.dumps(..., sort_keys=True, ensure_ascii=False)`, freezing the alphabetical key sequence
    and enforcing raw UTF-8 wire encoding independently of MicroPython allocator-driven insertion
    order.
  - **`bootstrap_sensor_node(node_id, private_key_path, sequence_file) -> SovereignEnvelope`**
    (`__init__.py`): Inspects `sys.platform.lower()` at runtime; routes to `ESP32HardwareDriver`
    when `"esp32"` is present in the platform string, otherwise binds `SoftwareFallbackDriver`.
    Calls `initialize_hardware()` inside a `try/except NotImplementedError` block — when the
    selected hardware driver raises (skeleton placeholder not yet implemented), a warning is
    printed and the factory rebinds to `SoftwareFallbackDriver` so sealing, sequencing, and VFS
    layers remain exercisable on the workbench before register-level engineering is complete.
    Accepts an optional `sequence_file` path (default `".sovereign_sequence"`) forwarded to
    `SovereignEnvelope` for VFS-based counter persistence across reboots.
  - **`SoftwareFallbackDriver`** (`drivers/software_fallback.py`): HMAC-SHA256 keyed signing
    driver using `hashlib` and `hmac`.  On `initialize_hardware()`, checks whether
    `private_key_path` equals `_MOCK_KEY_SENTINEL = "/mock/test_gateway.key"` — if so,
    substitutes the fixed deterministic `_MOCK_KEY` stub so desktop CI tests operate without
    a provisioned key store; for all other paths, opens the file in read-binary mode and
    re-raises any `OSError` as `RuntimeError`, eliminating the prior silent key substitution
    defect where any absent file silently resolved to the mock key.  Returns raw 32-byte
    HMAC-SHA256 digest bytes (no encoding applied; hex encoding is the envelope layer's
    exclusive responsibility).  Declares `algorithm() -> "hmac-sha256"`.
    Not intended for production custody chains.
  - **`ESP32HardwareDriver`** (`drivers/esp32_hardware.py`): v0.1 HAL skeleton class
    establishing the class contract and import surface for the ESP32 on-chip ECC accelerator
    via MicroPython `machine` and `hashlib` HAL bindings.  Declares `algorithm() -> "ecdsa-p256"`
    as a forward-looking identifier for the hardware signing primitive.  `sign()` raises
    `NotImplementedError`; full register-level engineering is deferred to the next sprint.
    This driver must not be wired into any production custody chain in its current state.

  - **`packages/sovereign-sensor/README.md`** — new distribution documentation asset satisfying
    the `pyproject.toml` `readme` field; includes a HAL architecture section, a driver
    comparison table, the 7-step sealing pipeline with preimage format specification, minimal
    `bootstrap_sensor_node()` usage examples for both production and desktop/CI paths, and a
    package invariants table.

  - **`packages/sovereign-sensor/tests/test_sensor.py`** — 33 test cases across three classes
    (`TestBootstrap`: 4 cases; `TestDriverGuard`: 3 cases; `TestEnvelopeSeal`: 26 cases)
    verifying: platform auto-detection confirmed via the public `algorithm()` contract (sealed
    `"alg"` field equals `"hmac-sha256"`) rather than private attribute access; driver
    initialization confirmed via successful `seal()` completion; bootstrap falls back to
    `SoftwareFallbackDriver` (with printed warning) when the selected hardware driver raises
    `NotImplementedError` (simulated by patching `sys.platform` to `"esp32"`), confirming the
    workbench fallback contract; `sign()` raises `RuntimeError` when called before
    `initialize_hardware()`, preventing silent keyless HMAC packets from bad setup sequencing;
    `initialize_hardware()` raises `RuntimeError` for any non-sentinel path that cannot be
    opened, eliminating the prior silent `_MOCK_KEY` substitution defect for production key
    paths; `initialize_hardware()` raises `ValueError` when the key file exists but contains zero
    bytes, preventing HMAC keyed with `b""` from producing authentication tags with no
    cryptographic uniqueness; `seal()` returns `bytes`; output parses as valid JSON; transmission
    envelope contains exactly the seven keys `v`, `n`, `t`, `q`, `alg`, `d`, `s`; `v` is
    integer `1`; `n` and `t` are preserved verbatim; `q` starts at `1` on the first call
    (isolated via `tmp_path` sequence file); three consecutive calls on the same instance produce
    `q` values `[1, 2, 3]` (monotonic increment, isolated via `tmp_path`); `"alg"` equals
    `"hmac-sha256"`; payload dict round-trips without mutation; signature is a non-empty ASCII
    string; software driver signature is exactly 64 hex characters (HMAC-SHA256 digest); output
    is ultra-minified (no whitespace after separators); two independent fresh instances produce
    byte-identical first seals for identical inputs (cross-instance determinism); distinct
    payloads at the same sequence position produce distinct signatures; `"s"` field contains only
    characters from the set `{0–9, a–f}`; `sort_keys=True` canonicalization produces
    byte-identical signatures for semantically equivalent payloads with inverted key insertion
    order, proving preimage invariance across all MicroPython targets; full raw wire-frame byte
    arrays are identical for semantically equivalent payloads with inverted key insertion order,
    proving transport-layer byte determinism independent of allocator-driven dict ordering; HMAC
    signatures diverge when distinct key files supply different secret material (key material
    participation); a 0-byte sequence file (power-loss truncation artifact) is caught as
    `ValueError` and resets the counter to 0 without aborting device initialization; sequence
    counter correctly resumes from the persisted VFS value after a simulated hardware reboot
    (replay-protection continuity); length-prefixed preimage delimiter injection immunity —
    `node_id="abc|def"` with `timestamp="ghi"` produces a distinct HMAC from `node_id="abc"`
    with `timestamp="def|ghi"`, confirming cross-identity preimage collision is eliminated;
    non-ASCII node_id byte-length prefix integrity — `"noëud"` (5 Unicode chars, 6 UTF-8 bytes)
    seals correctly and the sealed signature matches an independently computed HMAC over the
    byte-count-prefixed preimage, proving that character-count semantics are rejected and
    cross-platform field boundary deserialization is correct on all MicroPython targets;
    negative sequence counter clamp — `"-42"` written to the sequence file produces
    `_sequence == 0` after construction and `q=1` on the first `seal()`, confirming that
    adversarially written or filesystem-corrupted negative counter values are neutralized
    before the monotonic custody chain begins; algorithm identifier byte-count-prefix
    delimiter injection immunity — two drivers returning `"hmac|sha256"` and `"hmac"` produce
    distinct HMAC signatures confirming cross-algorithm preimage collision is closed; an
    independently reconstructed HMAC over the byte-count-prefixed preimage exactly matches
    the sealed signature, verifying the complete preimage format end-to-end; Unicode payload
    serialization without ASCII escaping — `{"sensor": "Nordøst-Ventil", "data": "Ω-Value"}`
    seals without error, the payload round-trips correctly, and the wire bytes contain raw
    UTF-8 characters (`ø`, `Ω`) with no `\uXXXX` escape sequences, confirming
    `ensure_ascii=False` is active on both the preimage and wire frame serialization paths;
    preimage reconstruction tests refactored to use the public `SoftwareFallbackDriver.sign()`
    API rather than accessing the private `_MOCK_KEY` attribute directly, eliminating all
    private-attribute access from the test suite.
    **33 passed, 0 failed.**

- **Phase 8 — `sovereign-ledger` immutable provenance engine** (new workspace member
  `packages/sovereign-ledger/`): Introduces a local-first, SQLite-backed, append-only
  audit ledger that enforces Write-Side Custody for all `ForensicReceipt` transactions.
  The engine stores receipts in a SHA-256 hash-chained table and makes any historical
  tampering mathematically detectable via `verify_ledger_integrity()`.

  - **`SovereignLedger`** (`engine.py`): Zero-external-dependency class backed by the
    Python standard library `sqlite3` module.  Applies WAL journal mode, `NORMAL`
    synchronous enforcement, and strict foreign-key locks as connection pragmas at
    initialization.  Creates the `forensic_ledger` table with nine typed columns
    (`id`, `payload_hash` UNIQUE, `parent_hash`, `timestamp`, `sieved_content`,
    `signature`, `raw_token_count`, `optimized_token_count`,
    `tax_savings_percentage`) and installs two engine-level `BEFORE UPDATE` /
    `BEFORE DELETE` SQL triggers that call `RAISE(ROLLBACK, 'Write-Side Custody
    violation: ...')`, aborting any mutation attempt and rolling back the entire
    enclosing transaction regardless of which SQLite client opens the file.
  - **`append_receipt(receipt, sieved_content) -> str`**: Derives a rolling SHA-256
    `parent_hash` chained from the immediately preceding row's `signature`,
    `payload_hash`, and `parent_hash` (or the static genesis constant
    `SHA-256("SOVEREIGN_LEDGER_GENESIS_BLOCK_v1.0")` for the first entry) before
    executing an atomic `INSERT`.  Prose Tax token-economy metrics are extracted from
    `receipt["metadata"]["prose_tax_summary"]` when present; the three metric columns
    store `NULL` when the summary key is absent.  Returns the `payload_hash` as an
    opaque receipt identifier.  No update or deletion methods are exposed.
  - **`verify_ledger_integrity() -> bool`**: O(n) cursor sweep that re-derives the
    expected `parent_hash` for every row from its predecessor, returning `False` on
    the first chain break.  Detects row field mutation, mid-chain row deletion that
    collapses the sequence, and injected rows whose `parent_hash` does not match the
    re-derived value.

  - **`packages/sovereign-ledger/tests/test_ledger.py`** — 60 test cases across seven
    classes (`TestSovereignLedgerInit`, `TestAppendReceipt`, `TestHashChain`,
    `TestImmutabilityTriggers`, `TestVerifyLedgerIntegrity`, `TestEdgeCases`,
    `TestConcurrentAppend`) covering schema assertion, WAL mode verification,
    hash-chain arithmetic from the genesis root through multi-row sequences,
    determinism across independent instances, adversarial trigger tests using both the
    ledger's own connection and an external raw `sqlite3` client connection,
    `RAISE(ROLLBACK)` transaction-abort semantics verifying that an INSERT staged
    inside an explicit `BEGIN` is rolled back when the trigger fires (closing post-hoc
    injection via a subsequent `COMMIT`), out-of-band signature/payload/parent-hash
    corruption detection, mid-chain deletion detection, fabricated-row injection
    detection, Unicode payload round-trip, duplicate `payload_hash` rejection,
    close-and-reopen lifecycle correctness, connection-registry purge verification
    (`close()` releases all thread-local handles and empties the registry to zero),
    `test_out_of_band_tax_savings_percentage_tamper_breaks_chain` covering the
    remaining column isolation gap, concurrent write stress tests
    (`TestConcurrentAppend`) covering separate-instance and shared-instance
    file-backed scenarios (8-thread chain-linearity assertions) plus a
    shared-instance in-memory scenario validating per-thread DDL bootstrap
    correctness (each thread receives an independent isolated SQLite in-memory
    store; writes are not aggregated into a single unified chain),
    closed-instance lifecycle hardening verifying that `SovereignStorageError`
    is raised immediately on any post-`close()` access, and
    `test_in_memory_cross_thread_emits_runtime_warning` asserting that `_get_conn()`
    emits a `RuntimeWarning` when a worker thread opens a new in-memory connection
    on a shared instance created by a different thread, confirming the per-thread
    isolation advisory reaches callers at the correct callsite.

- **Phase 7 — `sovereign-sieve` standalone micro-utility package** (new workspace member
  `packages/sovereign-sieve/`): Extracted `sovereign-sieve` into a zero-dependency
  standalone micro-utility package for framework-agnostic Prose Tax reduction.  The
  package exposes two public functions and one structured result type:

  - **`pure_sieve(payload: str) -> str`**: Pure synchronous string cleaner applying
    the full Prose Tax filler-phrase regex library (greeting tokens, hedging adverbs,
    affirmation filler, preamble phrases, degenerate markdown runs, duplicate headings)
    with post-substitution whitespace normalization.  No I/O, no shared state, no web
    framework or ML library import.  Safe for scripts, batch pipelines, and offline
    data-processing workers.
  - **`sieve_with_metrics(payload: str) -> SieveOutput`**: Identical sieve pass that
    additionally returns `raw_token_count`, `optimized_token_count`, and
    `tax_savings_percentage` in a `SieveOutput` dataclass — all computed via the
    UTF-8 byte-density heuristic (÷ 4) without invoking any external tokenizer or
    network service.
  - **`SieveOutput` dataclass**: Structured result with `text`, `raw_token_count`,
    `optimized_token_count`, and `tax_savings_percentage` (rounded to four decimal
    places) fields.

- **`packages/sovereign-sieve/tests/test_sieve.py`** — 67 determinism, filler-stripping,
  edge-case, and metrics-arithmetic test cases across nine classes
  (`TestPureSieveGreetingStripping`, `TestPureSieveHedgingAdverbs`,
  `TestPureSieveAffirmationFiller`, `TestPureSievePreamblePhrases`,
  `TestPureSieveMarkdownCleaning`, `TestPureSieveWhitespaceNormalization`,
  `TestPureSieveDeterminism`, `TestPureSieveMalformedInputs`, `TestSieveWithMetrics`).
  Covers determinism invariant, idempotency, Unicode payloads, very long inputs,
  `TypeError` on non-`str` arguments, and internal savings-percentage arithmetic
  consistency.

### Changed

- **`SovereignEnvelope.seal()` — `ensure_ascii=False` enforced on both `json.dumps()` calls** (`envelope.py`):
  `ensure_ascii=False` is now passed explicitly to the `json.dumps()` call that produces the
  canonical payload preimage string and to the final `json.dumps()` that serializes the wire
  frame.  CPython's default `ensure_ascii=True` escapes every character outside U+007F to a
  six-byte `\uXXXX` sequence, while many MicroPython builds emit the raw multi-byte UTF-8
  encoding for the same character.  For a payload containing `"Ω"` (U+03A9), CPython would
  produce `"Ω"` in the canonical string and MicroPython would produce the raw bytes
  `\xce\xa9`, yielding different HMAC preimages and causing cross-platform signature
  verification to fail silently on any non-ASCII payload.  `ensure_ascii=False` forces both
  runtimes to the same raw UTF-8 output, eliminating the split-brain encoding divergence
  at the preimage and wire frame layers simultaneously.

- **`SovereignEnvelope.seal()` — sequence counter commit deferred to after signing** (`envelope.py`):
  The sequence index is now computed transiently as `next_sequence = self._sequence + 1`
  before the preimage is assembled.  `self._sequence` is not mutated and the VFS sequence
  file is not written until after `self._driver.sign()` returns without raising.  Previously
  the counter was incremented and flushed to the VFS immediately at the start of `seal()`:
  a `sign()` failure (e.g. `RuntimeError` from a driver that was never initialized) would
  permanently consume a sequence position, introducing a numbering gap in the on-disk
  custody timeline that a downstream verifier would flag as a replay-protection breach.
  With the deferred commit, a signing failure propagates cleanly and the counter is not
  advanced, keeping the persistent sequence file in perfect sync with actually-sealed frames.

- **`tests/test_sensor.py` — private `_MOCK_KEY` attribute access eliminated** (`tests/test_sensor.py`):
  Two tests that reconstructed the expected HMAC preimage signature by directly reading
  `SoftwareFallbackDriver._MOCK_KEY` bytes and calling `hmac.new()` inline are refactored
  to use only the public driver API.  `test_seal_non_ascii_node_id_uses_utf8_byte_length_prefix`
  and `test_algo_field_length_prefix_closes_preimage_delimiter_collision` now construct a
  reference `SoftwareFallbackDriver(MOCK_KEY_SENTINEL)`, call `initialize_hardware()`, and
  delegate to the public `sign()` method to compute the expected signature.  The `_FixedAlgoDriver`
  mock in the algo-collision test holds its own `SoftwareFallbackDriver(MOCK_KEY_SENTINEL)` reference
  initialized in `__init__` and forwards all `sign()` calls to it, removing the last direct dependency
  on a private attribute from the test file.  `ensure_ascii=False` is added to the `json.dumps()`
  calls in both tests' canonical reconstruction to match the updated envelope implementation.

- **`SovereignEnvelope.seal()` — algorithm identifier uniformly byte-count-prefixed in the preimage** (`envelope.py`):
  `algorithm` is now encoded to a UTF-8 byte array independently and prefixed with its byte
  count before insertion into the versioned preimage, bringing it into parity with `node_id`
  and `timestamp`.  The preimage format is
  `1|{len(node_bytes)}:{node_id}|{len(time_bytes)}:{timestamp}|{seq}|{len(algo_bytes)}:{algorithm}|{canonical}`.
  Without the prefix, a driver returning an algorithm identifier containing `|` (e.g.
  `"hmac|sha256"`) could produce a preimage byte sequence that an alternative `(node_id,
  algorithm)` split reconstructs identically, enabling cross-algorithm signature reuse.
  Named variables `node_prefix`, `time_prefix`, and `algo_prefix` are assembled from the
  corresponding byte arrays and fed into a single f-string that is encoded to UTF-8 at the
  end, keeping the assembly logic readable and the encoding boundary explicit.

- **`SoftwareFallbackDriver.MOCK_KEY_SENTINEL` — renamed from `_MOCK_KEY_SENTINEL` to `MOCK_KEY_SENTINEL`** (`drivers/software_fallback.py`):
  The leading underscore implied a private attribute, but callers in both the `__init__.py`
  factory and test code reference the sentinel by name to opt into the deterministic mock key
  path.  Removing the underscore formalizes it as a documented public class attribute,
  eliminating the implicit private-access idiom and allowing downstream code to reference it
  without a pylint / mypy private-access warning.  `_MOCK_KEY` retains its underscore because
  it is a fixed internal stub that must not be referenced outside `initialize_hardware()`.

- **`bootstrap_sensor_node()` — fallback exception chain decoupled via boolean flag** (`__init__.py`):
  The `SoftwareFallbackDriver` re-initialization and warning print previously executed inside
  the `except NotImplementedError` block, implicitly chaining the original `NotImplementedError`
  as `__context__` on any exception raised during fallback setup.  A boolean flag
  `_hw_not_implemented` is now set inside the `except` clause; the warning and fallback
  initialization run in an unconditional `if _hw_not_implemented:` branch outside the block.
  On constrained MicroPython serial consoles, this prevents a doubled traceback — the original
  `NotImplementedError` and the `RuntimeError` from a misconfigured fallback key path — from
  flooding the output and obscuring the root cause.

- **`SovereignEnvelope.__init__()` — negative sequence counter clamped to zero** (`envelope.py`):
  After a successfully parsed integer is assigned to `self._sequence`, an `else` branch on the
  `try/except ValueError` block checks `if self._sequence < 0` and resets it to zero.  A
  negative value stored in the sequence file — whether written by an adversary with VFS write
  access or produced by a filesystem fault that corrupted a flash page with a plausible-looking
  negative decimal string — would cause the first `seal()` call to emit `q=0` (after the
  pre-increment `+= 1`), violating the `q ≥ 1` monotonic custody invariant and potentially
  confusing downstream consumers.  The clamp is placed in an `else` clause so it only executes
  on successfully parsed integers; the `ValueError` path already resets to zero independently.

- **`SovereignEnvelope.seal()` — preimage length prefix changed from character count to UTF-8 byte count** (`envelope.py`):
  `node_id` and `timestamp` are now encoded to UTF-8 byte arrays independently before the
  length-prefix is computed.  The preimage is assembled from raw byte slices rather than from
  a single f-string that is encoded afterward:
  `f"1|{len(node_bytes)}:".encode() + node_bytes + b"|" + f"{len(time_bytes)}:".encode() + time_bytes + ...`.
  Without this change, any `node_id` or `timestamp` containing multi-byte UTF-8 characters
  (characters outside U+007F) would produce a prefix reflecting the Unicode character count
  rather than the UTF-8 byte count.  A cross-platform receiver — including constrained
  MicroPython targets that may count string length in bytes — would then parse field
  boundaries at the wrong offset, silently reconstructing a different preimage and rejecting
  legitimate frames.  For ASCII-only inputs `len(s) == len(s.encode("utf-8"))`, so all
  existing tests remain byte-for-byte identical.

- **`packages/sovereign-sensor/pyproject.toml` — Trove classifiers corrected to valid PyPI identifiers**:
  `"Topic :: Embedded Systems"` is not a registered Trove classifier and would cause `twine
  check` to fail at release time.  It is replaced with `"Topic :: System :: Hardware"`, which
  is a registered classifier in the PyPI taxonomy.  `"Programming Language :: Python :: 3"`,
  `"Programming Language :: Python :: 3 :: Only"`, and
  `"Programming Language :: Python :: Implementation :: MicroPython"` are added to correctly
  signal the package's Python version floor and MicroPython target runtime to tooling and
  packaging indexes.

- **`SoftwareFallbackDriver.initialize_hardware()` — empty key file guard added** (`drivers/software_fallback.py`):
  After a successful `open()` of the key file, the read bytes are now stored in a local
  `key_bytes: bytes` variable rather than assigned directly to `self._secret_key`.  If
  `key_bytes` is empty, `ValueError` is raised immediately with a descriptive message before
  any assignment to `_secret_key` or `_initialized`.  A 0-byte key file produces an HMAC
  keyed with `b""`, which is identical across every node that encounters the same empty-file
  failure — indistinguishable from other nodes with the same defect and providing no
  cryptographic uniqueness.  The node must halt at the bootstrap boundary rather than emit
  authentication tags under a keyless primitive.

- **`SovereignEnvelope.seal()` — preimage length-prefix delimiter injection closed** (`envelope.py`):
  `node_id` and `timestamp` are now length-prefixed before insertion into the pipe-joined
  preimage string:
  `1|{len(node_id)}:{node_id}|{len(timestamp)}:{timestamp}|sequence|algorithm|canonical_payload`.
  Without length prefixes, `node_id="abc|def"` with `timestamp="ghi"` and `node_id="abc"` with
  `timestamp="def|ghi"` collapse to the identical naive pipe-joined string
  `1|abc|def|ghi|…`, enabling a cross-identity signature reuse attack where an adversary
  holding a valid frame for one `(node_id, timestamp)` pair can present it as authentic for
  a structurally equivalent pair.  Length prefixes make every field boundary unambiguous
  regardless of field content, eliminating the collision surface entirely.

- **`SovereignEnvelope.seal()` — alphabetical key canonicalization enforced** (`envelope.py`):
  The `json.dumps` call that serializes the payload into the HMAC preimage now passes
  `sort_keys=True`, guaranteeing that semantically equivalent payloads with different key
  insertion orders produce byte-identical canonical strings.  On constrained MicroPython
  targets, dict insertion order is not guaranteed stable across firmware versions or allocation
  events; without this flag, two nodes sealing the same observation could produce diverging
  preimages and non-matching signatures.  The flag makes cryptographic preimages perfectly
  deterministic across space and time.

- **`SoftwareFallbackDriver.sign()` — initialization guard simplified** (`drivers/software_fallback.py`):
  The guard condition was tightened to `if not self._initialized:`, removing a previously
  present redundant `hasattr(self, "_secret_key")` check.  The `_secret_key` attribute is
  unconditionally declared in `__init__`, making the `hasattr` test structurally dead code;
  the `_initialized` boolean is the sole authoritative initialization sentinel.

- **Driver imports standardized to package-relative form** (`drivers/software_fallback.py`,
  `drivers/esp32_hardware.py`): Both driver modules now import `SovereignCryptoDriver` via
  `from ..interface import SovereignCryptoDriver`, consistent with the workspace-wide
  convention for intra-package imports.

- **`SovereignEnvelope.__init__()` — power-loss truncation recovery** (`envelope.py`):
  The `int(data.strip())` call in the constructor now lives inside a nested
  `try/except ValueError` block.  A 0-byte or otherwise non-integer sequence file —
  the typical artifact of a mid-write power interruption that cut power after the VFS
  page was erased but before any digits were flushed — previously propagated `ValueError`
  and aborted device initialization entirely.  The handler now prints a diagnostic and
  resets `_sequence` to 0, allowing the node to boot and resume custody-chain sealing
  without manual intervention.

- **`SovereignEnvelope.seal()` — write-path exception scope narrowed** (`envelope.py`):
  The `except Exception` guard around the VFS counter write is replaced with `except OSError`.
  The previous broad catch silently discarded any structural Python runtime defect
  (e.g. a logic error producing a `TypeError` inside the `with` body) that could mask
  a code bug under the appearance of a benign flash-write failure.  `OSError` covers
  every storage and filesystem media error class that legitimate flash degradation
  produces, while allowing all other exception types to propagate normally.

- **`SovereignEnvelope.seal()` — wire-frame serialization frozen with `sort_keys=True`** (`envelope.py`):
  The final `json.dumps(frame, ...)` call that produces the transmitted bytes now passes
  `sort_keys=True`.  Without this flag, the seven envelope keys (`alg`, `d`, `n`, `q`, `s`,
  `t`, `v`) and the nested payload sub-object under `"d"` were serialized in insertion order,
  which CPython 3.7+ preserves but MicroPython does not guarantee stable across firmware
  versions or heap-allocation patterns.  Two nodes on different firmware builds could therefore
  produce different raw byte frames for the same observation even though their HMAC signatures
  matched — breaking byte-exact deduplication, frame-level checksumming, and any transport
  consumer that compares raw wire content rather than parsed JSON.  The flag freezes the
  alphabetical key sequence at both the outer frame and the inner `"d"` sub-object level.

- **`SoftwareFallbackDriver.initialize_hardware()` — silent key substitution eliminated** (`drivers/software_fallback.py`):
  The previous `except OSError` fallback substituted `_MOCK_KEY` for any key path that could
  not be opened, including real production paths that simply had a permissions error, wrong
  path, or transient mount failure.  A node would boot and begin sealing observations with a
  fixed, publicly known stub key indistinguishable from an authenticated one.  The method now
  checks `self._key_path == _MOCK_KEY_SENTINEL` first: only the explicit sentinel
  `"/mock/test_gateway.key"` opts into the deterministic mock key.  Any other path that cannot
  be opened re-raises the `OSError` wrapped in a descriptive `RuntimeError`, halting the node
  at the bootstrap phase rather than emitting silently-weakened custody frames.

- **`bootstrap_sensor_node()` — `NotImplementedError` guard added** (`__init__.py`):
  `driver.initialize_hardware()` is now called inside a `try/except NotImplementedError`
  block.  When the selected driver raises — as `ESP32HardwareDriver` does while hardware crypto
  acceleration is still a skeleton — the factory prints a loud warning and rebinds to a fully
  functional `SoftwareFallbackDriver` so the node can exercise the sealing, sequencing, and
  VFS serialization layers on the workbench.  Without this guard, an ESP32 boot would silently
  succeed initialization and then crash on the first `seal()` call, hiding the missing
  implementation behind an opaque `NotImplementedError` deep in the custody chain.

### Fixed

- **`rotate_keypair()` — `old_private_key` strict type annotation** (`crypto.py`):
  The `old_private_key = self._private_key` assignment previously left the variable
  with the inferred type `ed25519.Ed25519PrivateKey | None` because `self._private_key`
  is declared as `Optional`.  While the preceding guard clause (`if not self._private_key`)
  guarantees the value is non-`None` at that point, strict static type checkers and LSPs
  could not narrow the type through the guard boundary and emitted a warning at the
  `.sign()` call site.  The assignment is updated to
  `old_private_key: ed25519.Ed25519PrivateKey = cast(ed25519.Ed25519PrivateKey, self._private_key)`
  using `typing.cast` (imported alongside the existing `typing` imports), satisfying
  strict type checkers without changing runtime behavior.

## [1.1.0] - 2026-06-01

### Added

- **`SovereignStorageError` custom exception** (`crypto.py`): New `RuntimeError`
  subclass raised exclusively when the promotion phase of `rotate_keypair()` fails
  after partial disk mutation.  Carries one of two distinct human-readable messages
  depending on rollback outcome (see Security — Fix 4 and Fix 5).
  Exported from `sovereign_core` top-level namespace.

- **`TestPromotionPhaseRollback`** — 8 test cases in `test_verification_protocol.py`
  (5 original + 3 regression cases) covering the full promotion-phase fault matrix:
  first-replace failure (both staged files purged), second-replace failure with
  successful rollback (`SovereignStorageError` + restored `.pem`), second-replace
  failure with failed rollback (`SovereignStorageError` with `CRITICAL FAILURE`
  split-state warning), in-memory key invariant, zero orphaned files, post-rollback
  manager liveness, and cleanup `PermissionError` suppression (primary
  `SovereignStorageError` surfaces intact even when `os.remove` raises).
  Full workspace suite: **128 passed, 1 skipped**.

- **Phase 6.1 — `PublicKeyBundle` export format** (`crypto.py`, `gateway.py`):
  New `PublicKeyBundle` Pydantic v2 model in `sovereign_core.crypto` carrying four
  fields: `public_key` (base64-encoded raw Ed25519 key), `attestation` (a
  self-signed `ForensicReceipt` dict proving private-key ownership), `issued_at`
  (UTC ISO 8601 timestamp sealed inside the attestation signature — see Security),
  and optional `node_id` (human-readable label, default `None`).  Serialisable to
  JSON via Pydantic v2 `model_dump_json()`.

- **`SovereignGateway.export_public_key_bundle(node_id=None) -> PublicKeyBundle`**
  (`gateway.py`): New gateway method that mints a self-signed `ForensicReceipt`
  attestation over the payload `{"public_key": …, "type": "key_attestation"}`, then
  assembles and returns a `PublicKeyBundle`.  Triggers lazy keypair loading if the
  identity has not yet been initialised, consistent with `export_public_key()`.
  The `issued_at` timestamp is computed before `generate_receipt()` is called so it
  is covered by the Ed25519 signature.

- **`SovereignGateway.save_public_key_bundle(path, node_id=None) -> None`**
  (`gateway.py`): Convenience method that calls `export_public_key_bundle()` and
  writes the Pydantic v2 `model_dump_json()` output to `path` as UTF-8 JSON.  The
  parent directory must pre-exist; no `makedirs` is performed.

- **Phase 6.3 — `SuccessionReceipt` TypedDict** (`crypto.py`): New TypedDict with
  four fields: `previous_public_key` and `new_public_key` (base64-encoded Ed25519
  keys), `rotation_timestamp` (UTC ISO 8601), and `succession_signature`
  (base64-encoded Ed25519 signature of the canonical rotation payload produced by
  the *outgoing* private key).

- **`SovereignKeyManager.rotate_keypair() -> SuccessionReceipt`** (`crypto.py`):
  Generates a brand-new Ed25519 keypair, builds a canonical JSON rotation payload
  binding `previous_public_key`, `new_public_key`, and `rotation_timestamp`, signs
  it with the *outgoing* private key, then atomically promotes both the new private
  key `.pem` and the public key `.pub` to disk via the workspace's existing
  `tempfile.NamedTemporaryFile` → `os.fsync` → `os.replace` pattern.  In-memory
  identity slots are updated only after both disk writes succeed, ensuring the node
  is never left with a split identity state.

- **`SovereignKeyManager.verify_succession(receipt, trusted_previous_public_key) -> bool`**
  (`crypto.py`): Static method for standalone auditor-side verification of any
  rotation event.  Requires no live key material.  Performs an anchor assertion
  (see Security) before any cryptographic operation, then verifies the
  `succession_signature` against the canonical rotation payload using the
  `previous_public_key` confirmed by the anchor — see Security for why the
  explicit `trusted_previous_public_key` argument is mandatory.

- **`test_verification_protocol.py`** — 48 new test cases across six classes:
  `TestPublicKeyBundleModel` (4), `TestPublicKeyBundleExport` (14),
  `TestPublicKeyBundleSave` (6), `TestKeyRotationBasic` (9),
  `TestSuccessionVerification` (4), `TestSuccessionAdversarial` (8) and two
  dedicated security-property tests (`test_bundle_issued_at_is_sealed_in_attestation_signature`,
  `test_verify_succession_anchor_check_rejects_wrong_trusted_key`).  Full workspace
  suite: **108 passed, 1 skipped** (POSIX `fchmod`, correct on Windows).

- **`PublicKeyBundle` and `SuccessionReceipt` exported from `sovereign_core.__init__`**:
  Both new types added to `__all__` for direct importability from the top-level
  package namespace.

### Changed

- **Version promoted to `1.1.0`** across all workspace package manifests
  (`packages/sovereign-core/pyproject.toml`, `packages/sovereign-fastapi/pyproject.toml`,
  `packages/sovereign-runtime/pyproject.toml`, root `pyproject.toml`) and
  `sovereign_core.__version__`.

### Fixed

- **`SovereignKeyManager` constructor parameter corrected in documentation**
  (`README.md`, `packages/sovereign-core/README.md`): The "Key rotation with auditable
  succession" snippet used `SovereignKeyManager(key_path=".keys/sovereign_identity.pem")`,
  which would raise `TypeError` at runtime because the actual constructor parameter is
  `key_dir` (a directory path, not a file path).  Both README files now show the correct
  invocation: `SovereignKeyManager(key_dir=".keys")`.

### Security

- **Fix 8 — Atomic `save_public_key_bundle()` write via temp-file promotion** (`gateway.py`):
  `save_public_key_bundle()` previously called `Path.write_text()` directly, leaving
  the bundle file partially written or absent if a crash occurred during the I/O.
  The method now follows the workspace's standard promotion pattern:
  `tempfile.NamedTemporaryFile` → `os.fsync` → `os.replace`, so the destination path
  is either the complete previous bundle or the complete new bundle — never a partial
  write.  The orphaned temp is removed on failure.

- **Fix 7 — Broad cleanup exception suppression in `rotate_keypair()`** (`crypto.py`):
  Every `os.remove()` call in cleanup and rollback blocks previously caught only
  `FileNotFoundError`, allowing any other OS-level error (`PermissionError`,
  `IsADirectoryError`, etc.) to propagate out of the cleanup block and silently
  replace the primary exception — masking the `SovereignStorageError` carrying the
  split-state or recovery message.  All four cleanup sites now catch `Exception`,
  print a `⚠️ Warning:` diagnostic to `stderr`, and continue, guaranteeing the
  primary exception is never suppressed by a secondary file-deletion failure.

- **Fix 6 — Conditional split-state diagnostics in promotion rollback** (`crypto.py`):
  The `SovereignStorageError` raised after a `.pub` promotion failure previously carried
  a single static message regardless of whether the rollback restore succeeded.  An
  operator reading the log had no way to distinguish a clean recovery from a corrupted
  split identity.  The rollback path now tracks `rollback_succeeded` and branches:
  on success it raises with "Key rotation failed; structural disk consistency was
  preserved and the original private key was successfully restored."; on failure it
  raises with "CRITICAL FAILURE: Key rotation failed and the system identity is in a
  split-state. The original private key could not be restored. Disk state may be
  corrupted." — an unambiguous operator alert that the node requires manual intervention.

- **Fix 5 — Staged file cleanup on first `os.replace` failure** (`crypto.py`):
  Before this fix, a failure on the first `os.replace` (promoting the staged `.pem` to
  the live path) left the staged `.pub` temp file orphaned on disk — an un-promoted new
  private key with no corresponding public key.  The `backup_pem_bytes` read and the
  first `os.replace` are now wrapped in a single `try/except` that deletes both
  `tmp_pem_path` and `tmp_pub_path` before re-raising, guaranteeing no key material is
  left behind if the initial promotion step fails.

- **Fix 4 — Hardened key promotion phase with `SovereignStorageError` rollback** (`crypto.py`):
  Even after the transactional staging loop (Fix 1) guarantees both files are synced to
  disk before either is promoted, a failure on the second `os.replace` (the `.pub`
  promotion) would leave the node with the new private key live but the old public key
  on disk — an inconsistent pair.  The promotion phase now captures a byte-exact backup
  of the original `.pem` before executing the first `os.replace`.  The second
  `os.replace` is wrapped in a `try...except`.  On failure: the backup bytes are written
  to a new temp file and atomically promoted back to the live `.pem` path via a nested
  rollback write; the orphaned `.pub` staging file is removed in a `finally` block; a
  `SovereignStorageError` is raised with a message conditional on rollback outcome
  (see Fix 6).

- **Fix 1 — Hardened key rotation via atomic transactional staging loop** (`crypto.py`):
  The previous `rotate_keypair()` wrote the new `.pem` and `.pub` files via two
  *independent* atomic promotions, each in its own try/except block.  If the first
  promotion (`.pem`) succeeded and the second (`.pub`) failed — disk-full, permission
  denied, I/O error — the node was left with a split identity: the on-disk private
  key no longer matched the public key.  The method is now governed by a single
  transactional staging loop: both files are written to isolated temp files within the
  same key directory before either live path is touched.  Only once both staging writes
  commit to disk via `flush` + `fsync` are the two back-to-back `os.replace` calls
  executed.  If either staging write raises, the except handler deletes every staged
  file and re-raises without modifying any live key path or in-memory key state.
  In-memory identity slots are updated only after both `os.replace` calls return.

- **Fix 2 — Mitigated self-referential signature validation via mandatory external anchor pinning** (`crypto.py`):
  `verify_succession()` previously derived the verification public key entirely
  from the receipt's own `previous_public_key` field.  An attacker could mint a
  `SuccessionReceipt` with a rogue keypair, embed that keypair's own public key as
  `previous_public_key`, and have the receipt verify correctly against itself.
  The method signature is changed to
  `verify_succession(receipt, trusted_previous_public_key: str) -> bool`.  A strict
  anchor guard clause `if receipt["previous_public_key"] != trusted_previous_public_key: return False`
  executes before any base64 decoding or Ed25519 operation.  The trusted anchor
  must be supplied by the caller from an out-of-band source — e.g. the
  `new_public_key` of the preceding receipt, or the key recorded before the
  rotation was initiated — not from the receipt under audit.

- **Fix 3 — Sealed metadata exposure by capturing `issued_at` within the Ed25519 payload envelope** (`gateway.py`):
  In `export_public_key_bundle()`, `issued_at` was previously computed after
  `generate_receipt()` returned, leaving the bundle's issuance timestamp
  uncovered by the Ed25519 signature.  An attacker could modify `issued_at` on
  the bundle object or in a saved JSON file without breaking attestation
  verification.  `issued_at` is now captured before `generate_receipt()` is
  called and included in the metadata dict that becomes part of the signed
  canonical manifest.  `bundle.issued_at` and
  `bundle.attestation["metadata"]["issued_at"]` are now provably identical strings
  covered by the same signature.

## [1.0.0] - 2026-05-22

### Added

- **GitHub Actions CI/CD** (`.github/workflows/test.yml`, `.github/workflows/publish.yml`):
  `test.yml` triggers on every push to `main` and all pull requests; runs
  `uv sync --all-packages` then `uv run pytest` on `ubuntu-latest` with Python 3.12
  via `astral-sh/setup-uv`.  `publish.yml` triggers on `v*` tags; a dedicated `test`
  job mirrors the full CI suite inline and the `publish` job declares `needs: [test]`,
  guaranteeing the pipeline fails closed and blocks PyPI deployment when a tag is
  pushed against a broken or untested commit.  Uses `pypa/gh-action-pypi-publish`
  with OIDC Trusted Publishing (`id-token: write` permission).

- **`sovereign-verify` CLI — JSON object type guard**
  (`packages/sovereign-core/src/sovereign_core/cli.py`): After `json.loads` parses
  the receipt file, an explicit `isinstance(receipt, dict)` check is now enforced.
  If the top-level JSON value is not an object (e.g. an array, string, or bare
  number), the CLI writes a descriptive error to `stderr` and exits `1` before any
  field access is attempted, preventing raw `AttributeError` tracebacks when
  auditors supply malformed or non-object documents.

- **Post-launch documentation polish** (`ROADMAP.md`,
  `examples/fastapi_gateway/app.py`): Fixed duplicate Phase 5.1/5.2 section labels in
  `ROADMAP.md` — the shipped FastAPI middleware checklist is now consolidated under the
  single `5.1` heading, and the streaming extension path is correctly numbered `5.5`
  rather than colliding with the `5.2` Django entry.  Section 6.2 updated to reflect
  the shipped CLI: package corrected from `sovereign-runtime` to `sovereign-core`,
  flag corrected from `--key` to `--public-key`, and description updated to match the
  base64 key string interface rather than a bundle file.  In the FastAPI example app,
  the raw `receipt["payload_hash"]` subscript replaced with the safe `receipt.get()`
  accessor, eliminating the potential `KeyError` on an incomplete receipt dict.

- **`README.md` — Security provenance repositioning**: Reframed the project as a
  local-first AI WAF and compliance gate.  New introductory section leads with the
  non-repudiation and chain-of-custody value proposition.  Added a "The ForensicReceipt:
  Sealed Transformation Accounting" technical subsection documenting how the Ed25519
  signature binds both `payload_hash` (SHA-256 of the sieved output) and the
  `prose_tax_summary` token-delta metrics inside the same canonical manifest — giving
  auditors mathematical proof that neither the boundary output nor the transformation
  accounting can be altered after issuance.  Prose Tax optimization demoted to an
  optional feature subsection inside the audit envelope narrative.  All standalone
  code snippets wrapped in `asyncio.run()` harnesses for direct executability.

- **PEP 561 `py.typed` marker** (`packages/sovereign-fastapi/src/sovereign_fastapi/py.typed`):
  Empty marker file declaring `sovereign-fastapi` as a typed package.  Included in
  the built distribution via `[tool.setuptools.package-data]` in the package manifest,
  enabling downstream type checkers to resolve `sovereign_fastapi` stubs without
  additional configuration.

### Changed

- **Version promoted to `1.0.0`** across all package manifests
  (`packages/sovereign-core/pyproject.toml`, `packages/sovereign-fastapi/pyproject.toml`,
  root `pyproject.toml`, `sovereign_core.__version__`): Reflects stable, production-ready
  API surface with no further breaking-change windows before a major bump.

- **PyPI metadata injected** (`packages/sovereign-core/pyproject.toml`,
  `packages/sovereign-fastapi/pyproject.toml`): Both package manifests now carry
  `license = { text = "MIT" }`, `[project.classifiers]` (MIT, Python 3.12,
  Intended Audience :: Developers, Topic :: Security, Topic :: Software Development :: Libraries),
  and `[project.urls]` (Homepage, Repository, Changelog) targeting
  `https://github.com/kenwalger/sovereign-sdk`.

- **`sovereign-fastapi` inter-package dependency version-pinned**
  (`packages/sovereign-fastapi/pyproject.toml`): `"sovereign-core"` dependency
  tightened to `"sovereign-core>=1.0.0,<2.0.0"` to prevent accidental major-version
  drift across the workspace.

- **`sovereign-verify` CLI — narrowed exception handling**
  (`packages/sovereign-core/src/sovereign_core/cli.py`): `_verify()` now catches
  `except Exception` (fail-closed fallback comment added) instead of the
  previously redundant `except (InvalidSignature, KeyError, ValueError, Exception)`.
  The now-unused `from cryptography.exceptions import InvalidSignature` import removed.

- **`README.md` — async example wrapped in `asyncio.run(main())` harness**:
  The "Verifying a ForensicReceipt" code snippet is now a complete, runnable
  script with `async def main()` and `asyncio.run(main())` so readers can
  execute it directly without a surrounding async framework.

## [0.8.4] - 2026-05-22

### Added

- **`sovereign-verify` — Stateless ForensicReceipt Verification CLI**
  (`packages/sovereign-core/src/sovereign_core/cli.py`,
  `packages/sovereign-core/pyproject.toml`): New `argparse`-based CLI entry
  point registered as `sovereign-verify`.  Accepts `--receipt FILE` (path to a
  JSON ForensicReceipt) and `--public-key BASE64_KEY` (base64 raw Ed25519 public
  key).  Performs key-pin assertion (receipt `"public_key"` field must match the
  provided key) followed by Ed25519 signature verification over the canonical
  manifest `{"metadata": …, "payload_hash": …, "timestamp": …}`.  Prints
  `Verified  ✓  payload_hash: …` to stdout and exits `0` on success; prints a
  descriptive tamper warning to stderr and exits `1` on any failure.  Requires
  no private key or `SOVEREIGN_NODE_SECRET`, enabling fully stateless out-of-band
  auditing.

- **`examples/fastapi_gateway/app.py`** — Runnable FastAPI application wired with
  `SovereignMiddleware` pointing to `.keys/example_identity.pem`.  Exposes a
  single `POST /api/v1/sanitize` route that echoes the sieved `"text"` field and
  the receipt `payload_hash` back to the caller.  Start with
  `SOVEREIGN_NODE_SECRET=<secret> uv run uvicorn examples.fastapi_gateway.app:app --reload`.

- **`examples/fastapi_gateway/client.py`** — Companion `httpx` client that
  transmits a high-density Prose Tax payload to the example server, prints the
  optimized low-entropy output, and pretty-prints the `X-Sovereign-Receipt-Signature`
  and `X-Sovereign-Tokens-Saved` response headers.

- **`CONTRIBUTING.md`** — Open-source community guidelines specifying the four
  project invariants (local-first, tamper-evidence, zero regression, dependency
  minimalism), pull request requirements, code style rules, and instructions for
  running tests locally via `uv run pytest`.

- **`SECURITY.md`** — Security policy documenting the coordinated private
  disclosure process, in-scope vulnerability categories (key forgery, receipt
  bypass, boundary injection, CLI acceptance of tampered receipts), response
  timeline commitments, and contact address.

- **`README.md` — "Running the Workspace Examples" section**: Documents the exact
  `uv run` commands to start the example FastAPI server, execute the example
  client, and invoke the `sovereign-verify` CLI with sample output.

### Changed

- **`SovereignMiddleware` — PEP 8 Import Grouping** (`middleware.py`): Stdlib
  imports (`json`, `logging`, `typing`) are now grouped first, followed by a
  blank-line-separated Starlette framework block, then a local block for
  `sovereign_core.gateway`.  The module-level `logger` assignment is placed
  after all import blocks rather than inline between stdlib and framework imports.

- **`SovereignMiddleware.dispatch` — Strict-Mode Critical Log** (`middleware.py`):
  When `strict_mode=True`, the exception handler now emits
  `logger.critical("Sovereign boundary processing failed under strict enforcement. Aborting request.", exc_info=True)`
  immediately before returning the HTTP 422 response.  Previously, strict-mode
  failures were invisible to structured log consumers: only the 422 response
  status was observable, with no log record emitted.

## [0.8.3] - 2026-05-22

### Added

- **`SovereignKeyManager.has_identity` — Public State Property** (`crypto.py`):
  New read-only `@property` that returns ``True`` when both ``_private_key`` and
  ``_public_key`` are non-``None`` (fully initialised in memory), ``False``
  otherwise.  Provides a clean, public alternative to private-attribute inspection
  from outside the class, eliminating the need for callers to pierce internal
  slots to determine whether the keypair is loaded.

- **`TestSovereignGateway.test_gateway_export_public_key_uses_state_not_string_matching`**
  (`test_gateway.py`): Three-step architectural contract test for the
  ``has_identity``-based refactor of ``export_public_key()``.  Asserts that
  ``has_identity`` is ``False`` on a fresh gateway, transitions to ``True`` after
  the first call triggers lazy initialization, and that a subsequent call with
  ``load_or_generate_keypair`` mocked confirms the method does not attempt to
  reload — proving the implementation relies entirely on explicit state gating
  rather than string-in-exception matching.

- **`TestSovereignMiddleware.test_middleware_emits_error_log_on_interception_failure`**
  (`test_middleware.py`): Observability contract test for the structured error
  log added to the non-strict bypass path.  Sends a JSON payload missing the
  configured ``payload_field`` key to trigger a ``KeyError`` inside the
  middleware try block.  Uses ``pytest.LogCaptureFixture`` (``caplog``) to
  assert that (a) the request completes with HTTP 200 (defensive bypass intact)
  and (b) at least one ``logging.ERROR`` record is emitted to the
  ``sovereign_fastapi`` logger containing the phrase
  ``"Sovereign boundary processing failed"``.

### Changed

- **`SovereignGateway.export_public_key()` — State-Based Key Loading**
  (`gateway.py`): Replaced the ``try/except RuntimeError`` block (which
  matched the sentinel string ``"Keypair not loaded"`` inside the exception
  message) with a direct ``if not self._key_manager.has_identity:`` state
  check.  The refactor removes all coupling to internal exception message
  strings and makes the gating logic explicit and unconditionally correct.

- **`SovereignMiddleware.dispatch` — Structured Error Logging** (`middleware.py`):
  The non-strict exception bypass path now emits a ``logging.ERROR`` record via
  ``logger.error("Sovereign boundary processing failed. Bypassing defensively …",
  exc_info=True)`` before falling through.  Previously the bypass was entirely
  silent, making interception failures invisible to log-based observability
  systems.  Full exception context is captured via ``exc_info=True``.

- **`sovereign-fastapi` Starlette Dependency Range** (`pyproject.toml`):
  Narrowed from ``starlette>=0.27.0`` to ``starlette>=0.35.0,<1.0.0`` to
  anchor the package against the tested private-attribute contract
  (``request._body`` / ``_CachedRequest.wrapped_receive``) and prevent
  unguarded drift into a potential Starlette 1.x major-version break.

## [0.8.2] - 2026-05-22

### Added

- **`TestSovereignMiddleware.test_middleware_corrects_content_length_header`**
  (`packages/sovereign-fastapi/tests/test_middleware.py`): Functional regression
  test for the v0.8.1 `Content-Length` realignment fix.  A custom route handler
  echoes back both the `content-length` header it observes on the inbound request
  and the actual byte count of the body it reads via `await request.body()`.  A
  filler-dense payload (`"hi please just certainly of course help me now"` → sieved
  to `"help me now"`) ensures the body shrinks measurably, making a stale header
  produce a clearly visible mismatch.  Asserts that both values are identical,
  confirming that `request.scope["headers"]` was patched in-place by the middleware
  so downstream ASGI consumers never hang waiting for bytes that no longer exist.

- **`TestSovereignGateway.test_gateway_export_public_key_bubbles_unrelated_runtime_errors`**
  (`packages/sovereign-core/tests/test_gateway.py`): Defensive regression test for
  the v0.8.1 `export_public_key()` exception-narrowing fix.  Uses
  `unittest.mock.patch.object` with `PropertyMock` to make the underlying
  `SovereignKeyManager.public_key` property raise
  `RuntimeError("Unexpected database disk full error")`.  Asserts that (a) the
  exception propagates to the caller unchanged via `pytest.raises`, and (b)
  `load_or_generate_keypair()` — mocked and tracked independently — is never
  invoked.  This guarantees that system faults fail loudly rather than triggering
  silent key regeneration that would replace the node's cryptographic identity.

## [0.8.1] - 2026-05-22

### Fixed

- **`SovereignMiddleware` — `Content-Length` Header Realignment** (`middleware.py`):
  After the Prose Tax sieve shrinks or rewrites the JSON body, the inbound
  `Content-Length` header retained the original byte count.  Downstream ASGI
  handlers, routers, and reverse proxies that validate header/body length parity
  could therefore encounter a stale-length payload mismatch or a hanging read
  timeout waiting for bytes that no longer exist.  Fixed by computing
  ``len(new_body)`` immediately after ``request._body`` is overwritten and
  patching ``request.scope["headers"]`` in place: existing ``content-length``
  entries are replaced in a single list comprehension pass, and the header is
  appended if it was absent.

- **`SovereignGateway.export_public_key()` — RuntimeError Path Narrowing**
  (`gateway.py`): The bare ``except RuntimeError:`` guard intercepted every
  ``RuntimeError`` raised inside ``self._key_manager.public_key``, including
  system-level failures such as disk faults and file permission errors.
  Swallowing those errors silently triggered ``load_or_generate_keypair()``,
  risking the generation of a rogue cryptographic identity on a node that
  could not safely read its existing key material.  The handler is narrowed to
  ``except RuntimeError as e:`` with an explicit
  ``"Keypair not loaded" in str(e)`` guard; any ``RuntimeError`` whose message
  does not match the documented sentinel is re-raised immediately so the SDK
  fails loudly and defensively.

## [0.8.0] - 2026-05-22

### Added

- **`sovereign-fastapi` — Phase 5 ASGI Middleware Package** (new workspace member
  `packages/sovereign-fastapi/`): Implements Phase 5 of the Sovereign Systems
  Specification by introducing a drop-in `SovereignMiddleware` class for
  FastAPI and Starlette applications.  The class inherits from Starlette's
  `BaseHTTPMiddleware` and intercepts every inbound JSON request in its
  `dispatch` lifecycle:
  (1) Extracts the target text from the configurable `payload_field` key (or
  falls back to the serialised root body when no field is specified).
  (2) Invokes `await self.gateway.sieve_and_sign(target_text)` to strip Prose
  Tax filler, normalise whitespace, and mint a fully signed Ed25519
  `ForensicReceipt` in coroutine-local scope — concurrent requests sharing the
  same middleware instance never bleed per-request metrics into each other's
  receipts.
  (3) Overwrites `request._body` on the `_CachedRequest` instance so that
  `_CachedRequest.wrapped_receive` serves the cleaned payload to every downstream
  route handler transparently, without requiring any code changes in the application.
  (4) Caches the sealed receipt at `request.state.sovereign_receipt` for
  in-route auditability.
  (5) Injects `X-Sovereign-Receipt-Signature` (Ed25519 base64 signature) and
  `X-Sovereign-Tokens-Saved` (cumulative FinOps savings counter) headers on
  the outbound response.
  The constructor accepts `signing_key: str`, `strict_mode: bool = False`
  (returns HTTP 422 on any interception error rather than falling through), and
  `payload_field: Optional[str] = None`.  Importable as
  `from sovereign_fastapi.middleware import SovereignMiddleware`.

- **`sovereign-fastapi` Test Suite** (`packages/sovereign-fastapi/tests/test_middleware.py`):
  Four functional test cases covering the middleware contract:
  (1) `test_middleware_happy_path` — asserts that a filler-laden inbound
  payload is sieved to `"help me now"` before the route handler sees it, and
  that both `X-Sovereign-*` response headers are present and well-formed.
  (2) `test_middleware_passthrough_for_non_json` — verifies that requests with
  non-JSON content types bypass sieve processing entirely and produce no receipt
  header.
  (3) `test_middleware_strict_mode_missing_field` — confirms that `strict_mode=True`
  returns HTTP 422 when the `payload_field` key is absent from the JSON body.
  (4) `test_middleware_concurrency_isolation` — fires 20 simultaneous requests
  (10 filler + 10 clean payloads) via `asyncio.gather` and asserts that every
  response body reflects the correct minimized form of its own payload, proving
  that the coroutine-local `OptimizationReceipt` capture in `sieve_and_sign`
  prevents cross-request metric contamination under concurrent load.

- **Workspace `--import-mode=importlib`** (`pyproject.toml`): Added
  `addopts = "--import-mode=importlib"` to `[tool.pytest.ini_options]` so that
  pytest resolves test module paths via the `importlib` import system.  This
  prevents the `ModuleNotFoundError` collision that arises when multiple
  workspace packages each contain a `tests/` directory with an `__init__.py`,
  which the default `prepend` import mode treats as a single shared `tests`
  package.

- **Workspace dev-dependencies** (`pyproject.toml`): Added `fastapi>=0.100.0`,
  `httpx>=0.24.0`, and `sovereign-fastapi` to the root workspace dev-dependencies
  so that `uv run pytest` discovers and executes the new middleware test suite
  without requiring per-package invocations.

## [0.7.2] - 2026-05-22

### Added

- **`SovereignGateway` — Unified High-Level Developer Interface** (`gateway.py`):
  Introduced the `SovereignGateway` class as the primary entry point for application
  code interacting with the Sovereign Systems SDK.  The class accepts a `signing_key`
  file path (defaulting to `.keys/sovereign_identity.pem`), defensively creates the
  key directory via `os.makedirs(..., exist_ok=True)` before initializing the
  `SovereignKeyManager` (preventing `FileNotFoundError` in stateless containerized
  environments), and exposes four public methods:
  (1) `async sieve(text_payload: str) -> str` — delegates to `process_prose_tax` to
  strip conversational boilerplate, remove filler tokens, and normalize whitespace,
  returning the purified string and accumulating optimization metrics in the gateway's
  internal `SessionContext`; (2) `sign(clean_context: str) -> ForensicReceipt` — wraps
  `SovereignKeyManager.generate_receipt` to produce a fully minted, Ed25519-signed
  `ForensicReceipt` envelope that seals both data provenance and FinOps
  cost-optimization telemetry in a single cryptographic manifest (see below);
  (3) `async sieve_and_sign(text_payload: str) -> SovereignBoundaryResponse` — one-shot
  macro that internally calls `sieve()` then `sign()` and returns both the purified
  string and the sealed receipt together in a `SovereignBoundaryResponse` Pydantic model
  (see below); (4) `export_public_key() -> str` — returns the base64-encoded Ed25519
  public verification key, triggering keypair loading if not yet initialised, enabling
  out-of-band receipt verification by third parties who hold only the public key.
  The class is importable from `sovereign_core.gateway`.

- **`SovereignBoundaryResponse` — Structured Pydantic Return Model** (`gateway.py`):
  Added `SovereignBoundaryResponse(BaseModel)` as the return type of `sieve_and_sign()`,
  replacing the previous `SieveAndSignResult` TypedDict.  Exposes two attributes:
  `content: str` (the minimized payload) and `receipt: Dict[str, Any]` (the
  `ForensicReceipt` envelope).  Provides attribute-style access (`result.content`,
  `result.receipt`) rather than error-prone dict key lookups, and is validated by
  Pydantic on construction.  Exportable from `sovereign_core.gateway`.

- **`SovereignGateway.__init__` — Defensive Key Directory Creation** (`gateway.py`):
  Added `os.makedirs(key_path.parent, exist_ok=True)` as the first operation in
  `__init__`, executed before `SovereignKeyManager` is constructed.  Prevents
  unhandled `FileNotFoundError` crashes in stateless containerized environments
  (Docker, Kubernetes, CI runners) where the `.keys/` directory is not guaranteed
  to pre-exist.

- **`SovereignGateway.export_public_key()` — Public Verification Key Export**
  (`gateway.py`): New method that returns the base64-encoded raw 32-byte Ed25519
  public key for the gateway's signing identity.  Triggers lazy keypair loading if
  the `SovereignKeyManager` has not yet been initialised.  The returned value is
  identical to `receipt["public_key"]` in every `ForensicReceipt` minted by this
  gateway instance, enabling third-party receipt verification against a distributed
  public key without access to the node secret.

- **`SieveAndSignResult` — TypedDict Legacy Export** (`gateway.py`):
  The original `SieveAndSignResult` TypedDict remains exported from
  `sovereign_core.gateway` for backwards compatibility.  New code should use
  `SovereignBoundaryResponse` instead.

- **`ROADMAP.md` — Sovereign Systems Specification Strategic Plan** (repo root):
  Added `ROADMAP.md` documenting the full long-range architectural trajectory.
  Summarises the four completed phases (cryptographic identity, Prose Tax
  optimization, testing infrastructure, `SovereignGateway` interface).  Specifies
  Phase 5 drop-in framework middleware targets — `sovereign-fastapi`
  (`SovereignMiddleware` ASGI), `sovereign-django` (`SovereignDjangoMiddleware`),
  `sovereign-langchain` (`SovereignCallbackHandler` + `SovereignChain`), and
  `sovereign-llamaindex` (`SovereignNodePostprocessor` + `SovereignQueryTransform`)
  — with copy-paste-accurate usage examples for each.  Specifies Phase 6
  verification key protocol targets — `PublicKeyBundle` export format,
  `sovereign-verify` CLI, `SovereignKeyManager.rotate_keypair()` succession
  receipts, and `FederatedVerifier` multi-node trust registry.  Closes with four
  non-negotiable guiding invariants (local-first, tamper-evidence, zero regression,
  dependency minimalism).

- **Dual-Receipt Cryptographic Manifest — Prose Tax Telemetry Fusion** (`gateway.py`
  — `SovereignGateway.sign`): When `sieve()` has been called on a gateway instance
  prior to `sign()`, the Prose Tax optimization metrics accumulated in the internal
  `SessionContext` are injected into the `ForensicReceipt` metadata envelope under
  the `"prose_tax_summary"` key before the Ed25519 signature is computed.  This
  means the signed manifest atomically covers both the cleansed payload and its
  FinOps cost profile: `raw_token_count` and `optimized_token_count` (estimated
  via UTF-8 byte-density heuristic), `tokens_eliminated` (the raw minus optimized
  delta), `tax_savings_percentage` (percentage reduction rounded to four decimal
  places), and `total_tokens_saved` (accumulated savings across all `sieve()` calls
  on the same gateway instance).  When `sieve()` has not been called, `sign()`
  behaves identically to its prior form and omits the `"prose_tax_summary"` key.
  Any post-issuance mutation of the telemetry values — like all other metadata
  fields — causes `verify_receipt` to return `False`.

- **`TestSovereignGateway` — High-Level Interface Test Suite** (`test_gateway.py`):
  Added 11 functional test cases covering the `SovereignGateway` interface:
  (1) `test_sieve_returns_string` — confirms the return type is always `str`;
  (2) `test_sieve_strips_filler_tokens` — verifies full filler removal pipeline
  (`hi`, `please`, `just`) yields the expected clean output;
  (3) `test_sieve_normalizes_whitespace` — confirms multi-space runs collapse to
  single spaces; (4) `test_sieve_preserves_guarded_phrases` — asserts that
  instructional directives like `make sure to` pass through unmarred;
  (5) `test_sign_returns_forensic_receipt_fields` — checks all five required
  `ForensicReceipt` fields (`timestamp`, `payload_hash`, `public_key`,
  `signature`, `metadata`) are present; (6) `test_sign_receipt_is_cryptographically_valid`
  — performs a full `SovereignKeyManager.verify_receipt` pass against the
  original payload, proving end-to-end signing fidelity;
  (7) `test_sign_receipt_fails_on_tampered_payload` — asserts that `verify_receipt`
  returns `False` when the payload is mutated after signing, confirming
  tamper-evidence; (8) `test_sign_receipt_metadata_contains_source_tag` — verifies
  the `source` annotation is stamped into every receipt;
  (9) `test_gateway_sieve_and_sign_atomic` — calls `sieve_and_sign()` in one
  invocation and asserts the result is a `SovereignBoundaryResponse` with
  `result.content == "help me now"`, all five receipt fields present via
  `result.receipt`, `verify_receipt` passes, and `prose_tax_summary` is fused
  and arithmetically consistent — using attribute access throughout;
  (10) `test_gateway_export_public_key` — asserts the returned string is base64-
  decodable to exactly 32 bytes (Ed25519 key size) and matches `receipt["public_key"]`
  in receipts from the same gateway instance;
  (11) `test_gateway_fuses_prose_tax_metadata` — calls `sieve()` then `sign()` on
  the same gateway instance and asserts that `receipt["metadata"]["prose_tax_summary"]`
  contains all five keys, carries exact arithmetic-verified token counts
  (`raw=6`, `optimized=2`, `eliminated=4` for the 26-byte input `"hi please just
  help me now"`), and that the enriched envelope still passes full Ed25519
  `verify_receipt` verification — confirming dual-receipt manifest integrity.

- **pytest-asyncio Loop Scope Configuration** (`pyproject.toml`):
  Added `asyncio_default_fixture_loop_scope = "function"` to
  `[tool.pytest.ini_options]` to explicitly configure per-function fixture loop
  scoping and silence the pytest-asyncio loop deprecation warning.  The
  `pytest-asyncio` dependency floor is tightened from `>=0.23.0` to `>=1.0.0`
  to explicitly track the installed v1.x major release block.

### Changed

- **Documentation — `PHILOSOPHY.md` "Intended Usage" Sync**: Updated the
  illustrative code block in the "Intended Usage" section to mirror the
  implemented `SovereignGateway` API exactly.  The import is corrected to
  `from sovereign_core.gateway import SovereignGateway`; `signing_key` is
  updated to the canonical `.keys/sovereign_identity.pem` default; `gateway.sieve()`
  is prefixed with `await` to match its `async def` signature; and the
  `receipt.uuid` attribute reference is replaced with `receipt["payload_hash"]`
  to reflect `ForensicReceipt`'s TypedDict access pattern.

- **Documentation — `README.md` Primary Interface Showcase**: Added a new
  "Primary Developer Interface: `SovereignGateway`" section at the top of the
  implementation target block.  Provides a complete, copy-paste-ready usage
  example — including gateway initialization, async `.sieve()` call, `.sign()`
  receipt minting, and an independent `verify_receipt` check — establishing
  `SovereignGateway` as the canonical integration pattern for downstream
  application code.

### Fixed

- **`SovereignGateway.__init__` — Key Filename Contract** (`gateway.py`):
  When a custom `signing_key` path such as `"vault/node-alpha.pem"` was passed,
  only the parent directory was forwarded to `SovereignKeyManager`, which
  silently replaced the caller-specified filename with the hardcoded default
  `sovereign_identity.pem`.  The fix resolves the full absolute path via
  `Path(...).resolve()`, constructs `SovereignKeyManager` with the parent
  directory as before, then immediately overrides `private_key_path` and
  `public_key_path` on the key manager instance to honour the exact filename
  provided.  The companion `.pub` path is derived via `key_path.with_suffix(".pub")`.

- **`SovereignGateway.sieve_and_sign` — Concurrent Session Contention**
  (`gateway.py`): The original one-shot macro called `self.sieve()` (which
  wrote Prose Tax metrics into the shared `self._session`) and then `self.sign()`
  (which read those metrics back from the same shared session).  Under concurrent
  async load on the same gateway instance, a second request's `sieve()` could
  overwrite the session before the first request's `sign()` read it, silently
  fusing the wrong metrics into the receipt.  Fixed by having `sieve_and_sign`
  call `process_prose_tax` directly, capturing the returned `OptimizationReceipt`
  in the coroutine's local scope, and forwarding it to `sign()` via the new
  `prose_tax_receipt=` keyword argument.  The `sign()` method now branches on
  this argument: when present it reads only from the locally scoped receipt
  (concurrent-safe); when absent it falls back to session state (preserving the
  existing two-step `sieve()` + `sign()` workflow).

- **Documentation — Pydantic Attribute Access Alignment** (`PHILOSOPHY.md`,
  `README.md`): All usage examples updated from legacy dict subscripts
  (`result["content"]`, `result["receipt"]`) to `SovereignBoundaryResponse`
  Pydantic v2 attribute access (`result.content`, `result.receipt`).

- **Test Filesystem Isolation — Sieve-Only Key Path Hardening** (`test_gateway.py`):
  Four sieve-only tests updated to accept `tmp_path` and pass an isolated
  temporary key path to `SovereignGateway(signing_key=...)`, preventing a stray
  `.keys/` directory from being created at the workspace root during test sweeps.

- **`total_tokens_saved` — Cumulative FinOps Semantic Alignment** (`gateway.py`,
  `test_gateway.py`): The `total_tokens_saved` field in `prose_tax_summary` carried
  different semantics across the two execution tracks.  The sequential `sieve()` +
  `sign()` path correctly read the cumulative session accumulator (all prior sieve
  calls on the instance), while the concurrent-safe one-shot `sieve_and_sign()` path
  incorrectly emitted only the per-call delta (`raw - opt`), making the field
  meaningless for multi-call FinOps accounting.  Fixed by adding a
  `total_tokens_saved: Optional[int] = None` keyword argument to `sign()`.
  `sieve_and_sign()` now reads `self._session.get("prose_tax_total_savings", 0)` once
  — immediately after `process_prose_tax` has updated the accumulator — and forwards
  it via `total_tokens_saved=`.  The `sign()` method uses this explicit value in the
  one-shot branch and continues reading the session accumulator in the two-step branch,
  guaranteeing identical cumulative semantics across both tracks.  Verified by the new
  `test_gateway_telemetry_semantic_consistency` test case.

- **`SovereignGateway.export_public_key()` — Private Attribute Encapsulation**
  (`gateway.py`): The original implementation guarded keypair loading with
  `if not self._key_manager._private_key:`, directly piercing the private
  `._private_key` slot and treating any falsy value as an unloaded keypair.  The fix
  replaces this with a `try/except` wrapper around `self._key_manager.public_key`:
  the property raises `RuntimeError('Keypair not loaded.')` when the keypair has
  not been initialised, which is caught to trigger `load_or_generate_keypair()`
  before the key is returned.  This eliminates the private attribute dependency and
  uses only the documented public interface.

## [0.7.0] - 2026-05-21

### Added

- **Phase 3 Testing Framework — `pytest` + `pytest-asyncio` Infrastructure** (root `pyproject.toml`):
  Added `pytest>=8.0.0` and `pytest-asyncio>=0.23.0` as workspace-level development dependencies
  and configured `asyncio_mode = "auto"` under `[tool.pytest.ini_options]` so async test coroutines
  are discovered and executed without explicit per-function decorator decoration.

- **`tests/` Topology — `packages/sovereign-core/tests/`**: Established a unified test topology
  by creating the `tests/` directory (with `__init__.py`) inside `packages/sovereign-core/`.
  The `testpaths` ini option is set to `["packages"]` so that `uv run pytest` discovers
  any future package's `tests/` directory automatically without requiring manual path additions.

- **`test_crypto.py` — Cryptographic Identity Test Suite** (`packages/sovereign-core/tests/`):
  Authored 19 fully type-hinted, docstring-backed test cases across four classes covering:
  (1) `TestGreenfieldKeypairGeneration` — Ed25519 keypair generation returns valid 32-byte key
  material, persists both PEM files, and exposes a consistent `public_key` property;
  (2) `TestFchmodPermissionLocking` — private key file is non-empty, 0o600-permissioned on POSIX
  (skipped on Windows with a platform-guard assertion), leaves no staging temp file after
  `os.replace()`, and routes correctly through the `hasattr(os, 'fchmod')` branch;
  (3) `TestWrongSecretKeyLoad` — wrong, missing, and blank `SOVEREIGN_NODE_SECRET` values each
  raise `RuntimeError` before any key bytes are read or written;
  (4) `TestForensicReceiptMinting` — freshly minted receipts contain all five required fields,
  pass full key-pin/payload-hash/Ed25519 verification, carry a 64-byte signature, and return
  `False` for tampered metadata, rogue key pins, altered payloads, and explicit metadata round-trips.

- **`test_gateway.py` — Prose Tax Optimization Layer Regression Suite**
  (`packages/sovereign-core/tests/`): Authored 31 fully type-hinted, async-native regression
  test cases across five classes insulating `process_prose_tax` from future regressions:
  (1) `TestFillerTokenStripping` — `hi`, `hello`, `please`, `certainly`, `of course`,
  `I hope this`, and `just` are cleanly stripped while the remainder of each sentence is preserved;
  (2) `TestHighIntegrityPhrasePassthrough` — `make sure to`, `hello-world`, `hi-fi`,
  `certainly-not`, `be sure to`, and `absolutely-not` pass through the regex library completely
  unmarred, each exercising a distinct lookahead or lookbehind guard;
  (3) `TestCaseSensitiveHeadingPreservation` — identical duplicate headings are collapsed to one
  copy via the `\\1` backreference, while headings that differ in case, level, or position are
  preserved; (4) `TestWhitespaceNormalization` — multiple spaces collapse to one, orphaned spaces
  before commas and periods are absorbed, and multi-filler removal leaves no double-space artifact;
  (5) `TestDictMessageListStructure` — list-of-dicts input returns a list, all non-content keys
  are forwarded unmodified, content fields are minimized, messages without string content pass
  through intact, list length is preserved, and the `SessionContext` receives the
  `prose_tax_receipt` entry after each pass.

- **Automated `.env` Environment Hydration** (`__main__.py`): Added
  `python-dotenv>=1.0.0` as a runtime dependency and wired `load_dotenv()` at
  the absolute top of the node entrypoint.  `SOVEREIGN_NODE_SECRET` (and any
  other environment overrides) can now be specified in an uncommitted `.env`
  file at the repository root, eliminating the need to export variables
  manually before running `uv run sovereign-node`.

- **Prose Tax Optimization Layer — Phase 2 Scaffold** (`gateway.py`):
  Initialized the core architectural scaffold for the Prose Tax Optimization
  Layer inside `gateway.py`.  Added `OptimizationReceipt`, a Pydantic model
  tracking `raw_token_count`, `optimized_token_count`, and
  `tax_savings_percentage` for each compression pass.  Added the async gateway
  entry point `process_prose_tax(payload, context)`, which executes three
  sequential phases: Text Minimization (stripping conversational filler,
  greeting tokens, hedging preambles, and redundant markdown decoration via a
  compiled regex library), Local Token Approximation (UTF-8 byte-density
  heuristic; no external tokenizer), and Ledger Accumulation (writing the
  `OptimizationReceipt` and running savings total directly into
  `context.variables` so every downstream `ForensicReceipt` envelope carries a
  complete optimization audit trail).

### Fixed

- **Prose Tax — Duplicate Heading Deduplication Case-Sensitive** (`gateway.py`
  — `_FILLER_PATTERNS`): Removed ``re.IGNORECASE`` from the duplicate-heading
  compilation so that headings are only collapsed when their text and
  capitalisation match exactly.  The previous case-insensitive form silently
  mutated distinct headings that differed only in case (e.g.
  ``## Setup`` followed by ``## setup`` would have been collapsed to one).

- **Prose Tax — Whitespace Normalization Pass** (`gateway.py` —
  ``process_prose_tax``): Added a post-substitution whitespace normalization
  step in both the string and list-message processing branches.  After all
  filler patterns are applied, consecutive spaces are collapsed to a single
  space and orphaned spaces before punctuation characters are removed.  This
  cleans up double-space and space-punctuation artifacts that filler-word
  removal leaves behind.

- **Crypto — Class Docstring Aligned with Descriptor-Level Architecture**
  (`crypto.py` — ``SovereignKeyManager``): Updated the class-level docstring
  to replace the stale reference to path-based ``os.chmod`` with an accurate
  description of the current descriptor-level ``os.fchmod`` approach and its
  ``os.chmod`` fallback for platforms that lack ``fchmod``.

- **Runtime — E402 noqa Guards Applied** (`__main__.py`): Appended
  ``# noqa: E402`` suppression comments to every import line that follows the
  early ``load_dotenv()`` invocation, ensuring PEP 8 module-level import-order
  rules do not flag the intentional environment-hydration-first pattern.

- **Prose Tax — Preamble Pattern Greedy Wildcard Removed** (`gateway.py` —
  `_FILLER_PATTERNS`): Corrected the ``"I hope"`` preamble regex to remove
  the greedy trailing ``.*`` wildcard that consumed the entire remainder of
  the line, causing silent deletion of any sentence content that followed the
  phrase.  The pattern now terminates at the word boundary immediately after
  the subject pronoun (``\bI hope (?:this|that|you)\b``), stripping only the
  literal preamble token itself and leaving all trailing content on the same
  line perfectly intact.  The capturing group ``(this|that|you)`` is converted
  to a non-capturing group ``(?:this|that|you)`` as the capture was unused.

- **Prose Tax — List Payload Structure Preserved on Return** (`gateway.py` —
  ``process_prose_tax``): Stabilized the list-input branch to return the
  original message list structure with each ``"content"`` field individually
  minimized, instead of collapsing the structure into a flat joined string.
  Messages without a string ``"content"`` field are forwarded unmodified.
  All other dict keys within each message are preserved via shallow copy.
  The return type annotation is updated from ``tuple[str, OptimizationReceipt]``
  to ``tuple[Union[str, List[Dict[str, Any]]], OptimizationReceipt]`` to
  reflect list-in/list-out structural parity.  Token approximation metrics
  continue to use the joined minimized string internally.

- **Prose Tax — Affirmation Alternation Unified Under Global Lookahead**
  (`gateway.py` — `_FILLER_PATTERNS`): Unified the ``certainly`` and
  ``absolutely`` affirmation tokens inside a non-capturing group so that the
  ``(?![-\w])`` negative lookahead applies collectively to both options.  The
  previous naked ``\bcertainly\b|\babsolutely\b`` form carried no lookahead
  guard, making it vulnerable to corrupting hyphenated compound words
  (e.g. ``"certainly-not"`` → ``"-not"``).  Pattern replaced with
  ``\b(?:certainly|absolutely)\b(?![-\w])``.  The ``of course`` phrase-anchor
  and the dual-guarded ``sure`` sub-pattern are unchanged.  Inline comment
  restructured into a three-bullet breakdown documenting each sub-pattern's
  distinct guard rationale.

- **Prose Tax — Greeting Alternation Unified Under Global Lookahead**
  (`gateway.py` — `_FILLER_PATTERNS`): Unified the greeting alternation
  string inside a non-capturing group so that the ``(?![-\w])`` negative
  lookahead guard applies collectively to all four tokens.  The previous
  per-token form ``\bhi\b(?![-\w])|\bhello\b|\bhey\b|\bgreetings\b``
  guarded only ``hi``, leaving ``hello``, ``hey``, and ``greetings``
  unguarded and vulnerable to mangling hyphenated compound strings (e.g.
  ``"hello-world"`` → ``"-world"``).  Pattern replaced with
  ``\b(?:hi|hello|hey|greetings)\b(?![-\w])``.  Inline comment updated to
  document the grouping rationale.

- **Prose Tax — `sure` Affirmation Directive Guard** (`gateway.py` —
  `_FILLER_PATTERNS`): Corrected the ``sure`` affirmation pattern to enforce
  absolute mechanical symmetry between comment promises and regex behavior.
  The previous ``\bsure\b(?![-\w])`` lookahead-only guard did not protect
  instructional directives like ``"make sure to"`` because the lookahead
  examines only what follows ``sure`` (a space, which passes the guard), not
  what precedes it.  Added paired negative lookbehinds
  ``(?<!make )(?<!be )`` before ``\bsure\b`` so that ``sure`` is suppressed
  only when it appears as a standalone colloquial modifier, never as the
  anchor of an instructional phrase.  Both lookbehinds are fixed-width (5
  characters) and honor ``re.IGNORECASE`` at runtime.

- **Prose Tax — Hedging Adverb Coverage Expanded** (`gateway.py` —
  `_FILLER_PATTERNS`): Added ``actually``, ``basically``, and ``probably``
  to the hedging-adverb entry, each guarded with an explicit
  ``(?![-\w])`` negative lookahead to insulate technical documentation prose
  and hyphenated compound forms from accidental optimization corruption.

- **Repository — `.mailmap` Attribution Anchor** (`.mailmap`): Confirmed
  a root ``.mailmap`` file is present with a single clean entry mapping the
  ``noreply@anthropic`` committer identity to
  ``Ken W. Alger <kenalger@comcast.net>``, standardizing Git author graph
  attribution across all local workspace environments.

- **Prose Tax — `hi` Greeting Boundary Hardened** (`gateway.py` —
  `_FILLER_PATTERNS`): Hardened the conversational ``hi`` greeting regex
  boundary with a negative lookahead guard to prevent corruption of technical
  terms like ``hi-fi``, ``hi-res``, and ``hi-DPI``.  The previous ``\bhi\b``
  pattern matched the ``hi`` prefix in hyphenated compounds because ``\b``
  fires at the alphanumeric/hyphen boundary.  The pattern is updated to
  ``\bhi\b(?![-\w])``, suppressing the match when ``hi`` is immediately
  followed by a hyphen or word character.

- **Repository Tracking — `CLAUDE.md` Removed from Git Index**
  (`.gitignore` / git index): Removed ``CLAUDE.md`` from the active git
  tracking index to properly honor the repository ``.gitignore`` boundary.
  The file is already listed in ``.gitignore`` but remained tracked due to a
  prior ``git add``; ``git rm --cached CLAUDE.md`` cleanly removes it from
  the index while leaving the physical file intact on disk.

- **Prose Tax — `process_prose_tax` Returns Optimized Text Payload**
  (`gateway.py` — `process_prose_tax`): Corrected `process_prose_tax` to
  explicitly return the optimized text payload string alongside its tracking
  analytics receipt.  The previous return type was ``OptimizationReceipt`` only,
  making the cleansed text inaccessible to downstream consumers.  The function
  now returns ``tuple[str, OptimizationReceipt]`` — ``(minimized_text, receipt)``
  — and the docstring ``Returns:`` block and type annotation are updated to
  match.

- **Prose Tax — `sure` Affirmation Boundary Hardened** (`gateway.py` —
  `_FILLER_PATTERNS`): Hardened the conversational ``sure`` affirmation regex
  boundary with a negative lookahead guard to protect technical compound words
  from corruption.  The previous ``\bsure\b`` pattern matched the ``sure``
  prefix in hyphenated compounds such as ``sure-fire`` and ``sure-footed``
  because ``\b`` fires at the alphanumeric/hyphen boundary.  The pattern is
  updated to ``\bsure\b(?![-\w])``, suppressing the match when ``sure`` is
  immediately followed by a hyphen or word character.

- **Prose Tax — Bold/Italic Markdown Deduplication Bounds** (`gateway.py` —
  `_FILLER_PATTERNS`): Refined bold/italic markdown deduplication patterns to
  preserve valid formatting tags while stripping only degenerate token
  repetitions.  The previous ``(\*{1,3})(\s*\1)+`` pattern was overbroad: it
  matched the closing ``**`` of one bold span and the opening ``**`` of the next
  when only whitespace separated them (e.g. ``**foo** **bar**`` was corrupted
  to ``**foo**bar**``).  The pattern is replaced with ``\*{4,}|_{4,}``, which
  targets only runs of four or more consecutive bare asterisks or underscores.
  All valid Markdown wrappers (``*italic*``, ``**bold**``, ``***bold-italic***``,
  ``__double__``, ``___triple___``) are left completely untouched.

- **`example.env` — Synthetic Placeholder Sanitization** (`example.env`):
  Sanitized ``example.env`` to deploy explicit synthetic placeholders,
  eliminating credential-leak hazards for local node installations.  The
  previous value ``"super-secret-passphrase"`` was realistic enough to be copied
  verbatim into a live ``.env`` file without triggering alarm.  The value is
  replaced with the screaming placeholder
  ``"YOUR_SECRET_PASSPHRASE_HERE_DO_NOT_USE_THIS_LITERAL_VALUE"`` and the
  inline comment now explicitly warns that using this literal string in a live
  environment breaks cryptographic node identity uniqueness bounds.

- **Legacy Migration Path — Descriptor-Level `os.fchmod` Symmetry** (`crypto.py`
  — `load_or_generate_keypair`): Upgraded the legacy migration block to match
  the greenfield path's descriptor-level permission model.  The previous
  `os.chmod(tmp_path, 0o600)` call resolved the temp file through the filesystem
  namespace, leaving a TOCTOU race window.  On POSIX hosts the call is now
  `os.fchmod(tmp.fileno(), 0o600)`, operating directly on the open file
  descriptor.  A `hasattr(os, "fchmod")` guard retains `os.chmod` as a portable
  fallback on Windows.  Both key write paths now enforce permissions through an
  identical descriptor-first pattern.

- **Stale `RuntimeError` Docstring Entry Pruned** (`crypto.py` —
  `load_or_generate_keypair`): Removed the `Raises: RuntimeError` entry that
  documented a zero-byte `os.write` return as a disk-full signal.  This guard
  belonged to the manual raw write loop, which was fully replaced by
  `tmp_file.write(pem_bytes)` in v0.5.7.  The `Raises:` block now reflects only
  conditions that can actually be triggered by the current implementation.

- **Prose Tax — Duplicate Heading Deduplication** (`gateway.py` —
  `_FILLER_PATTERNS`): Corrected the duplicate-heading deduplication regex to
  properly preserve a single instance of consecutive matching markdown headers.
  The previous `pattern.sub("", ...)` call erased both the original and the
  duplicate heading entirely.  `_FILLER_PATTERNS` is now a list of
  ``(pattern, replacement)`` pairs; the heading entry uses ``r"\1"`` as its
  replacement so the captured first heading is restored after the match is
  consumed, leaving exactly one clean copy in the output.

- **Prose Tax — Filler Word Boundary Hardening** (`gateway.py` —
  `_FILLER_PATTERNS`): Hardened conversational filler word-stripping boundaries
  to prevent collateral text corruption or mangling of technical prose.  The
  previous ``\bword\b`` anchors were insufficient: ``\bsimply\b`` matched the
  ``simply`` prefix in ``simply-typed``, and ``\bjust\b`` matched inside
  ``just-in-time``, because ``\b`` only transitions on alphanumeric/non-alphanumeric
  boundaries and the hyphen is non-alphanumeric.  Each hedging adverb pattern now
  appends ``(?![-\w])`` — a negative lookahead that prevents a match when the
  token is immediately followed by a hyphen or word character — so only
  standalone colloquial uses are stripped.

## [0.5.8] - 2026-05-21

### Fixed

- **Greenfield Key Generation — Descriptor-Level `os.fchmod` and Buffered
  Stream Write** (`crypto.py` — `load_or_generate_keypair`): Eliminated
  path-based TOCTOU race windows and resolved the I/O layer buffering mismatch
  introduced in v0.5.6.  `os.chmod(tmp_path, 0o600)` was replaced with
  `os.fchmod(tmp_file.fileno(), 0o600)` on POSIX hosts (portable fallback via
  `hasattr(os, "fchmod")`) so permission enforcement operates on the open
  descriptor rather than the filesystem path.  The manual `while remaining_bytes`
  / `os.write` loop was replaced by `tmp_file.write(pem_bytes)`, aligning the
  write path with the high-level `NamedTemporaryFile` stream object and
  delegating short-write recovery to Python's buffered I/O layer.  The
  `tmp_file.flush()` → `os.fsync` → `os.replace` durability pipeline is
  retained unchanged.

## [0.5.7] - 2026-05-21

### Fixed

- **Greenfield Key Generation — Descriptor-Level `os.fchmod` Permission
  Tracking** (`crypto.py` — `load_or_generate_keypair`): Eliminated path-based
  TOCTOU race windows during greenfield key generation by upgrading file
  permission tracking to descriptor-level `os.fchmod` actions.  The previous
  `os.chmod(tmp_path, 0o600)` call resolved the temp file through the filesystem
  namespace, leaving a window between path resolution and permission application
  where a symlink substitution or concurrent rename could redirect the chmod to
  an unintended target.  On POSIX hosts the block now calls
  `os.fchmod(tmp_file.fileno(), 0o600)`, operating directly on the open file
  descriptor and bypassing the filesystem namespace entirely.  On Windows, where
  `os.fchmod` is unavailable, `os.chmod(tmp_path, 0o600)` is retained as a
  portable fallback via `hasattr(os, "fchmod")`.

- **Greenfield Key Generation — Native Buffered Stream Write** (`crypto.py` —
  `load_or_generate_keypair`): Streamlined filesystem I/O operations by
  substituting low-level raw `os.write` tracking with native
  `NamedTemporaryFile` buffered streaming blocks.  The previous manual
  `while remaining_bytes` loop called `os.write` directly on the file
  descriptor, mixing raw syscall-level I/O with a high-level `NamedTemporaryFile`
  object and creating a layered buffering mismatch where Python's internal stream
  buffer and the raw write path could diverge.  The block now calls
  `tmp_file.write(pem_bytes)` directly on the `NamedTemporaryFile` instance;
  Python's buffered I/O layer handles short-write recovery internally, and the
  subsequent `tmp_file.flush()` drains the stream buffer to the kernel before
  `os.fsync` commits it to physical storage.

## [0.5.6] - 2026-05-21

### Fixed

- **Greenfield Key Path — Atomic `NamedTemporaryFile` Creation Pattern**
  (`crypto.py` — `load_or_generate_keypair`): Eliminated process-wide umask
  leaks by migrating the greenfield key path to an atomic `NamedTemporaryFile`
  creation pattern.  The previous `os.umask(0)` / `os.open` sequence mutated
  the process-wide umask between the capture and restore calls, creating a race
  window where concurrent threads could create files with world-readable
  permissions.  The block now uses `tempfile.NamedTemporaryFile(dir=…,
  delete=False)`, which the Python runtime creates with `0o600` permissions
  internally without touching the process umask.  The fully written temp file
  is promoted over the final path via `os.replace()`, and an `except` block
  removes the temp file on any failure so no partial material is left on disk.

- **Greenfield Key Path — Strict Byte-Tracking `os.write` Loop**
  (`crypto.py` — `load_or_generate_keypair`): Protected key persistence against
  filesystem resource constraints by implementing a strict byte-tracking write
  loop over raw `os.write` boundaries.  A single `os.write` call is not
  guaranteed to consume the full buffer under resource pressure; a short write
  silently truncates the key file with no exception raised.  The block now
  iterates over the remaining bytes, re-submitting the unwritten tail on each
  pass, and raises `RuntimeError` explicitly on a zero-byte return (disk full
  or broken pipe) before that condition can produce a partial or empty identity
  file on disk.

- **Greenfield Key Generation — Explicit `os.chmod` Permission Symmetry**
  (`crypto.py` — `load_or_generate_keypair`): Hardened greenfield key generation
  by injecting an explicit, umask-independent `os.chmod(..., 0o600)` guard onto
  the atomic temp-file path, achieving perfect permission symmetry with the
  legacy migration architecture.  Although `tempfile.NamedTemporaryFile` targets
  `0o600` by default, this behaviour is not guaranteed across all host
  configurations and Python runtime versions.  The explicit `os.chmod` call
  is placed immediately after `tmp_path` is captured and before any bytes are
  written, inside the existing `try/except` wrapper so a permission failure
  triggers the same temp-file cleanup and re-raise path as any other pre-replace
  error.  Both the migration and greenfield paths now enforce file security
  through an unconditional `os.chmod` regardless of the inherited process
  environment.

## [0.5.4] - 2026-05-21

### Fixed

- **Greenfield Key Generation — Process Umask Isolation** (`crypto.py` —
  `load_or_generate_keypair`): Enforced process umask isolation around
  `os.open` boundaries during greenfield key generation to guarantee
  umask-independent `0o600` file permissions.  The `os.open` mode argument is
  ANDed with the bitwise complement of the current process umask at the kernel
  level, meaning a non-zero umask could silently strip permission bits from the
  newly created inode.  The block now captures the active umask with
  `old_umask = os.umask(0)` immediately before the `os.open` call and restores
  it with `os.umask(old_umask)` immediately after the file descriptor is
  obtained, so the `0o600` mode is applied unconditionally regardless of the
  inherited process environment.

- **Greenfield Key Generation — Pre-Close `os.fsync` Durability** (`crypto.py`
  — `load_or_generate_keypair`): Hardened greenfield key initialization
  durability by executing an explicit `os.fsync` on the newly generated file
  descriptor before closure.  Without this call, the kernel's write-back cache
  may not commit private key bytes to physical storage before the file descriptor
  is closed, leaving an empty or partial key file on disk after a power fault.
  `os.fsync(fd)` is now called after `os.write` and before `os.close(fd)` inside
  a `try/finally` block that guarantees the descriptor is always released, making
  the greenfield path's durability guarantee equivalent to the atomic migration
  path.

## [0.5.3] - 2026-05-21

### Fixed

- **Filesystem Durability and Creation-Time Permission Hardening** (`crypto.py`
  — `load_or_generate_keypair`): Hardened filesystem durability by injecting
  pre-rename `os.fsync` controls on atomic migration tasks and establishing
  strict creation-time permission bounds on greenfield key generation.  In the
  atomic migration path, `os.fsync(tmp.fileno())` is now called after
  `tmp.flush()` and before `os.replace()`, guaranteeing that encrypted PEM
  bytes are physically committed to storage before the rename promotes the temp
  file over the original key.  In the new-keypair generation path, the private
  key file is now opened via `os.open(path, O_WRONLY | O_CREAT | O_TRUNC,
  0o600)` wrapped with `os.fdopen`, so the inode is created with `0o600`
  permissions as its first filesystem operation.  The previous pattern of
  `open(..., "wb")` followed by `os.chmod` left a window where the file existed
  with inheritable process umask permissions before the restriction was applied.

## [0.5.2] - 2026-05-21

### Changed

- **Graceful Legacy Keypair Migration** (`crypto.py` —
  `load_or_generate_keypair`): Hardened the PEM identity loading sequence to
  automatically detect, warn, and seamlessly upgrade legacy unencrypted
  keypairs to password-encrypted storage without breaking the operator
  migration path.  On first boot after upgrading, the node attempts to open
  the existing `sovereign_identity.pem` with the active
  `SOVEREIGN_NODE_SECRET` passphrase.  If that raises `TypeError` or
  `ValueError` (the signature of an unencrypted legacy file), a second attempt
  is made with `password=None`.  Success triggers an advisory warning to
  `stderr` and an immediate in-place rewrite of the PEM under
  `BestAvailableEncryption`; subsequent boots load the now-encrypted file
  silently.  If both attempts fail, a `RuntimeError` is raised with explicit
  rotation guidance rather than exposing the raw library exception.

### Security

- **Key Self-Attestation Forgery Elimination** (`crypto.py` —
  `verify_receipt`; `router.py` — `dispatch`; `__main__.py` —
  `_audit_receipt`): `verify_receipt` previously accepted any receipt whose
  Ed25519 signature was internally valid, including receipts minted by a rogue
  keypair that embedded its own public key.  The method now accepts an optional
  `expected_public_key` parameter; when supplied, the receipt's embedded
  `public_key` field is compared against it before any cryptographic operation
  is attempted.  The router passes `self.key_manager.public_key` at every
  post-mint defense check and `_audit_receipt` performs an additional explicit
  string comparison, emitting a distinct fraud-alert message to `stderr` to
  distinguish identity-forgery from general seal tampering.

- **Atomic Legacy Key Migration** (`crypto.py` — `load_or_generate_keypair`):
  The previous migration path opened `sovereign_identity.pem` directly with
  `open(..., "wb")`, truncating the only copy of the unencrypted key before the
  encrypted replacement was ready.  A crash between truncation and the final
  write left the node with an empty PEM and no recovery path.  The rewrite now
  stages the encrypted PEM to a temporary file in the same directory (guaranteeing
  same-filesystem atomicity), applies `os.chmod(0o600)` before writing any bytes,
  then promotes the temp file over the original via `os.replace()`.  A `finally`
  block removes the temp file on any failure path so no partial material is left
  on disk.

## [0.5.1] - 2026-05-21

### Fixed

- **Pipeline Happy-Path Audit Gap** (`__main__.py` — `execute_runtime_node`):
  `_audit_receipt` was only reachable through the retry branch, meaning the
  first successful `"download"` dispatch advanced to Step 2 without any
  cryptographic seal or `execution_success` check.  The call has been moved
  outside the retry `if` block so it executes unconditionally on whichever
  receipt — original or recovery — is carried forward.  The documentation
  invariant ("every receipt is audited before the pipeline advances") now
  holds on every code path.

### Security

- **Phantom Field Closure — `payload_hash` Explicit Equality Assertion**
  (`crypto.py` — `verify_receipt`): `verify_receipt` reconstructed the
  canonical signing manifest using a locally re-derived `payload_hash` rather
  than the value stored in `receipt["payload_hash"]`.  This allowed an
  adversary to replace the stored hash with any arbitrary string; the
  signature check would still pass because the manifest was built from the
  correct re-derived value.  The method now derives `expected_payload_hash`
  and immediately returns `False` when it does not match
  `receipt["payload_hash"]` byte-for-byte, before any Ed25519 operation is
  attempted.  The canonical manifest in Step 2 is then built from
  `receipt["payload_hash"]`, which has been confirmed to be consistent with
  the original payload.

## [0.5.0] - 2026-05-21

### Added

- **`_audit_receipt` Helper** (`__main__.py`): Extracted cryptographic audit
  logic into a dedicated `_audit_receipt(receipt, payload, label)` function.
  Centralises `SovereignKeyManager.verify_receipt` and `execution_success`
  checks, eliminating duplicated abort logic across pipeline and single-tool
  call paths.
- **`SovereignKeyManager._resolve_node_secret`** (`crypto.py`): New private
  helper that reads and validates the `SOVEREIGN_NODE_SECRET` environment
  variable, raising a descriptive `RuntimeError` immediately when the secret is
  absent or blank rather than silently falling back to plaintext storage.
- **Google-Style Docstrings**: Added complete `Args:`, `Returns:`, and
  `Raises:` documentation blocks to every public class, method, and async tool
  function across `crypto.py`, `gateway.py`, `router.py`, and `__main__.py`.
- **Explicit PEP 484 Type Annotations**: Applied complete return-type and
  parameter annotations to all previously untyped functions, including
  `Dict[str, Any]` signatures on async tool callbacks and `-> None` on
  side-effect-only methods.

### Changed

- **`LocalRuntimeRouter.dispatch` Return Type** (`router.py`): Changed from
  `ForensicReceipt` to `tuple[ForensicReceipt, Dict[str, Any]]`.  The
  execution payload is now returned alongside the receipt so call sites can
  independently reconstruct the signed manifest for re-verification without
  duplicating internal router logic.
- **`__main__.py` Dispatch Call Sites**: All three `await router.dispatch(...)`
  calls now unpack the 2-tuple `(receipt, payload)`.  Receipt auditing is
  delegated to `_audit_receipt` at every pipeline step.

### Fixed

- **Unverified Envelope Propagation** (`router.py`): `dispatch` now calls
  `SovereignKeyManager.verify_receipt(receipt, execution_payload)` immediately
  after minting and raises `RuntimeError` if the seal check fails, preventing
  a corrupted receipt from reaching the caller.

### Security

- **Full Envelope Signing** (`crypto.py` — `generate_receipt` /
  `verify_receipt`): The Ed25519 signature previously covered only the raw
  serialised payload, leaving `metadata` (including `execution_success`),
  `timestamp`, and `payload_hash` unsigned and mutable after issuance.  The
  signature now covers a canonical JSON manifest binding all three fields;
  `verify_receipt` reconstructs the identical manifest before checking the
  signature, so any post-issuance mutation of the envelope returns `False`.
- **Encrypted Private Key Storage** (`crypto.py` — `load_or_generate_keypair`):
  Replaced `serialization.NoEncryption()` with
  `serialization.BestAvailableEncryption(passphrase)` when writing the PKCS8
  PEM to disk.  The passphrase is derived from the mandatory
  `SOVEREIGN_NODE_SECRET` environment variable; the node refuses to initialise
  when the variable is absent.  Existing unencrypted PEM files are rejected at
  load time and must be rotated.

## [0.4.1] - 2026-05-20

### Fixed
- **Retry Sequence Ledger Collision**: Corrected an api-level architectural bug where failed dispatches left the tracking index frozen. `execution_depth` now increments unconditionally for every single dispatch transaction attempt, completely preventing cryptographic index collisions or duplicate ledger positions during external loop recovery cycles.

## [0.4.0] - 2026-05-20

### Added
- **Symmetric CLI Failure Signaling**: Integrated strict termination controls across all CLI code paths inside `__main__.py`. Single-tool mode invocations now cleanly mirror the pipeline execution path by forcing an explicit non-zero exit status (`click.Abort()`) upon runtime faults, completely eliminating silent error masking.
- **Isolated Standalone Diagnostic Hydration**: Enhanced `analyze_stored_data` to gracefully intercept standalone debugging operations. If an empty execution boundary is detected, the runtime fallback-hydrates a baseline diagnostic telemetry stream, keeping standalone CLI options functional for out-of-band testing.

### Fixed
- **Ledger Sequence Collision**: Corrected an off-by-one ledger depth assignment flaw within `LocalRuntimeRouter.dispatch`. Stamping indices are now securely locked down *prior* to successful tool mutations, ensuring chronological integrity (`0`-indexed tracking) and preventing signature index duplication across success-to-failure state handoffs.

## [0.3.8] - 2026-05-20

### Fixed
- **Forensic Receipt Depth Collision**: Fixed an off-by-one ledger assignment where `execution_depth` was captured post-mutation. Stamped index allocations are now secured prior to state updates, preserving unique timeline signatures across success/failure transitions.
- **Standalone CLI Invocation Context**: Upgraded `analyze_stored_data` to support standalone context exploration options, fallback-hydrating diagnostic baselines smoothly to prevent standalone invocation crashes.

## [0.3.7] - 2026-05-20

### Fixed
- **Asymmetric CLI Failure Reporting**: Resolved a telemetry reporting gap by introducing explicit validation checks within the single-tool CLI execution path inside `__main__.py`. Single tool failures now print the forensic receipt and cleanly terminate with a non-zero exit status (`Aborted!`), ensuring visibility across containerized and shell orchestrators.
- **Tracking Metrics Integrity**: Reinforced `execution_depth` handling inside `router.py` to prevent post-increment capture pollution, keeping transactional session indexes stable across variable failure scenarios.

## [0.3.6] - 2026-05-20

### Fixed
- **Pipeline Failure Obscurity**: Implemented fail-fast circuit evaluation inside `__main__.py`. Upstream receipts are now immediately verified post-dispatch, gracefully aborting execution chains on failures and preventing misleading downstream errors.
- **Dead Fallback Elimination**: Streamlined `_calculate_stable_arguments_hash` inside `router.py` by removing redundant `try/except` error routing since string serialization coercion is covered natively via `default=str`.


## [0.3.5] - 2026-05-20

### Fixed
- **Public Key Serialization Encoding**: Resolved a `TypeError` crash in `SovereignKeyManager.get_base64_public_key` by ensuring public key raw byte extractions consume a valid `PublicFormat.Raw` enumeration parameter rather than a private key formatter target.

## [0.3.4] - 2026-05-20

### Fixed
- **Core Receipt Instability**: Eradicated the final remaining instance of Python's process-unstable `hash()` call inside `SovereignKeyManager.generate_receipt` (`crypto.py`). Upgraded `payload_hash` generation to use a deterministic SHA-256 hex digest to ensure forensic receipts are cross-process stable.


## [0.3.3] - 2026-05-20

### Fixed
- **Session State Leakage**: Fixed a side-effect bug where `execution_depth` was incremented before tool completion. State mutations now deferred until post-execution confirmation, preventing counter inflation on tool exceptions.
- **CLI Configuration Regression**: Restored dynamic Click terminal configuration bindings (`--tool`, `--resource-id`, `--session-id`) across the async runtime architecture, removing hardcoded script blocks.
- **Dead Import Pruning**: Stripped out an unused `SovereignKeyManager` dependency declaration from `__main__.py`.

## [0.3.2] - 2026-05-20

### Fixed
- **Process Hash Instability**: Eliminated Python's native `hash()` function inside `LocalRuntimeRouter.dispatch` due to runtime seed volatility. Replaced it with a canonical, key-sorted SHA-256 hex-digest loop.
- **Complex Argument Type Crash**: Resolved an unhashable `TypeError` regression when passing complex data arrays (`lists`, `dicts`) to runtime tools by using JSON serialization schemas prior to hashing.

## [0.3.1] - 2026-05-20

### Security
- **Exposed Secret Remediation**: Excluded the `.keys/` cryptographic directory from version control tracking inside `.gitignore` and untracked compromised development certificates.

### Fixed
- **Wildcard Import Runtime Crash**: Fixed a `TypeError` in `sovereign_core.__init__` by casting the `SessionContext` object inside `__all__` to a compliant string literal.
- **Forensic Hash Regression**: Restored the `arguments_hash` generation step inside the asynchronous `LocalRuntimeRouter` pipeline to prevent duplicate receipt structural hashes across varying parameter executions.

## [0.3.0] - 2026-05-20

### Added
- **Asynchronous Execution Architecture**: Upgraded the execution pipeline within `LocalRuntimeRouter` to handle non-blocking, cooperative concurrent tool patterns via `asyncio`.
- **Stateful Session Primitives**: Introduced the `SessionContext` model to track ephemeral state variables, variable scoping, and sequential transaction counts (`execution_depth`) across tool chains.
- **Inter-Tool Data Dependency Support**: Validated state mutation flows where secondary tools seamlessly digest and process outputs cached in volatile memory by preceding tools within the same execution session.

### Changed
- **Pipeline Signatures**: Modified the router verification mechanism to embed `session_id` and transactional metrics cleanly inside the finalized cryptographic payload receipts.

## [0.2.0] - 2026-05-20

### Added
- **Cryptography Implementation**: Added `sovereign_core.crypto` featuring native `cryptography` library integration for asymmetric Ed25519 key derivation.
- **Identity Persistence**: Added secure on-disk keypair caching inside a restricted `.keys/` directory (with `0o600` file permissions) to persist node identities across runtime restarts.
- **Forensic Verification Ledger**: Implemented `ForensicReceipt` (`TypedDict`) payload minting and a strict `ReceiptSchema` validation layer via Pydantic to ensure provenance integrity.
- **Local Dispatch Router**: Created `LocalRuntimeRouter` in `sovereign_runtime.router` to securely register local tool routines, wrap execution outputs, and map immutable cryptographic signatures over results.
- **Interactive CLI Context**: Updated the main executable entry-point (`sovereign-node`) to demonstrate a complete mock payload hash dispatch and automated receipt logging directly to the console.

### Fixed
- **Build Backend Alignment**: Shifted child workspaces from `hatchling` to `setuptools` to completely mitigate editable-link mapping failures (`build_editable` exceptions) within virtual workspaces.
- **Package Layout Discovery**: Added explicit `src-layout` path tracking configuration under `[tool.setuptools.packages.find]` to resolve `ModuleNotFoundError` anomalies on Windows runtime environments.
- **Namespace Cleanups**: Streamlined internal eager imports inside package `__init__.py` files, eliminating stale placeholder structures (`IngestionBoundary`, `PreFlightRouter`) that stalled initialization boundaries.
- **Python Toolchain Binding**: Pinned the root workspace execution target cleanly to Python `3.12` using `uv python pin` to eliminate host configuration skew against global `3.11` fallbacks.