# Sovereign Systems SDK

**High-Integrity Cryptographic Provenance and Inbound Protection Boundaries for Agentic Workflows.**

Sovereign Systems is a local-first AI WAF and compliance gate. It intercepts every inbound payload before it reaches a model or agentic loop, strips high-entropy boilerplate, and seals the result with an Ed25519-signed `ForensicReceipt` that gives enterprise auditors mathematical proof of un-tampered boundary transformation — all running on local silicon with no external service dependency.

Every boundary crossing produces a non-repudiable chain of custody: the receipt binds the sieved payload hash and the full transformation accounting inside a single cryptographic envelope. No post-hoc mutation of the output or its metrics can go undetected.

To learn more about the philosophy and reasoning behind this project, see [PHILOSOPHY.md](PHILOSOPHY.md).

---

## Why an AI WAF?

Modern agentic pipelines ingest user intent through prompt text. That text is routinely bloated with conversational filler — greetings, hedging adverbs, redundant preambles — that inflates token budgets and introduces non-deterministic reasoning noise without contributing semantic content. At the same time, enterprises operating LLM workloads need the same compliance guarantees they expect from a network WAF: proof that payloads were inspected, proof that the inspection was faithful, and an append-only audit log that is tamper-evident after the fact.

Sovereign Systems provides both:

- **Inbound boundary enforcement** — every payload is sieved before a model or tool sees it.
- **Cryptographic chain of custody** — every sieved payload is sealed with a node-local Ed25519 private key into a `ForensicReceipt` that persists forever.
- **Non-repudiation** — the receipt covers the output hash and the transformation accounting under the same signature, so neither the result nor the metrics can be altered without breaking verification.
- **Local-only execution** — no telemetry leaves the host; key material never leaves the node.

---

## Workspace Topography

This repository is managed as an integrated `uv` workspace separating the cryptographic data tier from the execution runtime:

```text
.
├── .github/
│   └── workflows/
│       ├── publish.yml                   # PyPI release pipeline
│       └── test.yml                      # CI test matrix
│
├── examples/
│   └── fastapi_gateway/
│       ├── app.py                        # Example FastAPI server with SovereignMiddleware
│       └── client.py                     # Example HTTP client
│
├── packages/
│   ├── sovereign-core/                   # Pure data tier (zero high-compute dependencies)
│   │   ├── src/sovereign_core/
│   │   │   ├── cli.py                    # CLI entry points (sovereign-verify)
│   │   │   ├── crypto.py                 # Ed25519 key management & ForensicReceipt minting
│   │   │   ├── gateway.py                # Prose Tax sieve & SovereignGateway high-level API
│   │   │   └── py.typed
│   │   └── tests/
│   │       ├── test_crypto.py
│   │       ├── test_gateway.py
│   │       └── test_verification_protocol.py
│   │
│   ├── sovereign-sieve/                  # Zero-dependency standalone Prose Tax utility
│   │   ├── src/sovereign_sieve/
│   │   │   └── sieve.py                  # pure_sieve(), sieve_with_metrics(), SieveOutput
│   │   └── tests/
│   │       └── test_sieve.py
│   │
│   ├── sovereign-runtime/                # Compute/Execution tier (tool & model isolation)
│   │   └── src/sovereign_runtime/
│   │       ├── router.py                 # Intent-based pre-flight namespace exposure
│   │       ├── __main__.py               # sovereign-node entry point
│   │       └── py.typed
│   │
│   └── sovereign-fastapi/                # FastAPI/Starlette ASGI middleware adapter
│       ├── src/sovereign_fastapi/
│       │   ├── middleware.py             # SovereignMiddleware — sieve-and-sign request interceptor
│       │   └── py.typed
│       └── tests/
│           └── test_middleware.py
│
├── example.env                           # Environment variable reference
├── main.py                               # Workspace-level development entry point
├── pyproject.toml                        # Monorepo configuration & workspace links
└── uv.lock                               # Deterministic dependency lockfile
```

---

## `sovereign-sieve` — Standalone Token Optimization

For scripts, batch pipelines, and offline data-processing workers that do not require cryptographic signing, `sovereign-sieve` delivers the Prose Tax regex engine as a zero-dependency synchronous utility:

```bash
pip install sovereign-sieve
```

```python
from sovereign_sieve import pure_sieve, sieve_with_metrics

# Drop-in string cleaner
clean = pure_sieve("Hello! Please just help me analyze this dataset.")
# → "help me analyze this dataset."

# Cleaner + immediate FinOps telemetry
result = sieve_with_metrics("Hi! I hope this helps. Please just run the pipeline.")
print(result.text)                   # → "run the pipeline."
print(result.raw_token_count)        # estimated tokens before sieve
print(result.optimized_token_count)  # estimated tokens after sieve
print(result.tax_savings_percentage) # e.g. 66.6667 (%)

# Offline batch pipeline
records = load_jsonl("prompts.jsonl")
cleaned = [pure_sieve(r["text"]) for r in records]
```

`pure_sieve()` is pure and synchronous — no I/O, no shared state, no web framework or ML library imports. See [`packages/sovereign-sieve/README.md`](packages/sovereign-sieve/README.md) for full API documentation.

---

## The ForensicReceipt: Sealed Transformation Accounting

Every boundary crossing produces a `ForensicReceipt`. Understanding its structure explains exactly what an enterprise auditor can prove from the output alone.

### What is sealed

The Ed25519 signature inside every receipt covers a single canonical manifest:

```json
{
  "metadata": { ... },
  "payload_hash": "<sha256-of-sieved-content>",
  "timestamp": "<utc-iso8601>"
}
```

`payload_hash` is the SHA-256 digest of the exact sieved string delivered to the model or tool. When the Prose Tax sieve is active, `metadata` always contains a `prose_tax_summary` sub-object:

```json
"prose_tax_summary": {
  "raw_token_count": 12,
  "optimized_token_count": 4,
  "tokens_eliminated": 8,
  "tax_savings_percentage": 66.6667,
  "total_tokens_saved": 8
}
```

Because `metadata` is bound inside the signed manifest, these token counts are just as tamper-evident as `payload_hash` itself. An auditor who holds only the node's public key can independently verify:

1. **Output integrity** — the sieved content hasn't been altered after signing (`payload_hash`).
2. **Transformation accounting** — the before/after token delta recorded at signing time hasn't been fabricated (`prose_tax_summary` is sealed under the same signature).
3. **Identity provenance** — the receipt was minted by the expected node, not a rogue keypair (`public_key` key-pin assertion).

Together these three checks give mathematical proof that the boundary transformation was faithful and un-tampered — equivalent to a signed audit log with built-in integrity verification.

### Verification workflow

```python
import asyncio
import json
from sovereign_core.gateway import SovereignGateway
from sovereign_core.crypto import SovereignKeyManager

async def main():
    gateway = SovereignGateway(signing_key=".keys/sovereign_identity.pem")
    result = await gateway.sieve_and_sign("Hi! Please just help me now.")

    # result.content == "help me now"
    # result.receipt["metadata"]["prose_tax_summary"]["tokens_eliminated"] == 4

    # Verify later — requires only the public key and the original sieved payload
    is_valid = SovereignKeyManager.verify_receipt(
        result.receipt,
        {"content": result.content},
        expected_public_key=gateway.export_public_key(),
    )
    assert is_valid  # fails if any field was mutated after signing

asyncio.run(main())
```

---

## Primary Developer Interface: `SovereignGateway`

`SovereignGateway` is the single entry point for application code. It wraps the full sieve-and-sign pipeline behind a clean four-method API.

### One-shot macro (recommended)

`sieve_and_sign()` strips Prose Tax boilerplate, fuses the transformation telemetry into the receipt metadata, and seals everything in a single awaitable call:

```python
import asyncio
from sovereign_core.gateway import SovereignGateway

async def main():
    gateway = SovereignGateway(signing_key=".keys/sovereign_identity.pem")
    result = await gateway.sieve_and_sign("Hi! Please just help me now.")

    # result  — SovereignBoundaryResponse (Pydantic model, fully typed)
    # result.content  — purified string, Prose Tax stripped ("help me now")
    # result.receipt  — ForensicReceipt with prose_tax_summary sealed inside

    print(result.content)
    print(result.receipt["payload_hash"])

asyncio.run(main())
```

Inside a FastAPI route the gateway instance lives on the application object; the route itself is already async:

```python
from sovereign_core.gateway import SovereignGateway

gateway = SovereignGateway(signing_key=".keys/sovereign_identity.pem")

@app.post("/api/v1/ingest")
async def handle_agent_input(raw_payload: dict):
    result = await gateway.sieve_and_sign(raw_payload["text"])

    await reasoning_ledger.append(
        payload=result.content,
        receipt=result.receipt,
    )
    return {
        "status": "sovereign_verified",
        "receipt_id": result.receipt["payload_hash"],
    }
```

### Granular two-step workflow

When the clean context is needed before signing (e.g. for intermediate validation or logging):

```python
import asyncio
from sovereign_core.gateway import SovereignGateway

async def main():
    gateway = SovereignGateway(signing_key=".keys/sovereign_identity.pem")

    # 1. Strip Prose Tax — remove boilerplate, normalize whitespace
    clean_context = await gateway.sieve("Hi! Please just help me now.")

    # 2. Cryptographically seal — transformation telemetry fused into metadata
    receipt = gateway.sign(clean_context)

    print(clean_context)            # "help me now"
    print(receipt["payload_hash"])  # SHA-256 of {"content": "help me now"}

asyncio.run(main())
```

### Independent receipt verification

Receipts produced by either workflow can be verified at any time using only the public key:

```python
from sovereign_core.crypto import SovereignKeyManager

is_valid = SovereignKeyManager.verify_receipt(
    receipt,
    {"content": clean_context},
    expected_public_key=gateway.export_public_key(),
)
```

### Public key distribution & key rotation

#### Exporting a signed public key bundle

`export_public_key_bundle()` mints a self-signed `PublicKeyBundle` that proves private-key ownership at a specific point in time. The `issued_at` timestamp is sealed inside the Ed25519 attestation signature — it cannot be back-dated after the fact.

```python
from sovereign_core.gateway import SovereignGateway

gateway = SovereignGateway(signing_key=".keys/sovereign_identity.pem")
bundle = gateway.export_public_key_bundle(node_id="node-alpha")

# bundle.public_key   — base64 Ed25519 public key
# bundle.attestation  — self-signed ForensicReceipt proving key ownership
# bundle.issued_at    — UTC ISO 8601 timestamp (sealed inside the signature)
# bundle.node_id      — human-readable node label

gateway.save_public_key_bundle(".keys/bundle.json", node_id="node-alpha")
```

Downstream auditors and consumers can verify the bundle's attestation receipt using only `bundle.public_key` and `SovereignKeyManager.verify_receipt()` — no private key material required.

#### Key rotation with auditable succession

`rotate_keypair()` generates a fresh Ed25519 keypair, signs a canonical rotation payload with the **outgoing** private key, and atomically promotes both the new `.pem` and `.pub` files on disk. The returned `SuccessionReceipt` provides a cryptographically auditable handoff chain.

```python
from sovereign_core.crypto import SovereignKeyManager

manager = SovereignKeyManager(key_dir=".keys")
manager.load_or_generate_keypair()

# Capture the pre-rotation key from a trusted out-of-band source before rotating
trusted_previous_key = manager.public_key

receipt = manager.rotate_keypair()
# receipt["previous_public_key"]  — base64 key active before rotation
# receipt["new_public_key"]       — base64 key active after rotation
# receipt["rotation_timestamp"]   — UTC ISO 8601 timestamp
# receipt["succession_signature"] — Ed25519 signature by the outgoing key
```

Verify the succession event using the out-of-band trusted copy of the previous public key. The `trusted_previous_public_key` anchor must come from a source independent of the receipt being verified — passing a key extracted from the receipt itself would be self-referential and cryptographically meaningless:

```python
is_valid = SovereignKeyManager.verify_succession(
    receipt,
    trusted_previous_public_key=trusted_previous_key,
)
assert is_valid  # False if any rotation field was tampered
```

---

## ASGI Middleware for FastAPI / Starlette

`SovereignMiddleware` applies the sieve-and-sign boundary to every inbound JSON request transparently, without changes to route handlers:

```python
from fastapi import FastAPI
from sovereign_fastapi.middleware import SovereignMiddleware

app = FastAPI()
app.add_middleware(
    SovereignMiddleware,
    signing_key=".keys/sovereign_identity.pem",
    payload_field="text",   # JSON key to sieve; omit to sieve the whole body
    strict_mode=False,      # True → return HTTP 422 on any interception error
)
```

The middleware:
1. Extracts the target field from the JSON body.
2. Calls `sieve_and_sign()` on the gateway.
3. Overwrites `request._body` so every downstream route handler sees the sieved payload.
4. Caches the sealed receipt at `request.state.sovereign_receipt`.
5. Injects `X-Sovereign-Receipt-Signature` and `X-Sovereign-Tokens-Saved` on the outbound response.

---

## Prose Tax Optimization

> **This feature is optional.** The cryptographic boundary and ForensicReceipt are produced whether or not any text is eliminated. Prose Tax optimization runs inside the audit envelope — every token count and savings metric is sealed alongside the output hash.

The sieve removes conversational boilerplate that inflates token budgets without contributing semantic content:

| Category | Examples stripped |
|---|---|
| Greeting tokens | `hi`, `hello`, `hey`, `greetings` |
| Hedging adverbs | `just`, `simply`, `actually`, `basically`, `probably` |
| Affirmation filler | `of course`, `certainly`, `absolutely`, `sure` |
| Preamble phrases | `I hope this`, `I hope that`, `I hope you` |
| Politeness tokens | `please`, `kindly` |

All patterns carry negative lookahead guards (e.g. `(?![-\w])`) so technical compound words (`hi-fi`, `just-in-time`, `certainly-not`) pass through unmarred.

---

## Local Development

### Bootstrap

```bash
uv sync
```

### Run tests

```bash
uv run pytest
```

### Run the sovereign-node runtime

```bash
uv run sovereign-node
```

---

## Running the Workspace Examples

### FastAPI Gateway Example

**1. Set your node secret** (required for Ed25519 key generation):

```bash
export SOVEREIGN_NODE_SECRET=your-local-secret   # Linux / macOS
$env:SOVEREIGN_NODE_SECRET = "your-local-secret"  # Windows PowerShell
```

**2. Start the example server:**

```bash
uv run uvicorn examples.fastapi_gateway.app:app --reload
```

The server starts on `http://127.0.0.1:8000`. On first boot it generates an Ed25519 keypair at `.keys/example_identity.pem`.

**3. In a second terminal, run the example client:**

```bash
uv run python examples/fastapi_gateway/client.py
```

### Verifying a ForensicReceipt (CLI)

Export a receipt and the gateway's public key:

```python
import asyncio
import json
from sovereign_core.gateway import SovereignGateway

async def main():
    gateway = SovereignGateway()
    result = await gateway.sieve_and_sign("example payload")

    with open("receipt.json", "w") as f:
        json.dump(result.receipt, f, indent=2)

    print(gateway.export_public_key())

asyncio.run(main())
```

Verify the receipt:

```bash
uv run sovereign-verify \
    --receipt receipt.json \
    --public-key <base64-encoded-public-key>
```

On success:

```
Verified  ✓  payload_hash: 4fec03e7...
```

On tampered receipt:

```
Tampered  ✗  Receipt failed cryptographic verification.
  payload_hash : 4fec03e7...
  timestamp    : 2026-05-22T...
```

---

## Verification & Deep-Dive Diagnostics

### Local Environment Configuration

`SOVEREIGN_NODE_SECRET` can be specified in a `.env` file at the repository root. The node entrypoint loads it automatically via `python-dotenv`:

```bash
echo 'SOVEREIGN_NODE_SECRET=your-local-secret' > .env
```

### Standalone Tool Analysis Mode

```bash
uv run sovereign-node --tool analyze
```

### Expected Console Output

```
====================================================
🟢 Sovereign Node initialization sequence successful.
====================================================
🔄 Dispatching single tool execution: 'analyze'...
⚠️ Standalone mode detected: Context empty. Hydrating baseline diagnostic state...

🔒 Authenticated Forensic Receipt Proof:
{
  "timestamp": "2026-05-22T15:00:00.000000+00:00",
  "payload_hash": "4fec03e7083cca73cfb1152ae1d941b5a5a581fc725a43b3ee7df1d9ce697954",
  "public_key": "<base64-encoded Ed25519 public key>",
  "signature": "<base64-encoded Ed25519 signature>",
  "metadata": {
    "runtime": "async-sovereign-node",
    "py_ver": "3.12.x",
    "execution_success": true
  }
}
```

**Line-by-line interpretation:**

| Output line | What it proves |
|---|---|
| `🟢 Sovereign Node initialization sequence successful.` | Ed25519 keypair loaded or generated; `SOVEREIGN_NODE_SECRET` resolved; router and session context initialised. |
| `⚠️ Standalone mode detected …` | The `analyze` tool detected no upstream context and self-hydrated a baseline telemetry stream — expected behaviour in single-tool invocations. |
| `"payload_hash": "4fec03e7…"` | SHA-256 digest of the deterministically serialised execution payload. |
| `"public_key": "<base64>"` | Base64-encoded raw Ed25519 public key; verified by `_audit_receipt` before process exit. |
| `"signature": "<base64>"` | Ed25519 signature over `{"metadata": …, "payload_hash": …, "timestamp": …}`. Any mutation of these fields after issuance causes `verify_receipt` to return `False`. |
| `"execution_success": true` | The tool completed without raising an exception; the receipt is audit-clean. |
