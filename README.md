# Sovereign Systems SDK

**High-Integrity Cryptographic Provenance and Inbound Protection Boundaries for Agentic Workflows.**

Sovereign Systems is a local-first AI Web Application Firewall (WAF) and compliance gate. It intercepts every inbound payload before it reaches a model or agentic loop, strips high-entropy boilerplate, and seals the result with an Ed25519-signed `ForensicReceipt` that gives enterprise auditors mathematical proof of un-tampered boundary transformation — all running on local silicon with no external service dependency.

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
│   ├── sovereign-ledger/                 # Immutable, hash-chained SQLite provenance engine
│   │   ├── src/sovereign_ledger/
│   │   │   └── engine.py                 # SovereignLedger — append_receipt(), verify_ledger_integrity()
│   │   └── tests/
│   │       └── test_ledger.py
│   │
│   ├── sovereign-sensor/                 # MicroPython HAL for bare-metal Write-Side Custody
│   │   ├── src/sovereign_sensor/
│   │   │   ├── interface.py              # SovereignCryptoDriver HAL base class
│   │   │   ├── envelope.py               # SovereignEnvelope — 7-step sealing pipeline
│   │   │   ├── __init__.py               # bootstrap_sensor_node() entry point
│   │   │   └── drivers/
│   │   │       ├── software_fallback.py  # SoftwareFallbackDriver — HMAC-SHA256 (non-ESP32)
│   │   │       └── esp32_hardware.py     # ESP32HardwareDriver — skeleton (ECC impl. pending)
│   │   └── tests/
│   │       └── test_sensor.py
│   │
│   ├── sovereign-edge/                   # Middleware bridge: sensor intake → ledger storage
│   │   ├── src/sovereign_edge/
│   │   │   ├── models.py                 # SensorFrame, EdgeResult dataclasses
│   │   │   ├── buffer.py                 # OffGridBuffer — async JSONL receipt queue
│   │   │   ├── pipeline.py               # EdgePipeline — 4-stage ingestion orchestrator
│   │   │   └── __init__.py
│   │   └── tests/
│   │       └── test_edge.py
│   │
│   ├── sovereign-airlock/                # Outbound governance boundary (SAR-0004)
│   │   ├── src/sovereign_airlock/
│   │   │   ├── boundary.py               # AirlockBoundary — async 4-component orchestrator
│   │   │   ├── payload.py                # NormalizedPayload — provider-neutral inspection surface
│   │   │   ├── policy.py                 # PolicyEngine — YAML rule evaluation (raw/fields/telemetry)
│   │   │   ├── telemetry.py              # AirlockTelemetry — sieve convergence metrics
│   │   │   ├── receipt.py                # ReceiptBuilder — evidence assembly and ledger commit
│   │   │   ├── exception.py              # AirlockPolicyViolation, AirlockConfigurationError
│   │   │   └── __init__.py
│   │   └── tests/
│   │       ├── test_boundary.py
│   │       ├── test_policy.py
│   │       ├── test_receipts.py
│   │       └── test_telemetry.py
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
pip install sovereign-sdk-sieve
```

```python
from sovereign_sieve import pure_sieve, sieve_with_metrics

# Drop-in string cleaner
clean = pure_sieve("Hello! Please just help me analyze this dataset.")
# → "! help me analyze this dataset."

# Cleaner + immediate FinOps telemetry
result = sieve_with_metrics("Hi! I hope this helps. Please just run the pipeline.")
print(result.text)                   # → "! helps. run the pipeline."
print(result.raw_token_count)        # estimated tokens before sieve
print(result.optimized_token_count)  # estimated tokens after sieve
print(result.tax_savings_percentage) # e.g. 66.6667 (%)

# Offline batch pipeline
records = load_jsonl("prompts.jsonl")
cleaned = [pure_sieve(r["text"]) for r in records]
```

`pure_sieve()` is pure and synchronous — no I/O, no shared state, no web framework or ML library imports. See [`packages/sovereign-sieve/README.md`](packages/sovereign-sieve/README.md) for full API documentation.

---

## `sovereign-ledger` — Immutable Provenance Engine

For systems that must retain a tamper-evident audit history of every `ForensicReceipt`, `sovereign-ledger` provides a local-first, append-only SQLite store with a SHA-256 hash chain and engine-level Write-Side Custody enforcement:

```python
from sovereign_core.gateway import SovereignGateway
from sovereign_ledger import SovereignLedger

gateway = SovereignGateway(signing_key=".keys/sovereign_identity.pem")
ledger  = SovereignLedger(db_path=".keys/sovereign_audit.db")

# Every boundary crossing is appended and chained
result = await gateway.sieve_and_sign("Hi! Please just run the analysis.")
ledger.append_receipt(result.receipt, result.content)

# Verify the full chain at any time — O(n) sweep, no external service
assert ledger.verify_ledger_integrity()  # False if any row was tampered
```

Two mechanisms enforce immutability:

1. **Engine-level SQL triggers** — `BEFORE UPDATE` and `BEFORE DELETE` triggers stored inside the `.db` file call `RAISE(ROLLBACK, 'Write-Side Custody violation: ...')`. Any client that opens the database file — Python code, a desktop SQL browser, a raw `sqlite3.connect()` call — receives a `sqlite3.IntegrityError` and has its entire enclosing transaction rolled back at the SQLite engine layer before any mutation can land.

2. **SHA-256 hash chain** — each row's `parent_hash` is derived from the preceding row's `signature + payload_hash + parent_hash`. Modifying any field of any historical row, deleting a middle row, or injecting a fabricated row breaks the chain; `verify_ledger_integrity()` returns `False` on the first detected discrepancy.

See [`docs/sovereign-ledger.md`](docs/sovereign-ledger.md) for the full schema reference, pragma table, threat model matrix, and integration pattern.

---

## `sovereign-edge` — Sensor Ingestion Bridge

For bare-metal sensor deployments where `sovereign-sensor` seals observations on the microcontroller and `sovereign-ledger` stores the receipts on a gateway host, `sovereign-edge` provides the middleware pipeline that bridges the two layers:

```python
from sovereign_edge import EdgePipeline
from sovereign_ledger import SovereignLedger

ledger   = SovereignLedger(".keys/sovereign_audit.db")
pipeline = EdgePipeline(
    ledger=ledger,
    signing_key=".keys/edge_identity.pem",
    buffer_path=".edge_buffer.jsonl",
    sensor_secret=b"<shared-hmac-secret>",  # required; pass allow_unauthenticated=True to opt out
)
try:
    # Intercept a sealed wire frame from sovereign-sensor
    wire_bytes = envelope.seal("2026-06-19T00:00:00Z", {"sensor": "temp", "value": 21.4})
    result = pipeline.process(wire_bytes)
    # result.payload_hash  — hex receipt identifier committed to the ledger
    # result.sieved_content — Prose-Tax-minimized observation payload
    # result.buffered       — True when the ledger was unreachable

    # When the ledger recovers, flush the off-grid buffer
    committed_hashes = pipeline.drain_buffer()
finally:
    pipeline.close()
    ledger.close()
```

Three fortification properties are enforced at the architecture level:

1. **Backpressure isolation** — `push()` dispatches to a background daemon thread;
   the calling sensor-read loop is never blocked on `os.fsync` latency.  `buffer_depth`
   reflects in-flight entries immediately via the dual-counter pair `_pending` (enqueued,
   not yet fsync'd) and `_committed` (fsync'd, not yet drained), and `flush()` provides
   an explicit synchronization point via `Queue.join()`.

2. **Chronological replay** — the receipt `metadata` carries `"sequence": frame.q`.
   `drain()` sorts all buffered entries by this key before returning them, protecting
   the ledger's linear hash chain from out-of-order replay when entries arrive from
   concurrent push paths.

3. **Sieve fault isolation** — if `sieve_with_metrics()` raises, the pipeline falls
   back to the raw `text_content()` observation string, sets `tax_savings_percentage`
   to `0.0`, and stamps `"sieve_fault": True` in the receipt metadata.  The receipt
   is still committed to the ledger or off-grid buffer so no observation is silently
   discarded regardless of sieve-layer faults.

---

## `sovereign-sdk-airlock` — Outbound Governance Boundary

For applications and agentic runtimes that must govern what leaves the sovereign perimeter,
`sovereign-sdk-airlock` provides the deliberate inspection and containment boundary defined
by SAR-0004 (Airlock, Not Gateway):

```python
import asyncio
from sovereign_ledger import SovereignLedger
from sovereign_airlock import AirlockBoundary, AirlockPolicyViolation, normalize_openai

ledger = SovereignLedger(".keys/sovereign_audit.db")
boundary = AirlockBoundary(
    policy_path="policy.yaml",
    signing_key=".keys/",
    ledger=ledger,
)

try:
    result = await boundary.process(normalize_openai(request))
    # result.sieved_content         — minimised payload ready for transmission
    # result.telemetry.payload_hash — SHA-256 of the raw pre-sieve content
    # result.receipt["signature"]   — Ed25519 boundary crossing evidence
    # result.policy_warnings        — non-fatal warn-rule messages
except AirlockPolicyViolation as exc:
    # Payload blocked by a deny rule — do not transmit
    raise
```

Policy rules are declared in a local YAML file and evaluated in three scopes:
- `raw` — regex pattern matched against the flat combined content string
- `fields` — pattern matched against named structured fields (`messages.content`, `tools.description`, etc.)
- `telemetry` — numeric threshold applied to `raw_tokens`, `sieved_tokens`, or `tax_savings_percentage`

Airlock is transport-agnostic. `normalize_openai()`, `normalize_anthropic()`, and `normalize_raw()` convert any request format into the provider-neutral `NormalizedPayload` before governance evaluation begins.

---

## `sovereign-sensor` — Bare-Metal Write-Side Custody

For IoT and embedded systems where data must be sealed cryptographically at the exact point of genesis — before any network hop or cloud ingestion — `sovereign-sensor` provides a MicroPython-compatible Hardware Abstraction Layer that runs on ESP32 and Raspberry Pi Pico with zero external dependencies:

```python
from sovereign_sensor import bootstrap_sensor_node

# Selects ESP32HardwareDriver or SoftwareFallbackDriver at runtime.
# Falls back to SoftwareFallbackDriver with a warning while ECC acceleration
# is pending register-level engineering.
envelope = bootstrap_sensor_node(
    node_id="node-temperature-01",
    private_key_path="/flash/keys/node.key",
    sequence_file="/flash/.sovereign_sequence",
)

observation = {"sensor": "temperature", "value": 21.4, "unit": "C"}
wire_bytes = envelope.seal("2026-06-16T12:00:00Z", observation)
# → b'{"alg":"hmac-sha256","d":{...},"n":"node-temperature-01","q":1,"s":"<64-char hex>","t":"...","v":1}'
```

Each sealed frame carries a monotonic sequence counter (`q`) persisted to VFS across hardware reboots, an HMAC-SHA256 signature over a deterministic UTF-8 preimage, and a `sort_keys=True, ensure_ascii=False` wire serialization that is byte-identical across CPython and bare-metal MicroPython for any payload — including Unicode sensor labels and multi-byte locale strings.

For desktop CI and testing, pass `SoftwareFallbackDriver.MOCK_KEY_SENTINEL` as `private_key_path` to opt into a deterministic stub key without a provisioned key store. See [`packages/sovereign-sensor/README.md`](packages/sovereign-sensor/README.md) for the full HAL contract, driver table, and invariants reference.

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
from sovereign_ledger import SovereignLedger

gateway = SovereignGateway(signing_key=".keys/sovereign_identity.pem")
ledger  = SovereignLedger(db_path=".keys/sovereign_audit.db")

@app.post("/api/v1/ingest")
async def handle_agent_input(raw_payload: dict):
    result = await gateway.sieve_and_sign(raw_payload["text"])
    ledger.append_receipt(result.receipt, result.content)
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

---
(c) 2026 - Ken W. Alger