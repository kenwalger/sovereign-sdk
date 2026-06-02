Here is your updated `ROADMAP.md`. The three new architectural primitives—**`sovereign-sieve`**, **`sovereign-ledger`**, and **`sovereign-vault`**—have been seamlessly woven into the document.

Rather than overloading your development schedule right now, they have been strategically placed as **Phase 7 (Token Economics Extraction)**, **Phase 8 (Write-Side Custody Ledger)**, and **Phase 9 (Context-Isolation Core)**. This transforms your document from a standalone Python SDK roadmap into the definitive master blueprint for the complete **Sovereign Systems Software Suite**.

---

```markdown
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

## Phase 7 — Standalone Token Economics Primitives (`sovereign-sieve`)

**Target:** Extract the payload-cleansing mechanics from the web framework middleware tier into a zero-dependency, ultra-lightweight standalone utility to maximize Python ecosystem adoption.

Allows scripts, background workers, and pipeline ingestors to aggressively filter structural text noise and mitigate Prose Tax outside an HTTP ASGI/WSGI context.

```python
from sovereign_sieve import pure_sieve

raw_payload = "Hello! I would be honored to assist you today. The data is: {'id': 42}"
optimized_payload = pure_sieve(raw_payload)  # Result: "{'id': 42}"

```

Key deliverables:

* **Zero-Dependency Core:** Decouple regex token heuristics, negative-lookahead phrase filters, and AST structural cleansing code from web-framework runtimes.
* **Micro-Utility Performance:** Optimize execution pathways for high-throughput batch processing loops (e.g., historical ledger ingestion pipelines).
* **Frictionless On-Ramp:** Provide immediate FinOps token-savings metrics output for offline script environments.

---

## Phase 8 — Write-Side Custody Ledger (`sovereign-ledger`)

**Target:** A dedicated, lightweight, local-first storage substrate designed specifically to enforce Write-Side Custody by indexing and safeguarding `ForensicReceipts` at the exact millisecond of ingestion.

Provides local-first systems with an append-only, tamper-evident transactional trace to shield compliance audits from post-hoc database mutation or log-injection vulnerabilities.

```python
from sovereign_ledger import SovereignAppendOnlyLedger

ledger = SovereignAppendOnlyLedger(database_path=".storage/sovereign_history.db")
ledger.commit_receipt(receipt, payload_manifest)

```

Key deliverables:

* **Append-Only SQLite Engine:** A hardened local transactional datastore engineered specifically for logging, tracing, and indexing reasoning artifacts and causal state transitions.
* **Automated Lineage Verification:** Continuous background scanning or query-time hooks that ensure stored records exactly match their signed Ed25519 public key history.
* **Anti-Attic Structuring:** Force strict indexing schemas on raw data, formally migrating local data environments away from loose "Digital Attic" vector storage anti-patterns.

---

## Phase 9 — Isolated Context Vault & Governance Server (`sovereign-vault`)

**Target:** Implement the "Sovereign Vault" architecture as an isolated local orchestration boundary, delivering a first-class Model Context Protocol (MCP) server for enterprise boundary containment.

Ensures local Small Language Models (SLMs) and autonomous cloud agents function within a strictly sandboxed, zero-variance hardware context ring, neutralizing adversarial prompt injections and data leaks.

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

```

```