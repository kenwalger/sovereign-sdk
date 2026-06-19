# sovereign-edge

**High-Integrity Middleware Pipeline and Asynchronous Ingestion Bridge for Sovereign Systems.**

`sovereign-edge` acts as the durable middle-tier intake handler for the Sovereign Systems ecosystem. It intercepts sealed sensor wire envelopes generated at the point of data genesis by `sovereign-sensor`, processes them locally using `sovereign-sieve` to enforce Prose Tax token optimization, and commits the resulting high-integrity records to `sovereign-ledger`. 

To insulate the runtime environment from disk I/O bottlenecks and network volatility, `sovereign-edge` features an off-grid background journaling buffer that preserves strict chronological ordering and fault-mode custody loops.

---

## Architecture Overview

The pipeline operates as a deterministic, four-stage loop running with zero network dependencies:

```mermaid
graph TD
    A[Raw Wire Bytes] -->|1. Deserialize| B(SensorFrame)
    B -->|2. Sieve Pass| C{Prose Tax Optimization}
    C -->|Success| D[Refined Bytes]
    C -->|Sieve Fault| E[Raw Passthrough / sieve_fault=True]
    D -->|3. Sign Phase| F(Ed25519 ForensicReceipt)
    E -->|3. Sign Phase| F
    F -->|4. Dispatch Target| G{SovereignLedger}
    G -->|Storage Error / Offline| H[OffGridBuffer]
    H -->|Async Thread + fsync| I[JSONL Disk Log]
    I -->|Chronological drain()| G
```

1. **Deserialize:** Extracts the seven-key minified wire frame (`v`, `n`, `t`, `q`, `alg`, `d`, `s`) arriving from embedded hardware via `SensorFrame.from_bytes()`.
2. **Sieve:** Executes a local token-reduction pass via `sieve_with_metrics()` to strip conversational padding and whitespace anomalies.
3. **Sign:** Mints a local `ForensicReceipt` via `SovereignKeyManager`, sealing both the payload hash and the transformation telemetry with an Ed25519 identity.
4. **Commit:** Attempts to write immediately to the ledger, falling back seamlessly to an isolated daemon background thread that manages durable disk-bound serialization if the target is locked or offline.

---

## Core Implementations

### Inbound Intake Contract: `SensorFrame`
The pipeline maps incoming wire bytes directly to a typed `SensorFrame` dataclass. To guarantee that validation metrics are independent of dictionary key insertion order, `text_content()` exposes a deterministic, sort-keyed, `ensure_ascii=False` serialization surface for downstream ingestion.

### Asynchronous Durability: `OffGridBuffer`
When log destinations are unreachable, entries pass into a single-line JSONL-backed file queue. Writes are dispatched to an isolated daemon background thread, decoupling caller execution from blocking disk-bound `os.fsync` latency. 

* **Chronological Ordering:** Every entry carries an internal hardware sequence identifier (`q`). During a ledger recovery pass, `drain()` flushes background I/O handles and sorts the file records by ascending sequence before replay, protecting the linearity of the destination hash chain.
* **Atomic Clears:** To eliminate dual-replay windows during crashes, the queue clears itself using an atomic `tempfile` promotion loop.

### Resilient Processing: `EdgePipeline`
The `EdgePipeline` coordinates execution through strict exception isolation barriers. If `sovereign-sieve` encounters an anomalous payload structure, processing falls back seamlessly to the raw text payload, sets tax metrics to `0.0`, and stamps `sieve_fault=True` inside the ledger metadata. The audit record is never broken.

---

## Installation & Workspace Configuration

Register the package inside your local environment or workspace architecture using Python packaging tools:

```toml
[project]
name = "sovereign-edge"
version = "0.1.0"
dependencies = [
    "sovereign-core>=1.1.0",
    "sovereign-ledger>=1.1.0",
    "sovereign-sieve>=1.1.0",
]
```

---

## Quick Start Usage

```python
from sovereign_edge import EdgePipeline
from sovereign_ledger import SovereignLedger

# Initialize destination tier
ledger = SovereignLedger(".keys/sovereign_audit.db")

# Bind middleware pipeline
pipeline = EdgePipeline(
    ledger=ledger,
    signing_key=".keys/edge_identity.pem",
    buffer_path=".edge_buffer.jsonl"
)

# Process incoming wire frames arriving from sovereign-sensor hardware
raw_sensor_bytes = b'{"alg":"hmac-sha256","d":{"temp":23.4},"n":"node-01","q":42,"s":"...","t":"2026-06-19T10:00:00Z","v":1}'
result = pipeline.process(raw_sensor_bytes)

print(f"Committed Hash: {result.payload_hash}")
print(f"Buffered Status: {result.buffered}")  # True if ledger was locked/offline

# Recovery Pass: When ledger locks release, flush the off-grid queue chronologically
if pipeline.buffer_depth > 0:
    committed_hashes = pipeline.drain_buffer()
```

---

## Operational Verification Properties

Every engineering iteration is validated using a comprehensive desktop test harness ensuring the following pipeline invariants remain unbroken:

| Invariant Verification Property | Target File | Verification Metric |
| :--- | :--- | :--- |
| **Zero-Block Ingestion** | `buffer.py` | Asynchronous worker protects ingestion loops from blocking system execution. |
| **Sequence Continuity** | `buffer.py` | Playback logic enforces strict ascending sort order by source counter values. |
| **Fault Containment** | `pipeline.py` | Sieve-layer runtime anomalies degrade to a raw passthrough marked with explicit fault tags. |
| **Hash-Chain Stability** | `tests/` | Post-drain replay states leave destination ledger linearity intact. |
