# Sovereign Systems Specification — Strategic Roadmap

This document tracks the long-range architectural trajectory of the Sovereign Systems SDK and Suite.
Each phase represents a discrete, shippable capability layer built on the invariants
established by the previous phase.

---

## Completed Phases

### Phase 1 — Cryptographic Identity Foundation
- Ed25519 keypair generation with `BestAvailableEncryption` passphrase-protected PEM storage
- `ForensicReceipt` TypedDict with full-manifest Ed25519 signing (`timestamp` + `payload_hash` + `metadata`)
- `SovereignKeyManager.verify_receipt` with key-pin assertion, phantom-field guard, and signature check
- Atomic temp-file promotion (`os.replace`) with descriptor-level `os.fchmod` permission enforcement

### Phase 2 — Prose Tax Optimization Layer
- `process_prose_tax` async gateway with filler-phrase regex library, byte-density token heuristic, and ledger accumulation
- `OptimizationReceipt` tracking `raw_token_count`, `optimized_token_count`, and `tax_savings_percentage`
- `SessionContext` Pydantic model for stateful, cross-call optimization metric accumulation

### Phase 3 — Testing Infrastructure
- Full `pytest` + `pytest-asyncio` workspace test topology under `packages/*/tests/`
- 60-case suite across `test_crypto.py` (19 cases) and `test_gateway.py` (41 cases)
- `asyncio_default_fixture_loop_scope = "function"` configured; `pytest-asyncio >= 1.0.0` floor

### Phase 4 — SovereignGateway High-Level Interface
- `SovereignGateway` class with `async sieve()`, `sign()`, and `async sieve_and_sign()` methods
- `SovereignBoundaryResponse` Pydantic model returned by `sieve_and_sign()` with `.content` and `.receipt` attributes
- Prose Tax telemetry fused into `ForensicReceipt.metadata["prose_tax_summary"]` before Ed25519 signing
- Defensive `os.makedirs` on key directory at gateway initialization for containerized environments
- `export_public_key()` returning the base64-encoded Ed25519 public key for out-of-band verification

### Phase 7 — Standalone Token Economics Primitives (`sovereign-sieve`) — Shipped ✓

Extracted `sovereign-sieve` into a zero-dependency standalone micro-utility package for
framework-agnostic Prose Tax reduction.  Allows scripts, background workers, and pipeline
ingestors to aggressively filter structural text noise and mitigate Prose Tax outside an
HTTP ASGI/WSGI context.

```python
from sovereign_sieve import pure_sieve, sieve_with_metrics

# Drop-in string cleaner — no web framework or ML library required
clean = pure_sieve("Hello! Please just help me analyze this dataset.")
# → "! help me analyze this dataset."

# Cleaner with immediate FinOps telemetry
result = sieve_with_metrics("Hi! I hope this helps. Please just run the pipeline.")
print(result.text)                   # → "! helps. run the pipeline."
print(result.tax_savings_percentage) # e.g. 66.6667 (%)
```

**Delivered:**

* [x] `pure_sieve(payload: str) -> str` — pure synchronous Prose Tax filler-phrase cleaner;
  no I/O, no shared state, zero external dependencies
* [x] `sieve_with_metrics(payload: str) -> SieveOutput` — sieve pass with `raw_token_count`,
  `optimized_token_count`, and `tax_savings_percentage` computed via UTF-8 byte-density heuristic
* [x] `SieveOutput` dataclass with `text`, `raw_token_count`, `optimized_token_count`, and
  `tax_savings_percentage` fields
* [x] `packages/sovereign-sieve/pyproject.toml` declaring `sovereign-sieve` as a workspace member
  at version `1.1.0` with zero runtime dependencies
* [x] 67-case test suite in `packages/sovereign-sieve/tests/test_sieve.py` covering determinism,
  idempotency, all filler categories, edge cases, Unicode, malformed inputs, and metrics arithmetic

---

## Phase 5 — Drop-In Framework Middleware

**Target:** Zero-configuration `SovereignGateway` integration for the four dominant Python AI
and web frameworks, delivered as thin adapter packages under `packages/`.

### 5.1 FastAPI Middleware (`sovereign-fastapi`) — Shipped ✓

A `SovereignMiddleware` ASGI class that intercepts every inbound JSON request body, runs
`sieve_and_sign()` on the target payload field, and injects the resulting
`SovereignBoundaryResponse` into the request state before the route handler fires.
The sealed `ForensicReceipt` is surfaced on every outbound response via two dedicated
headers: `X-Sovereign-Receipt-Signature` (the base64 Ed25519 signature) and
`X-Sovereign-Tokens-Saved` (the cumulative FinOps savings counter).

```python
from fastapi import FastAPI
from sovereign_fastapi import SovereignMiddleware

app = FastAPI()
app.add_middleware(SovereignMiddleware, signing_key=".keys/sovereign_identity.pem")

```

Key deliverables:

* Request-body interception via transparent `_CachedRequest.wrapped_receive` body overwrite before handler dispatch
* `X-Sovereign-Receipt-Signature` response header carrying the base64-encoded Ed25519 signature
* `X-Sovereign-Tokens-Saved` response header carrying the cumulative FinOps savings counter
* Automatic binding of the sealed receipt to `request.state.sovereign_receipt` for in-route auditability
* Optional strict mode: returns HTTP 422 on any interception error (missing field, JSON parse failure)
* Configurable field selector for non-root payload extraction (e.g. `payload_field="text"`)

**Delivered:**

* [x] Full-lifecycle ASGI body interception, local-silicon context sieving, and cryptographic signing.
* [x] Inbound proxy alignment via dynamic `Content-Length` byte-length recalculation.
* [x] Structured diagnostic observability logging for non-strict failure modes.

### 5.2 Django Middleware (`sovereign-django`)

A `SovereignDjangoMiddleware` class conforming to Django's `get_response` middleware contract.
Wraps `process_request` to sieve inbound POST bodies and attach the `SovereignBoundaryResponse`
to `request.sovereign`.  Compatible with Django 4.x and 5.x.

```python
# settings.py
MIDDLEWARE = [
    "sovereign_django.SovereignDjangoMiddleware",
    ...
]
SOVEREIGN_SIGNING_KEY = ".keys/sovereign_identity.pem"

```

Key deliverables:

* `request.sovereign.content` and `request.sovereign.receipt` available in every view
* Settings-driven configuration via `SOVEREIGN_SIGNING_KEY` and `SOVEREIGN_STRICT_MODE`
* Async-compatible via Django's async middleware protocol

### 5.3 LangChain Integration (`sovereign-langchain`)

A `SovereignCallbackHandler` implementing `BaseCallbackHandler` that intercepts
`on_llm_start` events, runs `sieve_and_sign()` on the prompt strings, and writes the
`ForensicReceipt` to the chain's run metadata before the LLM call is dispatched.
Provides a `SovereignChain` wrapper that applies the sieve-and-sign pass transparently.

```python
from sovereign_langchain import SovereignCallbackHandler

handler = SovereignCallbackHandler(signing_key=".keys/sovereign_identity.pem")
llm = ChatOpenAI(callbacks=[handler])

```

Key deliverables:

* `on_llm_start` interception with Prose Tax sieve applied to all prompt strings
* ForensicReceipt written to `run_manager.metadata["sovereign_receipt"]`
* `SovereignChain` LCEL-compatible wrapper for `pipe`-style integration
* Receipt export to a configurable append-only JSONL ledger file

### 5.4 LlamaIndex Integration (`sovereign-llamaindex`)

A `SovereignNodePostprocessor` and `SovereignQueryTransform` pair that apply the
sieve-and-sign pipeline to retrieved node text and query strings respectively, before
they are assembled into the final LLM prompt.

```python
from sovereign_llamaindex import SovereignNodePostprocessor

postprocessor = SovereignNodePostprocessor(signing_key=".keys/sovereign_identity.pem")
query_engine = index.as_query_engine(node_postprocessors=[postprocessor])

```

Key deliverables:

* `SovereignNodePostprocessor` applying `sieve()` to each retrieved `TextNode`
* `SovereignQueryTransform` applying `sieve_and_sign()` to query strings
* Prose Tax savings metrics surfaced in the LlamaIndex response metadata
* Compatible with `VectorStoreIndex`, `SummaryIndex`, and custom retrievers

### 5.5 Streaming Request & Response Boundaries (Extension Path)

* [ ] Architectural specification for asynchronous generator token streams (`StreamingResponse`).
* [ ] Out-of-band verification tracing: alternative background task worker or trailing chunk aggregator to handle Server-Sent Events (SSE) where HTTP headers cannot be mutated mid-stream.

---

### PyPi

* [x] `sovereign-core` package
* [x] `sovereign-fastapi` package
* [x] `sovereign-sieve` package

---

## Phase 6 — Verification Key Protocol

**Target:** A first-class public key distribution and multi-party receipt verification
mechanism, enabling downstream consumers and auditors to independently authenticate
ForensicReceipts without access to the signing node's private key material.

### 6.1 Public Key Export and Distribution — Shipped ✓

Extend `SovereignGateway.export_public_key()` with a structured `PublicKeyBundle`
export format that carries the base64-encoded key, a self-signed attestation receipt,
a UTC issuance timestamp, and an optional node identifier string.

```python
bundle = gateway.export_public_key_bundle()
# bundle.public_key   — base64 Ed25519 key
# bundle.attestation  — self-signed ForensicReceipt proving key ownership
# bundle.issued_at    — UTC ISO 8601 timestamp
# bundle.node_id      — optional human-readable node label

gateway.save_public_key_bundle(".keys/bundle.json", node_id="node-alpha")
```

**Delivered:**

* [x] `PublicKeyBundle` Pydantic v2 model in `crypto.py` with `model_dump_json()` serialization; `node_id` defaults to `None`
* [x] `SovereignGateway.export_public_key_bundle(node_id=None)` minting a self-signed `ForensicReceipt` attestation that proves private-key ownership
* [x] `SovereignGateway.save_public_key_bundle(path, node_id=None)` writing the bundle to disk as UTF-8 JSON

### 6.2 Stateless Receipt Verification CLI — Shipped ✓

A `sovereign-verify` CLI entrypoint (shipped as part of `sovereign-core`) that
accepts a receipt JSON file and a base64-encoded Ed25519 public key string and exits
`0` on verified, `1` on tampered, without requiring access to any private key or
node secret.

```bash
sovereign-verify --receipt receipt.json --public-key <base64-encoded-public-key>
# Verified  ✓  payload_hash: 4fec03e7...

```

### 6.3 Key Rotation and Succession — Shipped ✓

A structured key rotation flow in `SovereignKeyManager` that generates a new keypair,
mints a signed succession receipt (signed by the old key, containing the new public key),
and atomically promotes both the private-key PEM and public-key PEM via a transactional
staging loop.  The succession receipt provides a cryptographically auditable chain of
identity handoff.

```python
# Capture the pre-rotation key from a trusted source before rotating
pre_rotation_key = manager.public_key

receipt = manager.rotate_keypair()
# receipt["previous_public_key"]  — base64 key active before rotation
# receipt["new_public_key"]       — base64 key active after rotation
# receipt["rotation_timestamp"]   — UTC ISO 8601 timestamp
# receipt["succession_signature"] — Ed25519 signature by the outgoing private key

SovereignKeyManager.verify_succession(receipt, pre_rotation_key)  # → True
```

**Delivered:**

* [x] `SuccessionReceipt` TypedDict in `crypto.py` with `previous_public_key`, `new_public_key`, `rotation_timestamp`, and `succession_signature`
* [x] `SovereignKeyManager.rotate_keypair() -> SuccessionReceipt` generating a fresh Ed25519 keypair, signing the canonical rotation payload with the outgoing private key, and atomically promoting both the new `.pem` and `.pub` via a two-phase hardened write: a transactional staging loop (both files staged before either is promoted) followed by a promotion phase with `SovereignStorageError` rollback (second `os.replace` failure restores the original `.pem` atomically)
* [x] `SovereignKeyManager.verify_succession(receipt, trusted_previous_public_key) -> bool` static method enabling standalone auditor-side verification of any rotation event without live key material; the mandatory `trusted_previous_public_key` anchor prevents self-referential signature forgery
* [x] `SovereignStorageError` custom exception raised exclusively when the promotion phase partially commits, carrying a human-readable consistency-preservation confirmation

### 6.4 Multi-Node Federated Verification

A `FederatedVerifier` class that holds a registry of trusted `PublicKeyBundle` objects
and can verify a `ForensicReceipt` against any registered node identity, enabling
cross-node audit workflows where receipts from multiple sovereign nodes must be
validated against a shared trust registry.

Key deliverables:

* `FederatedVerifier` with `register(bundle)`, `verify(receipt, payload)`, and
`trusted_keys() -> list[str]` methods
* JSON-serializable trust registry for persistence across process restarts
* Configurable strict mode: require key-pin match against a specific registered bundle

---

## Phase 8 — Write-Side Custody Ledger (`sovereign-ledger`) — Shipped ✓

**Target:** A dedicated, lightweight, local-first storage substrate designed specifically
to enforce Write-Side Custody by indexing and safeguarding `ForensicReceipts` at the
exact millisecond of ingestion.

Provides local-first systems with an append-only, tamper-evident transactional trace to
shield compliance audits from post-hoc database mutation or log-injection vulnerabilities.

```python
from sovereign_ledger import SovereignLedger

ledger = SovereignLedger(db_path=".keys/sovereign_audit.db")
ledger.append_receipt(receipt, sieved_content)
assert ledger.verify_ledger_integrity()  # → True on an untampered chain

```

**Delivered:**

* [x] `SovereignLedger` — zero-external-dependency SQLite engine with WAL mode, NORMAL
  synchronous enforcement, and strict foreign-key locks applied at initialization.
* [x] `forensic_ledger` table with nine typed columns and a `UNIQUE` constraint on
  `payload_hash` to guarantee single-ingestion invariant.
* [x] Engine-level `BEFORE UPDATE` and `BEFORE DELETE` SQL triggers that call
  `RAISE(ROLLBACK, 'Write-Side Custody violation: ...')`, aborting mutation and
  rolling back the entire enclosing transaction from any SQLite client regardless
  of whether it uses the Python class or opens the file raw.
* [x] SHA-256 parent-hash chain rooted at a static genesis constant, linking every
  appended row to its cryptographic predecessor.
* [x] `verify_ledger_integrity() -> bool` — O(n) sweep that re-derives the expected
  parent hash for each row and returns `False` on any detected breach.
* [x] `SovereignStorageError` exception (exported from `sovereign_ledger`) raised by
  `_get_conn()` when called on a closed instance, preventing use-after-close access to
  released connection handles.
* [x] 60-case adversarial test suite covering trigger enforcement (internal and external
  client), `RAISE(ROLLBACK)` transaction-abort semantics confirming post-hoc injection
  via COMMIT is impossible, out-of-band corruption detection across all eight data columns
  (signature, payload hash, parent hash, timestamp, raw/optimized token counts, savings
  percentage, sieved content), field-boundary delimiter collision resistance, mid-chain
  deletion detection, injected-row detection, full lifecycle correctness, closed-instance
  guard (`SovereignStorageError` on post-`close()` access), concurrent write serialisation
  via `BEGIN IMMEDIATE` (8-thread stress tests across separate-instance and
  shared-instance file-backed scenarios confirming zero chain fragmentation; plus a
  shared-instance in-memory scenario confirming correct per-thread DDL bootstrapping —
  each thread obtains an independent isolated SQLite store, so chain linearity
  assertions apply only to file-backed topologies), connection-registry purge verification
  (`close()` releases all thread-local handles to zero), and cross-thread in-memory
  `RuntimeWarning` emission (`test_in_memory_cross_thread_emits_runtime_warning`).

---

## Phase 9 — Bare-Metal Write-Side Custody Sensor Layer (`sovereign-sensor`) — Shipped ✓

**Target:** Extend Write-Side Custody enforcement to bare-metal microcontroller sensor nodes
running MicroPython, sealing each observation at the exact point of data genesis before any
network transit or cloud ingestion occurs.

```python
from sovereign_sensor import bootstrap_sensor_node

# Auto-selects hardware or software crypto driver at runtime
envelope = bootstrap_sensor_node("node-temperature-01", "/flash/keys/node.key")
wire_bytes = envelope.seal("2026-06-16T00:00:00Z", {"sensor": "temp", "value": 21.4})
# → b'{"alg":"hmac-sha256","d":{"sensor":"temp","value":21.4},"n":"node-temperature-01","q":1,"s":"<hex-sig>","t":"2026-06-16T00:00:00Z","v":1}'
```

**Delivered:**

* [x] `SovereignCryptoDriver` HAL base class (`interface.py`) — enforces `initialize_hardware()`,
  `sign(payload: bytes) -> bytes`, and `algorithm() -> str` contract stubs via `NotImplementedError`
  without importing the `abc` module, keeping the MicroPython heap footprint minimal.
* [x] `SovereignEnvelope` (`envelope.py`) — accepts a `sequence_file` VFS path (default
  `".sovereign_sequence"`) and restores any previously persisted counter from that file on
  construction, enabling the monotonic sequence to resume across hardware reboots; a `ValueError`
  raised by `int(data.strip())` — the signature of a truncated 0-byte file left by a mid-write
  power interruption — is caught, a diagnostic is printed, and the counter resets to 0 so device
  initialization completes rather than aborting; a successfully parsed negative integer is clamped
  to 0, preventing an adversarially written or filesystem-corrupted negative counter from producing
  `q < 0` wire frames; `OSError` on file open degrades gracefully without raising; seals observations in a seven-step deterministic pipeline: (1) monotonic sequence index
  computed transiently as `self._sequence + 1` and bound into the preimage — the in-memory
  counter and VFS sequence file are advanced only after the driver returns raw signature bytes
  without raising, guaranteeing that a `sign()` failure never consumes a sequence position or
  introduces a gap in the on-disk custody timeline; VFS write failures degrade gracefully to
  RAM-only tracking; (2) algorithm identifier queried from driver; (3) payload keys
  alphabetically sorted and serialized via
  `json.dumps(..., separators=(',', ':'), sort_keys=True, ensure_ascii=False)`, producing raw
  UTF-8 preimage bytes regardless of dict key insertion order across CPython and bare-metal
  MicroPython — `ensure_ascii=False` eliminates the `\uXXXX`-vs-raw-UTF-8 split-brain
  divergence that would cause cross-platform HMAC verification to fail on any payload
  containing characters outside U+007F; (4) `node_id`, `timestamp`, and `algorithm`
  independently encoded to UTF-8 byte arrays, each prefixed with its UTF-8 byte count (not
  Unicode character count), and concatenated into the versioned preimage
  `1|{len(node_bytes)}:{node_id}|{len(time_bytes)}:{timestamp}|sequence|{len(algo_bytes)}:{algorithm}|canonical_payload`;
  byte-count prefixes close delimiter injection across all three variable-length fields —
  a crafted algorithm identifier embedding `|` produces an ambiguous preimage without the
  prefix, enabling cross-algorithm signature reuse; prefixes also close multi-byte encoding
  ambiguity (a receiver using character-count semantics parses field boundaries at the wrong
  byte offset for any field with characters outside U+007F); (5) preimage signed by driver
  returning raw binary bytes; (6) sequence state committed and VFS flushed; (7) signature
  hex-encoded via `binascii.hexlify`; all fields serialized into a 7-key ultra-minified JSON
  frame `{"v", "n", "t", "q", "alg", "d", "s"}` via
  `json.dumps(..., sort_keys=True, ensure_ascii=False)`, freezing the alphabetical key
  sequence and enforcing raw UTF-8 wire encoding independently of MicroPython
  allocator-driven insertion order.
* [x] `bootstrap_sensor_node(node_id, private_key_path, sequence_file) -> SovereignEnvelope`
  (`__init__.py`) — inspects `sys.platform.lower()` to route between `ESP32HardwareDriver`
  (on `"esp32"` targets) and `SoftwareFallbackDriver` (all other platforms); calls
  `initialize_hardware()` inside a `try/except NotImplementedError` block via a boolean
  `_hw_not_implemented` flag — the flag is set inside the `except` clause and both the
  fallback warning print and the `SoftwareFallbackDriver` re-initialization execute outside
  the block, preventing the original `NotImplementedError` from chaining into the fallback
  initialization traceback on MicroPython serial consoles; if the selected hardware driver
  raises (skeleton placeholder not yet implemented), a warning is printed and the factory
  rebinds to `SoftwareFallbackDriver` so sealing, sequencing, and VFS layers remain
  exercisable on the workbench; forwards `sequence_file` to the constructed `SovereignEnvelope`
  for VFS counter persistence across reboots.
* [x] `SoftwareFallbackDriver` (`drivers/software_fallback.py`) — HMAC-SHA256 keyed signing
  driver using `hashlib` and `hmac`; reads key material from the VFS path supplied at
  construction during `initialize_hardware()`; `MOCK_KEY_SENTINEL = "/mock/test_gateway.key"`
  is the only path that opts into the fixed deterministic `_MOCK_KEY` stub — any other path
  that cannot be opened raises `RuntimeError` immediately, eliminating silent key substitution
  for production key paths; read bytes are stored in a local `key_bytes: bytes` variable before
  assignment — if zero bytes are read, `ValueError` is raised immediately rather than keying HMAC
  with `b""`, which would be deterministic across all nodes sharing the same empty-file failure
  and provide no cryptographic uniqueness; `sign()` guards against uninitialized calls via
  `if not self._initialized` and raises `RuntimeError` immediately; returns raw 32-byte
  HMAC-SHA256 digest bytes (no encoding); declares `algorithm() -> "hmac-sha256"`; uses
  package-relative import (`from ..interface`); not suitable for production custody chains.
* [ ] `ESP32HardwareDriver` (`drivers/esp32_hardware.py`) — v0.1 HAL skeleton establishing
  the class contract and import surface for the ESP32 on-chip ECC accelerator; declares
  `algorithm() -> "ecdsa-p256"` as a forward-looking algorithm identifier; `sign()` raises
  `NotImplementedError` — full register-level engineering is deferred to the next sprint
  and this driver must not be wired into any production custody chain in its current state.
* [x] `packages/sovereign-sensor/pyproject.toml` — zero runtime dependencies; targets Python 3.12
  for desktop test compatibility; restricts internal library code to standard MicroPython built-ins
  (`json`, `sys`, `machine`, `hashlib`, `hmac`, `binascii`); Trove classifiers corrected to valid PyPI
  identifiers: `"Programming Language :: Python :: 3"`, `"Programming Language :: Python :: 3 :: Only"`,
  `"Programming Language :: Python :: Implementation :: MicroPython"`, `"Topic :: System :: Hardware"`.
* [x] `packages/sovereign-sensor/README.md` — distribution documentation asset satisfying the
  `pyproject.toml` `readme` field; includes architectural overview, HAL driver table,
  7-step sealing pipeline description, minimal usage examples, and invariants table.
* [x] 33-case desktop validation test suite (`tests/test_sensor.py`) across three classes
  (`TestBootstrap`: 4 cases, `TestDriverGuard`: 3 cases, `TestEnvelopeSeal`: 26 cases) verifying
  platform auto-detection via public `algorithm()` contract, driver initialization confirmation,
  bootstrap falls back to `SoftwareFallbackDriver` (with warning) when hardware driver raises
  `NotImplementedError` (simulated via `sys.platform` patch to `"esp32"`), `sign()` raises
  `RuntimeError` before `initialize_hardware()` is called, `initialize_hardware()` raises
  `RuntimeError` for any non-sentinel path that cannot be opened (silent key substitution
  eliminated), `initialize_hardware()` raises `ValueError` when the key file exists but contains
  zero bytes (empty-key guard), bytes return type, valid JSON parse, exact 7-key envelope
  structure (`v`, `n`, `t`, `q`, `alg`, `d`, `s`), protocol version integer type, sequence
  counter starts at 1 (isolated via `tmp_path` sequence file), monotonic sequence increment
  across 3 consecutive calls (isolated via `tmp_path`), algorithm field value, field identity
  preservation, HMAC-SHA256 hex signature length (64 chars), ultra-minified output, cross-instance
  determinism (two fresh instances at q=1 produce identical output), payload-isolated signature
  divergence, hex-only character set, `sort_keys=True` canonicalization produces byte-identical
  signatures for semantically equivalent payloads with inverted key insertion order (preimage
  invariance), full raw wire-frame byte equality for payloads with inverted key insertion order
  (transport-layer determinism independent of allocator ordering), HMAC signature divergence
  when distinct key files supply different secret material (key material participation verified),
  graceful recovery from a 0-byte sequence file left by a mid-write power interruption (counter
  resets to 0 without aborting initialization), sequence counter resumption from the
  persisted VFS value after a simulated hardware reboot (replay-protection continuity across
  power cycles), and length-prefixed preimage delimiter injection immunity — `("abc|def", "ghi")`
  and `("abc", "def|ghi")` are verified to produce distinct HMAC signatures, confirming that
  cross-identity preimage collision is closed; non-ASCII `node_id` byte-length prefix
  integrity — `"noëud"` (5 chars, 6 UTF-8 bytes) produces a sealed signature that exactly
  matches an independently computed HMAC over the byte-count-prefixed preimage, confirming
  that character-count semantics are rejected and cross-platform field boundary parsing is
  correct on all targets; negative sequence counter clamped to zero — `"-42"` written to
  the sequence file produces `q=1` on the first `seal()`, confirming the `q < 0`
  invariant violation is closed; algorithm identifier
  byte-count-prefix delimiter injection immunity — two drivers returning `"hmac|sha256"` and
  `"hmac"` respectively produce distinct HMAC signatures, confirming that a pipe character
  embedded in the algorithm string cannot collapse adjacent preimage fields; an independently
  reconstructed HMAC over the byte-count-prefixed preimage exactly matches the sealed
  signature, verifying the complete preimage format end-to-end; Unicode payload
  serialization without ASCII escaping — `{"sensor": "Nordøst-Ventil", "data": "Ω-Value"}`
  seals without error, the payload round-trips correctly, and the wire bytes contain raw
  UTF-8 characters (`ø`, `Ω`) with no `\uXXXX` escape sequences, confirming
  `ensure_ascii=False` is active on both the preimage and wire frame serialization paths;
  preimage reconstruction tests refactored to use the public `SoftwareFallbackDriver.sign()`
  API rather than accessing the private `_MOCK_KEY` attribute directly. **33 passed, 0 failed.**

---

## Phase 9.5 — Sensor Ingestion Bridge (`sovereign-edge`) — Shipped ✓

**Target:** Introduce a local-first middleware pipeline that intercepts sealed sensor
wire frames from `sovereign-sensor`, applies the `sovereign-sieve` Prose Tax
transformation, and commits a signed `ForensicReceipt` to `sovereign-ledger`.  An
off-grid JSONL buffer absorbs receipts when the ledger is temporarily unreachable, with
background-threaded persistence so the ingestion loop is never blocked on disk I/O.

```python
from sovereign_edge import EdgePipeline
from sovereign_ledger import SovereignLedger

ledger = SovereignLedger(".keys/sovereign_audit.db")
pipeline = EdgePipeline(ledger=ledger, signing_key=".keys/edge_identity.pem")

# Intercept a sealed wire frame from sovereign-sensor
result = pipeline.process(wire_bytes)
# result.payload_hash  — hex SHA-256 receipt identifier
# result.sieved_content — Prose-Tax-minimized observation payload
# result.buffered       — True when ledger was unreachable; receipt queued off-grid

# Replay buffered receipts when the ledger recovers
committed = pipeline.drain_buffer()
```

**Delivered:**

* [x] `SensorFrame` dataclass: deserializes all seven sovereign-sensor wire envelope keys
  (`v`, `n`, `t`, `q`, `alg`, `d`, `s`) from UTF-8 JSON bytes; `text_content()` produces
  deterministic, sort-keyed JSON for canonical sieve input.
* [x] `OffGridBuffer`: durable JSONL-backed queue with background daemon writer thread —
  `__init__` invokes `self._path.parent.mkdir(parents=True, exist_ok=True)` immediately
  after resolving the buffer path, guaranteeing that both the background append and the
  `tempfile` staging directory are available at construction rather than at first write;
  `push()` returns immediately (non-blocking); `flush()` blocks via `Queue.join()` until all
  pending writes are committed to disk; disk write failures (`OSError` during `open` /
  `fsync`) are preserved in a `_write_errors` list under `_count_lock` so no receipt is
  silently discarded on a full disk or read-only filesystem; `size` returns
  `_pending + _committed + len(_write_errors)` under a single lock acquisition with no file
  read, keeping `buffer_depth` accurate across normal writes, write failures, and in-flight
  entries simultaneously; `write_error_count: int` property surfaces the failure indicator
  explicitly; `_drain_lock` provides mutual exclusion between the `push()` enqueue window
  and the entire `drain()` critical section (flush → read → `os.replace` → counter decrement),
  preventing concurrent `push()` calls from writing to a file inode that is being atomically
  replaced and losing the write; `drain()` flushes, merges `_write_errors` entries with
  on-disk entries, sorts the combined list by ascending `metadata["sequence"]` through a
  `_seq_key` helper guarded by `try/except (TypeError, ValueError)` so a non-numeric
  sequence value falls back to `0` rather than aborting the drain (stable sort for FIFO at
  equal keys), atomically clears via `tempfile` → `os.replace`, returns the combined entries
  only when the atomic promotion succeeds (returns `[]` on `OSError` so no downstream ledger
  commits are made against an uncleared buffer; write-error entries are preserved in
  `_write_errors` for the next pass), and decrements `_committed` by the file-entry count
  only so write-error entries — which never accumulated in `_committed` — require no counter
  adjustment; `close()` joins the worker thread then inspects `_write_errors` and raises
  `RuntimeError` if any un-journaled entries remain, forcing the host application to
  acknowledge data loss instead of shutting down silently.
* [x] `EdgePipeline.__init__()` — key directory created with `mode=0o700` at first use and
  explicitly re-enforced via `chmod(0o700)` on every construction, correcting a pre-existing
  directory whose permissions may have been set with a lax umask.
* [x] `EdgePipeline`: four-stage orchestrator (deserialize → sieve → sign → commit);
  sieve stage guarded by `try/except Exception` — on failure falls back to raw
  `text_content()`, sets `tax_savings_percentage=0.0`, and stamps `sieve_fault=True`
  in receipt metadata; receipt metadata carries `"sequence": frame.q` as the drain
  sort key; routes `SovereignStorageError` and `sqlite3.Error` to the off-grid buffer;
  `drain_buffer()` re-queues entries that still cannot reach the ledger.
* [x] `EdgeResult` dataclass: structured return type from `process()` with `payload_hash`,
  `receipt`, `sieved_content`, Prose Tax telemetry fields, and `buffered` flag.
* [x] `EdgePipeline.close()`: best-effort `drain_buffer()` then `OffGridBuffer.close()`;
  pipeline owns buffer lifecycle; `RuntimeError` from un-journaled write errors propagates
  unmodified to enforce clean teardown invariant; `drain_buffer()` wrapped in `try/finally`
  so `OffGridBuffer.close()` is reached even if the drain pass raises unexpectedly.
* [x] `edge_pipeline` fixture refactored to `yield` + `pipeline.close()` teardown:
  background writer thread joined after every test; pytest dependency order ensures
  ledger remains open during the teardown drain pass.
* [x] Ad-hoc pipeline teardown hardened: `TestEdgePipelineBuffering` local instances
  closed in `try/finally`; `TestEdgePipelineDrainBuffer` adds `_buffer.flush()` after
  each `process()` call and closes both `pipeline_a` and `pipeline_b` in `try/finally`
  with recovery ledger closed after `pipeline_b.close()`.
* [x] `process()` sieve-fault fallback: `frame.text_content()` called once before the
  `try` block; both happy and fault paths consume the single `raw_text` reference.
* [x] `OffGridBuffer.close()` shutdown race eliminated: sentinel `queue.put(None)` moved
  inside `_drain_lock` so `push()` and `close()` are fully serialized; no payload can
  be enqueued behind the sentinel; `_worker_thread.join()` remains outside the lock.
* [x] 63-case desktop validation test suite across eight classes (`TestSensorFrame`,
  `TestOffGridBuffer`, `TestEdgePipelineProcess`, `TestEdgePipelineBuffering`,
  `TestEdgePipelineDrainBuffer`, `TestOffGridBufferAsync`, `TestEdgePipelineSieveFault`,
  `TestOffGridBufferWriteErrors`) covering all fortification scenarios: non-blocking
  `push()` with immediate `size` reporting, chronological `drain()` sort by sequence,
  non-integer sequence value tolerance in the sort key guard, `_committed` counter
  accuracy after drain, sieve fault fallback with raw text and `sieve_fault=True`
  metadata, disk write error tracking via `write_error_count`, `size` accuracy under
  disk failure, full `drain()` recovery of write-error entries, `close()` raising
  `RuntimeError` when un-journaled entries remain at shutdown, `EdgePipeline.close()`
  propagating `RuntimeError` when un-journaled write errors survive the drain pass, and
  20-thread concurrent `push()`-vs-`close()` stress test asserting zero orphan entries.

---

## Phase 10 — Isolated Context Vault & Governance Server (`sovereign-vault`)

**Target:** Implement the "Sovereign Vault" architecture as an isolated local orchestration
boundary, delivering a first-class Model Context Protocol (MCP) server for enterprise
boundary containment.

Ensures local Small Language Models (SLMs) and autonomous cloud agents function within a
strictly sandboxed, zero-variance hardware context ring, neutralizing adversarial prompt
injections and data leaks.

```bash
# Registering the Sovereign Vault as a secure local compliance gate
mcp start sovereign-vault --config .config/vault-permissions.json

```

Key deliverables:

* **Sovereign MCP Core:** A standalone Model Context Protocol server enforcing explicit, byte-exact schema and file-system isolation per agent call.
* **Pre-Flight Namespace Router:** An intent-based namespace classifier that prevents tool selection dilution by restricting available runtime tools to a lean, relevant profile dynamically ($O(\text{relevant})$ optimization).
* **Ephemeral Sandbox Management:** Automatic orchestration of zero-shared-memory runtime buffers to guarantee raw context is scrubbed from hardware immediately upon inference closure.

---

## Future Extensions & Ecosystem Blueprints

### Decentralized Trust Anchoring (Hedera Consensus Service Blueprint)

While the Sovereign Systems SDK maintains a strict local-first, zero-network isolation boundary by default, enterprise compliance architectures may require non-repudiation proofs that survive local system compromises.

We propose a decoupled, asynchronous anchoring pattern using the **Hedera Consensus Service (HCS)**:

* **Zero Core Bloat:** The runtime gateway remains local-first and does not block on external consensus loops.
* **State-Stable Proof of Existence:** Downstream background workers can capture the self-contained `ForensicReceipt` objects and emit the `signature` and `payload_hash` to an HCS topic.
* **Immutable Timestamping:** This provides decentralized, consensus-driven proof of time and sequence, preventing back-dating or historical audit tampering by rogue internal administrators.

---

## Guiding Invariants

Every phase must preserve these non-negotiable properties:

1. **Local-first** — No network call, external service, or cloud API is invoked during
sieve, sign, or verify operations.  All cryptographic operations run on local silicon.
2. **Tamper-evidence** — Any post-issuance mutation of a `ForensicReceipt` field
(including `metadata`) must cause `verify_receipt` to return `False`.
3. **Zero regression** — Each phase ships with a complete test suite maintaining
100% pass rate across the entire workspace.  No test may be deleted or marked xfail
to make a phase land.
4. **Dependency minimalism** — `sovereign-core` must never take on a high-compute
dependency (PyTorch, transformers, etc.).  Framework adapters carry their own
optional dependency trees via extras (`pip install sovereign-fastapi[all]`).
