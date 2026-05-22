# Sovereign Systems Specification — Strategic Roadmap

This document tracks the long-range architectural trajectory of the Sovereign Systems SDK.
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

### 5.1 FastAPI Middleware (`sovereign-fastapi`)

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
- Request-body interception via transparent `_CachedRequest.wrapped_receive` body overwrite before handler dispatch
- `X-Sovereign-Receipt-Signature` response header carrying the base64-encoded Ed25519 signature
- `X-Sovereign-Tokens-Saved` response header carrying the cumulative FinOps savings counter
- Automatic binding of the strict `SovereignBoundaryResponse` to `request.state.sovereign_receipt` for in-route auditability and IDE type preservation
- Optional strict mode: returns HTTP 422 on any interception error (missing field, JSON parse failure)
- Configurable field selector for non-root payload extraction (e.g. `payload_field="text"`)

### Phase 5.1 — FastAPI / Starlette ASGI Middleware
- [x] Full-lifecycle ASGI body interception, local-silicon context sieving, and cryptographic signing.
- [x] Inbound proxy alignment via dynamic `Content-Length` byte-length recalculation.
- [x] Structured diagnostic observability logging for non-strict failure modes.

### Phase 5.2 — Streaming Request & Response Boundaries (Extension Path)
- [ ] Architectural specification for asynchronous generator token streams (`StreamingResponse`).
- [ ] Out-of-band verification tracing: Designing an alternative background task worker or trailing chunk aggregator to handle Server-Sent Events (SSE) where HTTP headers cannot be mutated mid-stream.

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
- `request.sovereign.content` and `request.sovereign.receipt` available in every view
- Settings-driven configuration via `SOVEREIGN_SIGNING_KEY` and `SOVEREIGN_STRICT_MODE`
- Async-compatible via Django's async middleware protocol

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
- `on_llm_start` interception with Prose Tax sieve applied to all prompt strings
- ForensicReceipt written to `run_manager.metadata["sovereign_receipt"]`
- `SovereignChain` LCEL-compatible wrapper for `pipe`-style integration
- Receipt export to a configurable append-only JSONL ledger file

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
- `SovereignNodePostprocessor` applying `sieve()` to each retrieved `TextNode`
- `SovereignQueryTransform` applying `sieve_and_sign()` to query strings
- Prose Tax savings metrics surfaced in the LlamaIndex response metadata
- Compatible with `VectorStoreIndex`, `SummaryIndex`, and custom retrievers

---

## Phase 6 — Verification Key Protocol

**Target:** A first-class public key distribution and multi-party receipt verification
mechanism, enabling downstream consumers and auditors to independently authenticate
ForensicReceipts without access to the signing node's private key material.

### 6.1 Public Key Export and Distribution

Extend `SovereignGateway.export_public_key()` with a structured `PublicKeyBundle`
export format that carries the base64-encoded key, a self-signed attestation receipt,
a UTC issuance timestamp, and an optional node identifier string.

```python
bundle = gateway.export_public_key_bundle()
# bundle.public_key   — base64 Ed25519 key
# bundle.attestation  — self-signed ForensicReceipt proving key ownership
# bundle.issued_at    — UTC ISO 8601 timestamp
# bundle.node_id      — optional human-readable node label
```

Key deliverables:
- `PublicKeyBundle` Pydantic model with Pydantic v2 JSON serialization
- `SovereignGateway.export_public_key_bundle()` method
- `SovereignGateway.save_public_key_bundle(path)` for writing to disk as JSON

### 6.2 Stateless Receipt Verification CLI

A `sovereign-verify` CLI entrypoint (added to `packages/sovereign-runtime`) that
accepts a receipt JSON file and a public key bundle file and exits `0` on verified,
`1` on tampered, without requiring access to any private key or node secret.

```bash
sovereign-verify --receipt receipt.json --key node.pub.json
# Verified  ✓  payload_hash: 4fec03e7...
```

### 6.3 Key Rotation and Succession

A structured key rotation flow in `SovereignKeyManager` that generates a new keypair,
mints a signed succession receipt (signed by the old key, containing the new public key),
and atomically replaces the on-disk PEM.  The succession receipt provides a
cryptographically auditable chain of identity handoff.

Key deliverables:
- `SovereignKeyManager.rotate_keypair() -> SuccessionReceipt`
- `SuccessionReceipt` TypedDict containing `previous_public_key`, `new_public_key`,
  `rotation_timestamp`, and `succession_signature`
- `SovereignKeyManager.verify_succession(receipt)` for auditing rotation events

### 6.4 Multi-Node Federated Verification

A `FederatedVerifier` class that holds a registry of trusted `PublicKeyBundle` objects
and can verify a `ForensicReceipt` against any registered node identity, enabling
cross-node audit workflows where receipts from multiple sovereign nodes must be
validated against a shared trust registry.

Key deliverables:
- `FederatedVerifier` with `register(bundle)`, `verify(receipt, payload)`, and
  `trusted_keys() -> list[str]` methods
- JSON-serializable trust registry for persistence across process restarts
- Configurable strict mode: require key-pin match against a specific registered bundle

---

## Future Extensions & Ecosystem Blueprints

### Decentralized Trust Anchoring (Hedera Consensus Service Blueprint)
While the Sovereign Systems SDK maintains a strict local-first, zero-network isolation boundary by default, enterprise compliance architectures may require non-repudiation proofs that survive local system compromises.

We propose a decoupled, asynchronous anchoring pattern using the **Hedera Consensus Service (HCS)**:
- **Zero Core Bloat:** The runtime gateway remains local-first and does not block on external consensus loops.
- **State-Stable Proof of Existence:** Downstream background workers can capture the self-contained `ForensicReceipt` objects and emit the `signature` and `payload_hash` to an HCS topic.
- **Immutable Timestamping:** This provides decentralized, consensus-driven proof of time and sequence, preventing back-dating or historical audit tampering by rogue internal administrators.

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
