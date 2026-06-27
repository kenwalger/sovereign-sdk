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
pipeline = EdgePipeline(
    ledger=ledger,
    signing_key=".keys/edge_identity.pem",
    sensor_secret=b"<shared-hmac-secret>",
    # allow_unauthenticated=True  # required if sensor_secret is omitted
)

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
* [x] `EdgePipeline.__init__()` — `sensor_secret: str | bytes = b""` parameter: when
  non-empty, `process()` reconstructs the HMAC-SHA256 preimage from the deserialized
  frame fields (matching `SovereignEnvelope.seal()` format exactly) and compares the
  digest against `frame.s` via `hmac.compare_digest`; mismatch raises `ValueError` before
  the sieve or ledger is reached; empty secret disables verification for backwards
  compatibility with unauthenticated deployments.
* [x] `SensorFrame.from_bytes()` — protocol version gate: `if frame["v"] != 1` raises
  `ValueError("Unsupported wire format version …")` immediately after JSON decode,
  before dataclass construction, HMAC verification, or ledger interaction.
  `test_from_bytes_raises_on_unsupported_version` covers the `v=2` rejection path.
  `TestSensorFrame` grows from 11 to 12 cases.
* [x] `OffGridBuffer._dead_letter` — 100-entry eviction cap: `_DEAD_LETTER_MAX = 100`
  constant; both append sites (worker OSError path and `drain()` malformed-line path)
  evict the oldest entry under `_count_lock` when the ceiling is reached, bounding
  heap growth under sustained malformed-payload injection.
* [x] `OffGridBuffer._disk_writer` — non-OSError exception catch and `_worker_failed` flag:
  `except Exception:` added after `except OSError:` to prevent silent thread death;
  affected entry preserved in `_write_errors` / `_dead_letter`; `_worker_failed = True`
  set as the first statement inside the `finally`-block's `_count_lock` section so
  concurrent `push()` callers see the flag before counter updates complete;
  lock-free evacuation loop (`get_nowait()` under `_count_lock` per item, no
  `_drain_lock`) calls `task_done()` for each orphan, eliminating the circular-wait
  deadlock where `drain()` held `_drain_lock` and waited on `queue.join()` while the
  evacuation loop waited for `_drain_lock`; `worker_failed: bool` read-only property
  exposes the flag; `push()` raises `RuntimeError` immediately when the flag is set.
* [x] `SensorFrame.from_bytes()` — strict runtime type validation: `isinstance` checks
  for all seven fields immediately after JSON decode; boolean masquerading as int blocked
  via `and not isinstance(_val, bool)` guard on `v` and `q`; type mismatch raises
  `TypeError` before the version gate, HMAC verifier, sieve, or ledger.
  `test_from_bytes_raises_on_wrong_field_type` covers the `q="not-an-int"` rejection path.
  `TestSensorFrame` grows from 12 to 13 cases.
* [x] `test_worker_non_oserror_failure_does_not_hang` (`TestOffGridBufferWriteErrors`):
  patches `builtins.open` with `RuntimeError`; asserts `flush()` returns, `worker_failed`
  is `True`, `push()` raises, `drain()` recovers entries, `close()` completes cleanly.
  `TestOffGridBufferWriteErrors` grows from 5 to 6 cases.
* [x] `test_drain_concurrent_with_worker_crash_no_deadlock` (`TestOffGridBufferWriteErrors`):
  pushes two items under a `builtins.open` mock that fails on append-mode writes; starts
  `drain()` in a daemon thread; asserts the thread joins within 5 seconds (timeout =
  deadlock); asserts `worker_failed is True`; `close()` completes cleanly.  Guards the
  lock-free evacuation path against future regressions that re-introduce `_drain_lock`.
  `TestOffGridBufferWriteErrors` grows from 6 to 7 cases.
* [x] `OffGridBuffer.close()` — idempotent guard: `if self._worker_thread.is_alive()`
  skips sentinel placement and join on repeated calls, preventing counter corruption
  and indefinite `Queue.join()` block from overlapping teardown paths.
* [x] `EdgePipeline.process()` — HMAC hex case normalisation: `frame.s.lower()` passed
  to `hmac.compare_digest` so uppercase hex from bare-metal hardware drivers is accepted
  without a spurious signature mismatch.
* [x] `EdgePipeline.process()` — sieve-fault fallback guard: ``except Exception:`` catches
  all anomalous sieve plugin faults; ``SystemExit`` and ``KeyboardInterrupt`` inherit from
  ``BaseException`` not ``Exception`` and propagate naturally without an explicit re-raise.
* [x] `test_close_is_idempotent` (`TestEdgePipelineBuffering`): two explicit calls plus
  the fixture teardown third call all complete without deadlock or state corruption.
* [x] `EdgePipeline.process()` — algorithm-gate hard-block: when `sensor_secret` is
  provisioned, `frame.alg != "hmac-sha256"` immediately raises `ValueError`
  (`"Unsupported or unauthenticated algorithm …"`) before the HMAC digest is even
  computed; no frame can bypass verification by spoofing the `alg` field.
* [x] Test suite — private attribute access eliminated: three `_conn.execute()` calls
  replaced with `SovereignLedger.verify_ledger_integrity(expected_tip_hash=...)`;
  `_closed` guard in `mem_ledger` fixture simplified to unconditional `ledger.close()`
  (idempotent); all 13 `EdgePipeline` constructions pass `sensor_secret=_SENSOR_SECRET`.
* [x] `TestOffGridBuffer` and `TestOffGridBufferAsync` — all 16 test methods that
  construct an `OffGridBuffer` directly now close it in `try/finally` teardown, joining
  the background daemon writer thread before the next test begins.
* [x] `README.md` — `sovereign-edge` example extended with `sensor_secret` parameter and
  `try/finally` teardown calling `pipeline.close()` and `ledger.close()`.
* [x] `EdgePipeline.drain_buffer()` — requeue loop hardened: each `push()` call in the
  requeue pass is wrapped in `except RuntimeError:` so all items are iterated before
  raising; `push_failure_count` is accumulated and surfaced in a single
  `RuntimeError("could not re-queue N receipts...")` after the loop; no item is orphaned
  mid-iteration regardless of buffer state.
* [x] `OffGridBuffer.close()` — concurrent-teardown sentinel race eliminated: `is_alive()`
  check moved inside `_drain_lock → _count_lock` and gated on `not self._closed`;
  first caller sets `_closed = True` and places one sentinel; every subsequent caller
  observes `_closed = True` and skips; `_pending` is incremented exactly once regardless
  of concurrent teardown fan-out; `sentinel_placed: bool` flag controls the `join()` call
  so `join()` is only called by the thread that placed the sentinel.
* [x] `test_drain_buffer_survives_push_failure_on_requeue` (`TestEdgePipelineDrainBuffer`):
  patches `append_receipt` (SovereignStorageError) and `_buffer.push` (RuntimeError +
  counter); asserts `push_call_count == 2` and `pytest.raises(RuntimeError, match="could
  not re-queue")`; authoritative guard that both items are attempted, not just the first.
  `TestEdgePipelineDrainBuffer` grows from 5 to 6 cases.
* [x] `test_concurrent_close_no_counter_drift` (`TestOffGridBufferAsync`): 8 threads call
  `buf.close()` simultaneously after push/flush/drain; asserts all threads join within
  5 s (timeout = deadlock) and `buf.size() == 0` (no counter drift from duplicate
  sentinels). `TestOffGridBufferAsync` grows from 7 to 8 cases.
* [x] `EdgePipeline.close()` — dual-failure exception chaining: captured-exception model
  replaces `try/finally`; `drain_buffer()` exception stored in `drain_exc`; if `buffer.close()`
  also raises, the buffer `RuntimeError` is chained via `raise buf_exc from drain_exc` so the
  drain root cause is visible in the traceback; only one raises → normal propagation.
* [x] `OffGridBuffer.drain()` — `drain_read_failed: bool` observable flag: `OSError` on
  `Path.read_text()` sets the flag under `_count_lock` before returning `[]`; caller can
  distinguish filesystem-blocked drain from genuine empty drain; `drain_read_failed` property
  with Sphinx docstring; on-disk entries preserved.
* [x] `SensorFrame` — `@dataclass(frozen=True)`: post-construction field assignment raises
  `dataclasses.FrozenInstanceError`; `d: dict[str, Any]` reference is immutable (contents not
  deep-frozen); no change required in pipeline code because no stage rebinds a frame field.
* [x] `SovereignDoubleFaultError(RuntimeError)` — total-persistence failure sentinel with
  ``receipt: dict[str, Any]`` attribute; raised from `process()` when ledger commit fails
  and the subsequent buffer push also fails; exported from `sovereign_edge.__all__`.
* [x] `OffGridBuffer.drain()` — OSError re-raised (not silently swallowed with `return []`)
  after setting `_drain_read_failed = True` under `_count_lock`; callers receive an explicit
  exception rather than an empty list indistinguishable from a genuine empty drain.
* [x] `EdgePipeline.drain_buffer()` — `list(self._buffer.drain())` wrapped in
  `try/except OSError`; raises descriptive `RuntimeError` chained from the `OSError` so
  operators see the storage-tier failure before any replay is attempted.
* [x] `OffGridBuffer.close()` — OS-exit-gap sentinel guard: ``_worker_running`` flag (set
  ``False`` under ``_count_lock`` before the thread's final return) replaces the
  ``is_alive()`` check in the liveness gate, closing the window where the Python thread
  function has returned but the OS has not yet unregistered the thread; ``close()`` now
  reads ``False`` before the OS marks the thread dead and skips sentinel injection.
  ``test_close_skips_sentinel_during_worker_os_exit_gap`` validates the fix by patching
  ``is_alive()`` to return ``True`` after worker exit.
  ``TestOffGridBufferWriteErrors`` grows from 8 to 9 cases.
* [x] `OffGridBuffer.push()` — TOCTOU liveness-gate fix: ``_worker_failed`` check and
  ``queue.put()`` share a single ``_count_lock`` acquisition so a racer that passes the
  check before a crash cannot enqueue into an abandoned queue after the evacuation loop
  completes; ``test_push_toctou_worker_crash_no_dangling_items`` (16-thread
  ``threading.Barrier`` stress test) asserts ``_pending == 0`` after all threads resolve.
  ``TestOffGridBufferWriteErrors`` grows from 9 to 10 cases.
* [x] `EdgePipeline.process()` — sieve-fault fallback guard broadened to
  ``except Exception:``; ``SystemExit`` and ``KeyboardInterrupt`` inherit from
  ``BaseException`` not ``Exception``, so the broader guard naturally excludes host-level
  abort signals while trapping all anomalous sieve plugin faults (``ArithmeticError``,
  ``LookupError``, etc.) that the prior narrow tuple propagated unhandled.
* [x] `SensorFrame.d` docstring corrected: ``MappingProxyType`` "enforces shallow
  read-only protection on the top-level envelope dictionary keys; nested mutable values
  are not frozen" — removes the implication of deep immutability.
* [x] `test_close_is_idempotent` docstring corrected: references ``_worker_running``
  state flag instead of ``is_alive()``, matching the OS-exit-gap implementation.
* [x] `EdgePipeline.__init__()` — secure-by-default initialization: new
  ``SovereignConfigurationError(ValueError)`` raised when ``sensor_secret`` is empty or
  ``None`` and ``allow_unauthenticated=False`` (the default); new
  ``allow_unauthenticated: bool = False`` keyword parameter allows deliberate opt-out;
  ``SovereignConfigurationError`` exported from ``sovereign_edge.__all__``.
* [x] `OffGridBuffer.drain()` — stale read-failure flag reset: ``self._drain_read_failed``
  set to ``False`` under ``_count_lock`` immediately before each ``Path.read_text()``
  attempt so a prior transient ``OSError`` does not persist as a false-positive after
  filesystem recovery.
* [x] `TestEdgePipelineSecureInit` — four new tests: construction without secret raises
  ``SovereignConfigurationError`` matching ``"allow_unauthenticated=True"``; empty-string
  secret also raises; ``allow_unauthenticated=True`` permits construction; error is
  catchable as ``ValueError`` (inheritance invariant).  New class
  ``TestEdgePipelineSecureInit``: 4 cases.
* [x] `test_drain_read_failed_flag_resets_on_successful_drain`
  (``TestOffGridBufferWriteErrors``): simulates transient ``OSError``
  (``drain_read_failed → True``), then successful drain asserts ``drain_read_failed is
  False``; guards the reset path against future regression.
  ``TestOffGridBufferWriteErrors`` grows from 10 to 11 cases.
* [x] `OffGridBuffer.drain()` — rotation failure raises ``OSError`` (not returns ``[]``):
  bare ``raise`` added at the end of the ``except OSError:`` block so a failed
  ``os.replace`` propagates to the caller after temp-file cleanup; callers can no longer
  confuse a filesystem-blocked rotation with a genuinely empty drain; on-disk entries and
  ``_write_errors`` are preserved for retry.
* [x] `SovereignDoubleFaultError` — ``uncommitted_receipts: list[dict[str, Any]] | None``
  attribute: new optional constructor parameter; ``receipt`` made optional (default
  ``None``); two fault contexts documented: single-receipt (from ``process()``) and
  batch-drain (from ``drain_buffer()``).
* [x] `EdgePipeline.drain_buffer()` — ``try/finally`` for guaranteed unprocessed-entry
  capture; tracked ``failed_requeue_entries`` list (not just count); cascading double fault
  (replay loop crash + buffer push failures) raises ``SovereignDoubleFaultError`` with
  ``uncommitted_receipts`` attached; OSError handler message generalised to "operation
  failed" to cover both read and rotation failures.
* [x] `test_drain_buffer_raises_on_rotation_failure` (``TestEdgePipelineDrainBuffer``):
  patches ``sovereign_edge.buffer.os.replace`` with ``OSError``; asserts ``RuntimeError``
  with ``__cause__ is OSError`` and message matching ``"operation failed"``; then asserts
  a second ``drain_buffer()`` commits exactly 1 receipt (entries survived the failed
  rotation).  ``TestEdgePipelineDrainBuffer`` grows from 8 to 9 cases.
* [x] `SensorFrame.text_content()` — float literal preservation; `_canonicalize_payload` removed:
  the ``_canonicalize_payload`` helper (which coerced every ``float`` whose ``is_integer()``
  returned ``True`` to its ``int`` equivalent) is removed in its entirety; ``text_content()``
  now calls ``json.dumps(dict(self.d), sort_keys=True, separators=(",", ":"), ensure_ascii=False)``
  directly, without any numeric type coercion, so ``float(1.0)`` is serialized as ``"1.0"``
  rather than ``"1"``; the edge-side HMAC preimage is byte-identical to the canonical string
  the sensor passed to its own HMAC-SHA256 digest function, eliminating the class of signature
  verification failures caused by cross-runtime float normalization.
  ``test_text_content_normalizes_integer_valued_floats`` is renamed
  ``test_text_content_preserves_float_literal_format`` (``TestSensorFrame``) and its
  assertion is inverted: asserts ``"1.0" in frame_float.text_content()`` and
  ``frame_float.text_content() != frame_int.text_content()`` — confirming float and int wire
  representations produce distinct preimage strings.
* [x] `EdgePipeline.drain_buffer()` — duplicate eviction on `sqlite3.IntegrityError`:
  ``except sqlite3.IntegrityError: pass`` inserted before the broader
  ``except (SovereignStorageError, sqlite3.Error): requeue.append(...)`` guard; a receipt
  already committed in a prior drain pass is evicted silently (neither counted in
  ``committed`` nor re-queued), preventing the infinite replay loop where duplicates grow
  ``buffer_depth`` without bound on every subsequent ``drain_buffer()`` call;
  ``test_drain_buffer_evicts_duplicate_receipt_on_integrity_error``
  (``TestEdgePipelineDrainBuffer``) asserts ``committed == []`` and
  ``pipeline_b.buffer_depth == 0`` after a patched ``append_receipt`` raises
  ``sqlite3.IntegrityError``.  ``TestEdgePipelineDrainBuffer`` grows from 9 to 10 cases.
* [x] `OffGridBuffer._disk_writer` — close-phase evacuation wrapped in ``try/finally``:
  the ``if worker_failed:`` evacuation loop's ``with self._count_lock:`` block is now the
  body of a ``try`` clause; the outer ``finally`` acquires ``_count_lock`` independently,
  drains any sentinel injected by a racing ``close()`` (checks ``self._closed`` and calls
  ``get_nowait()`` / ``task_done()`` until ``_queue.Empty``), and unconditionally sets
  ``self._worker_running = False``; previously the flag was cleared only as the last
  statement inside the evacuation loop — an exception in that loop left
  ``_worker_running = True``, causing a subsequent ``close()`` to inject a ``None``
  sentinel into the abandoned queue with no consumer, stalling any future
  ``queue.join()``.
* [x] `OffGridBuffer` — two-phase drain with non-destructive staging rotation:
  ``drain()`` renames the active buffer to ``{path}.staging`` via ``os.replace`` instead
  of atomically clearing it with an empty temp file; new ``commit_drain()`` method deletes
  staging only after the caller confirms all entries are committed or re-queued;
  ``_recover_staging()`` in ``__init__`` restores a leftover staging file (rename-back if
  only staging exists; temp-file merge if both exist) before the worker thread starts;
  ``drain_buffer()`` calls ``commit_drain()`` only on the success path, so any exception
  leaves staging intact for recovery.  ``test_drain_buffer_preserves_staging_file_on_mid_replay_crash``
  (``TestEdgePipelineDrainBuffer``) buffers 2 receipts, crashes the replay loop on entry 2,
  asserts ``.staging`` exists with both JSONL lines, then asserts a second ``drain_buffer()``
  commits entry 2 and deletes staging.  ``TestEdgePipelineDrainBuffer`` grows from 10 to 11 cases.
* [x] `OffGridBuffer` — exclusive instance lock prevents concurrent path collisions:
  ``_acquire_buffer_lock()`` in ``__init__`` creates ``{path}.lock`` containing the
  current PID via exclusive ``open(..., 'x')``; stale locks (dead PID via ``os.kill``)
  are stolen; live locks raise ``RuntimeError`` at construction time; lock is released by
  ``close()`` on clean shutdown; write-error tests updated to unlink the lock file before
  ``buf_dir.rmdir()``.
* [x] `OffGridBuffer._recover_staging()` — corrupt-staging quarantine: always reads the
  staging file first (even in the promote-only branch) so that ``OSError`` or
  ``UnicodeDecodeError`` is detected at boot time before the worker thread starts; on any
  read or operation failure the staging file is renamed to ``{path}.staging.corrupt`` and
  ``SovereignStorageError`` is raised immediately, preventing a corrupt staging file from
  being silently skipped and its entries permanently lost; ``SovereignStorageError``
  imported into ``buffer.py`` from ``sovereign_ledger`` (no new workspace dependency —
  the package already depends on ``sovereign_ledger`` via ``pipeline.py``).
  ``test_recover_staging_quarantines_corrupt_file`` (``TestOffGridBuffer``) writes invalid
  UTF-8 bytes to the staging path and asserts ``SovereignStorageError`` from
  ``OffGridBuffer.__init__`` and the presence of the ``.staging.corrupt`` quarantine file.
  ``TestOffGridBuffer`` grows from 10 to 11 cases.
* [x] `EdgePipeline.drain_buffer()` docstring — crash-recovery deduplication explicitly
  documented: a new paragraph specifies that ``sqlite3.IntegrityError`` eviction (the
  existing ``except sqlite3.IntegrityError: pass`` clause) is the crash-recovery
  deduplication mechanism; after a crash-restart cycle where ``_recover_staging()`` merges
  a staging file back into the active buffer, re-submitted entries already in the ledger
  raise ``IntegrityError`` and are silently evicted — not re-queued — guaranteeing each
  receipt is persisted exactly once across a crash-restart boundary.  No code change.
* [x] `test_drain_buffer_deduplicates_on_post_crash_restart` (``TestEdgePipelineDrainBuffer``):
  three-phase integration test: Phase 1 buffers 2 receipts via a closed ledger; Phase 2
  crashes ``drain_buffer()`` after entry 1 is committed (entry 2 re-queued, staging intact);
  Phase 3 constructs a new pipeline triggering ``_recover_staging()`` merge → 3 active lines;
  ``drain_buffer()`` deduplicates via ``IntegrityError`` → exactly 1 new commit.
  ``TestEdgePipelineDrainBuffer`` grows from 11 to 12 cases.
* [x] `OffGridBuffer.__init__()` — lock file cleanup on staging recovery failure:
  ``_recover_staging()`` is now called inside ``try/except BaseException:`` whose handler
  unlinks the ``.lock`` file before re-raising; previously a ``SovereignStorageError`` from
  the staging quarantine path left the lock on disk, causing every subsequent construction
  attempt on the same path to raise ``RuntimeError("already held by process …")`` for the
  lifetime of the process.  ``test_init_cleans_up_lock_on_staging_recovery_failure``
  (``TestOffGridBuffer``) triggers the failure, asserts the lock is gone, then confirms a
  second construction on the same path succeeds and returns ``size == 0``.
  ``TestOffGridBuffer`` grows from 11 to 12 cases.
* [x] `OffGridBuffer._disk_writer` — evacuation ``finally`` block ``_pending`` decrement for
  racing-close sentinel: the ``with self._count_lock: if self._closed:`` drain loop consumed
  sentinels placed by a concurrent ``close()`` via ``get_nowait()`` / ``task_done()`` but
  omitted ``self._pending -= 1``, leaving the counter inflated after the thread exited and
  stalling any subsequent ``flush()`` → ``queue.join()`` call indefinitely.  ``_pending -= 1``
  added before ``task_done()`` in the sentinel drain loop, matching the decrement semantics
  of the main evacuation loop.
* [x] `EdgePipeline.process()` — ``sqlite3.IntegrityError`` duplicate eviction: a new
  ``except sqlite3.IntegrityError:`` clause before ``except (SovereignStorageError,
  sqlite3.Error):`` extracts ``payload_hash`` from the receipt dict and leaves
  ``buffered = False``; previously ``IntegrityError`` fell through to the broader guard and
  routed the duplicate to the off-grid buffer with ``buffered=True``, diverging from the
  ``drain_buffer()`` silent-eviction contract.  ``process()`` docstring updated.
* [x] ``test_process_evicts_duplicate_submission_without_buffering``
  (``TestEdgePipelineProcess``): patches ``mem_ledger.append_receipt`` with
  ``sqlite3.IntegrityError``; asserts ``result.buffered is False`` and
  ``buffer_depth == 0``.  ``TestEdgePipelineProcess`` grows from 18 to 19 cases.
* [x] ``test_crash_evacuation_racing_close_leaves_pending_at_zero``
  (``TestOffGridBufferWriteErrors``): non-OSError worker crash + 8 concurrent ``close()``
  threads; asserts all threads join within 5 s (deadlock sentinel) and
  ``buf._pending == 0`` after completion, directly validating the sentinel-decrement fix.
  ``TestOffGridBufferWriteErrors`` grows from 11 to 12 cases.
* [x] `EdgePipeline.__init__()` — secret validation before buffer construction:
  ``_sensor_secret`` computed and ``SovereignConfigurationError`` guard fired at the very
  top of ``__init__`` before ``OffGridBuffer(buffer_path)`` is invoked; previously the
  buffer was constructed first, leaving an orphaned ``.lock`` file on validation failure.
  ``test_configuration_error_does_not_create_buffer_lock_file``
  (``TestEdgePipelineSecureInit``): asserts lock absent after ``SovereignConfigurationError``.
  ``TestEdgePipelineSecureInit`` grows from 4 to 5 cases.
* [x] `EdgePipeline.drain_buffer()` — ``flush()`` before ``commit_drain()``:
  after the re-queue pass, ``self._buffer.flush()`` blocks until all re-queued entries
  are fsync'd to the active JSONL file before ``commit_drain()`` deletes the staging
  backup; closes the window where a process exit between successful ``push()`` calls
  and ``commit_drain()`` left re-queued entries only in the in-memory queue.
* [x] `SensorFrame.text_content()` — compact separator; HMAC canonical unified:
  ``text_content()`` now uses ``separators=(",", ":")`` (compact, no spaces), matching
  ``SovereignEnvelope.seal()``'s preimage canonical form; ``pipeline.process()`` replaces
  the inline ``json.dumps(dict(frame.d), ...)`` with ``frame.text_content()`` so the HMAC
  preimage and sieve input share a single normalized, compact canonical; unused
  ``import json`` removed from ``pipeline.py``.
* [x] `OffGridBuffer._disk_writer` — write errors persisted to disk-backed quarantine log:
  on ``OSError`` in the disk writer, the failed JSONL entry is appended to
  ``{path}.quarantine`` via ``open(..., "a")`` + ``flush()`` + ``os.fsync()`` before being
  recorded in the in-memory ``_write_errors`` list; ``_load_quarantine()`` is called during
  ``__init__`` (after ``_recover_staging()``, before the worker thread starts) and repopulates
  ``_write_errors`` from a prior run's quarantine file so the next ``drain()`` surfaces all
  failed entries across crash-restart boundaries; ``drain()`` unlinks the quarantine file on
  both the early-return path and the normal completion path after ``_write_errors`` is cleared;
  quarantine write failures are caught with ``except OSError: pass`` so a missing or
  unwritable quarantine directory degrades silently without masking the primary write error.
* [x] `OffGridBuffer._acquire_buffer_lock()` — atomic OS-level lock creation via `os.open`:
  ``open(self._lock_path, "x", encoding="utf-8")`` replaced with
  ``os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)``; the single
  atomic kernel syscall prevents two concurrent ``OffGridBuffer`` constructions on the same
  path from both receiving a successful lock creation (the previous ``open(..., "x")`` could
  race on some platforms between the existence check and file creation); the fd is closed
  immediately after writing the PID via ``os.write`` + ``os.close`` in ``try/finally`` so
  that external callers on Windows (which disallows ``unlink`` of open files without
  ``FILE_SHARE_DELETE``) can unlink the lock file without contention during test teardown.
* [x] `EdgePipeline.__init__()` — buffer closed on post-buffer construction failure:
  all initialization steps after ``OffGridBuffer(buffer_path)`` (key path resolution,
  ``mkdir``, ``chmod``, ``SovereignKeyManager`` construction) are wrapped in
  ``try/except BaseException``; the handler calls ``self._buffer.close()`` — with
  ``except Exception: pass`` suppressing any close error so it cannot mask the constructor
  exception — then re-raises; previously a failure in any of those steps left the daemon
  thread running and the ``.lock`` file on disk, permanently blocking every subsequent
  construction attempt on the same path for the lifetime of the process.
  ``test_init_failure_after_buffer_creation_closes_worker_thread``
  (``TestEdgePipelineSecureInit``) patches ``SovereignKeyManager`` to raise and asserts
  the ``.lock`` file is absent, confirming the thread was joined and lock released.
  ``TestEdgePipelineSecureInit`` grows from 5 to 6 cases.
* [x] `OffGridBuffer._acquire_buffer_lock()` — `PermissionError` and unexpected `OSError` raise `SovereignStorageError`:
  three permission-boundary guards added: (1) ``except OSError`` after ``except FileExistsError``
  in the initial ``os.open`` block converts any non-file-exists OS rejection to
  ``SovereignStorageError``; (2) ``except PermissionError`` before ``except (OSError, ValueError)``
  in the held-PID read block prevents a permission-denied read from being misclassified as a
  stale lock; (3) ``except PermissionError`` before ``except OSError`` in the
  ``os.kill(held_pid, 0)`` block prevents a permission-denied kill — meaning the owner IS alive
  under a different user — from triggering a lock steal.  All three cases raise
  ``SovereignStorageError("Lock file acquisition failed due to permission or system boundaries")``
  with the original OS exception chained as ``__cause__``.
* [x] `OffGridBuffer.drain()` — write-error clear bounded to snapshot count:
  ``_error_snapshot_count = len(self._write_errors)`` is recorded atomically with the
  ``pending_error_entries`` snapshot under ``_count_lock``; the former
  ``self._write_errors.clear()`` on both the file-absent early-return path and the normal
  ``os.replace`` completion path is replaced with
  ``del self._write_errors[:_error_snapshot_count]``, so any entry appended to
  ``_write_errors`` after the snapshot boundary survives into the next drain pass;
  if ``os.replace`` raises ``OSError``, the code exits before any ``del`` executes,
  guaranteeing no write-error entry is cleared or orphaned by a failed file swap.
* [x] `OffGridBuffer.close()` — lock file unlinked unconditionally in `finally` block:
  ``self._lock_path.unlink()`` moved from after the write-error check into a ``finally``
  block that wraps the entire check-and-raise sequence; previously a non-empty
  ``_write_errors`` caused ``RuntimeError`` to propagate before ``unlink()`` was reached,
  leaving the ``.lock`` file on disk after the worker thread had exited and permanently
  blocking every subsequent construction attempt on the same path; the ``finally`` guarantee
  ensures the lock is always released regardless of whether ``close()`` returns normally or
  raises, so a faulted shutdown never creates a permanent construction barrier.
* [x] `packages/sovereign-edge/README.md` and root `README.md` — `EdgePipeline` examples aligned with secure-by-default contract:
  the Quick Start snippet in ``packages/sovereign-edge/README.md`` previously omitted
  ``sensor_secret``, which raises ``SovereignConfigurationError`` under the secure-by-default
  policy; ``sensor_secret=b"<shared-hmac-secret>"`` added and a preceding comment explains
  ``allow_unauthenticated=True`` as the explicit opt-out; the root ``README.md``
  ``sensor_secret`` inline comment ``"omit to disable inbound verification"`` replaced with
  ``"required; pass allow_unauthenticated=True to opt out"`` to accurately reflect the
  constructor contract.
* [x] 97-case desktop validation test suite across nine classes (`TestSensorFrame`: 16;
  `TestOffGridBuffer`: 12; `TestEdgePipelineProcess`: 19; `TestEdgePipelineBuffering`: 8;
  `TestEdgePipelineDrainBuffer`: 12; `TestOffGridBufferAsync`: 8;
  `TestEdgePipelineSieveFault`: 4; `TestOffGridBufferWriteErrors`: 12;
  `TestEdgePipelineSecureInit`: 6) covering all
  fortification scenarios: non-blocking `push()` with immediate `size` reporting,
  chronological `drain()` sort by sequence, non-integer sequence value tolerance,
  `_committed` counter accuracy after drain, sieve fault fallback with raw text and
  `sieve_fault=True` metadata, disk write error tracking, full `drain()` recovery, `close()`
  raising on un-journaled entries, `EdgePipeline.close()` propagating RuntimeError, 20-thread
  push-vs-close stress test, HMAC-SHA256 inbound signature rejection, algorithm-gate, HMAC
  hex normalisation, broadened sieve-fault exception scope to `except Exception:`, idempotent
  close triple-call, protocol version gate, dead-letter eviction cap, non-OSError worker
  failure, strict field type validation, lock-free evacuation deadlock regression,
  drain_buffer requeue-loop survivability, concurrent close counter-drift, dual-failure
  exception chaining, drain read-failure OSError propagation, frozen dataclass mutation
  guard, exhaustive replay-crash re-queue, deep MappingProxyType immutability on
  ``SensorFrame.d``, ``SovereignDoubleFaultError`` double-fault receipt recovery,
  drain_buffer OSError halt-and-alert, 16-thread TOCTOU push-vs-crash stress test with
  zero ``_pending`` drift, OS-exit-gap ``_worker_running`` sentinel guard,
  secure-by-default init with ``SovereignConfigurationError``, stale
  ``drain_read_failed`` flag reset after filesystem recovery, atomic rotation
  ``OSError`` propagation, drain_buffer cascading double-fault with
  ``uncommitted_receipts`` preservation, float literal preservation in
  ``text_content()`` for byte-identical HMAC preimage fidelity, duplicate
  ledger-entry eviction on ``sqlite3.IntegrityError``,
  close-phase evacuation ``try/finally`` sentinel guard, two-phase non-destructive
  drain with ``.staging`` crash recovery, exclusive instance-lock collision guard,
  corrupt-staging quarantine with ``SovereignStorageError`` boot-time alert,
  crash-restart ``IntegrityError`` deduplication end-to-end integration,
  lock-file cleanup on staging recovery failure enabling immediate retry,
  evacuation-finally ``_pending`` decrement for racing-close sentinel,
  direct duplicate eviction in ``process()`` matching drain-buffer eviction contract,
  no lock-file orphan on ``SovereignConfigurationError`` constructor failure,
  re-queue durability flush before staging commit, unified compact HMAC
  canonical via ``text_content()`` with separator alignment,
  disk-backed quarantine file for crash-durable write-error recovery across
  restart boundaries, atomic ``os.open(O_CREAT | O_EXCL)`` lock creation
  eliminating TOCTOU races in concurrent ``OffGridBuffer`` construction,
  buffer worker thread termination on post-buffer constructor failure with lock
  release verified via ``SovereignKeyManager`` injection, ``SovereignStorageError``
  on ``PermissionError`` lock-file access preventing silent lock theft,
  snapshot-bounded ``_write_errors`` drain clearing preserving post-snapshot entries,
  and unconditional ``.lock`` unlink in ``close()`` ``finally`` block guaranteeing
  lock release even when ``RuntimeError`` propagates from the write-error check,
  ledger exception handler in ``process()`` broadened to ``except Exception`` so
  custom adapter faults that inherit from neither ``SovereignStorageError`` nor
  ``sqlite3.Error`` route to the off-grid buffer instead of propagating unhandled,
  quarantine double-fault detector: ``sys.stderr`` emission + ``worker_failed``
  flag on secondary quarantine write failure so no entry is silently lost even
  when both the primary buffer and the quarantine disk path are unwritable,
  and selective open() mock in ``test_close_propagates_buffer_write_error_as_runtime_error``
  isolating primary-buffer failures from quarantine writes.

- Stale-sentinel elimination: outer ``try/finally`` belt-and-suspenders sweep in
  ``_disk_writer`` drains any sentinel stranded in the queue after the main loop exits;
  re-indented ``while True:`` from 10-space to canonical 12-space nesting with
  consistent 4-space body indentation throughout.

- Atomic sentinel placement in ``close()``: ``_write_queue.put(None)`` moved inside
  ``_count_lock`` acquisition so the sentinel is always in the queue when ``_closed =
  True`` becomes visible to the evacuation ``finally`` block, closing the race window
  where the evacuation could drain an empty queue before ``put(None)`` fires.

- Permanent data-format fault eviction in ``drain_buffer()``: inner
  ``except (ValueError, TypeError) as permanent_err:`` clause in the per-entry replay
  loop emits a ``SOVEREIGN-EDGE CRITICAL`` line to ``sys.stderr`` and skips re-queuing
  for entries that will never succeed (bad schema, type mismatch), preventing infinite
  replay loops while still allowing operational ``RuntimeError`` to abort the loop and
  preserve the staging file; ``import sys`` added to ``pipeline.py``.

- Three new/updated tests: ``test_drain_buffer_evicts_permanently_on_non_storage_exception``
  (renamed from ``test_drain_buffer_requeues_all_items_on_unexpected_exception``, verifies
  ``ValueError`` → permanent eviction, no re-queue, ``drain_buffer()`` returns normally),
  ``test_drain_buffer_permanent_fault_emits_critical_log`` (guards stderr format: CRITICAL
  prefix, exception type, exception message), and
  ``test_racing_close_sentinel_drained_by_evacuation`` (verifies close() does not hang
  when racing with an in-progress crash, ``_pending == 0`` after resolution).
  **101 passed, 0 skipped (edge); 390 passed, 1 skipped (workspace).**

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
