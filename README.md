# Sovereign Systems Architecture Workspace

A high-integrity, local-first monorepo platform enforcing the architectural boundaries of the **Sovereign Systems Specification**. This workspace contains the development frameworks, primitive tools, and local execution barriers designed to eliminate the Prose Tax, guarantee cryptographic provenance, and establish append-only chains of custody for agentic workflows entirely on local silicon.

To learn more about the philosophy and reasoning behind the project's origins, please check out [this doc](PHILOSOPHY.md).

---

## Workspace Topography

This repository is managed as an integrated workspace using `uv`. It cleanly separates pure data/cryptographic boundaries from actual tool execution runtimes:

```text
.
├── packages/
│   ├── sovereign-core/       # Pure data tier (Zero high-compute dependencies)
│   │   └── src/sovereign_core/
│   │       ├── crypto.py     # Ed25519 key management & Forensic Receipt minting
│   │       └── gateway.py    # Context Cleansing & Prose Tax ledger calculations
│   │
│   └── sovereign-runtime/    # Compute/Execution tier (Tool & Model isolation)
│       └── src/sovereign_runtime/
│           ├── router.py     # Intent-Based Pre-Flight Namespace Exposure
│           └── __main__.py   # Execution runtime entrypoint
│
├── main.py                   # High-level monorepo testing orchestrator
├── pyproject.toml            # Monorepo configuration & workspace links
└── uv.lock                   # Deterministic dependency lockfile
```

## Core Systems Implementation Target
1. The Ingestion Boundary (`sovereign-core`)
Implements strict token and structural filters to execute Context Cleansing. It aggressively evaluates inbound traffic strings, strips conversational boilerplates or markdown filler, and calculates the exact byte/token delta returned to the host environment as a Prose Tax balance profile.

2. The Sieve-and-Sign Pattern (`sovereign-core`)
Wraps bare-metal cryptographic signing primitives. Every piece of cleansed low-entropy data passing through the ingestion gateway is hashed, stamped with an immutable millisecond timestamp, and sealed with a local `Ed25519` keypair to generate a structured Forensic Receipt—forming the foundation of a local Chain of Custody Ledger.

3. Intent-Based Namespace Exposure (`sovereign-runtime`)
Handles local pre-flight intent routing. Before an application prompt or agent loop communicates with resource-heavy external APIs or executable local tools, a lightweight local tokenizer and model wrapper evaluates the context block, pruning tool access entirely on local silicon to isolate the computation domain.

## Local Development Lifecycle
This workspace utilizes `uv` for seamless, lightning-fast cross-package dependency resolution.

### Bootstrap Workspace
Initialize the environment and synchronize all editable workspace packages in a single step:

```bash
uv sync
```

### Execution Interface
Run the main runtime entry point via the workspace wrapper:
```bash
uv run sovereign-runtime
# Or execute the high-level root monitor orchestrator
uv run main.py
```

### Testing Strategy
To execute test suites globally across all isolated workspace members:
```bash
uv run pytest
```

---

## Verification & Deep-Dive Diagnostics

This section documents how to exercise the local cryptographic runtime in
isolation and interpret the output produced at each layer of the stack.

### Local Environment Configuration

`SOVEREIGN_NODE_SECRET` can be specified in a `.env` file at the repository
root instead of being exported manually before each invocation.  The node
entrypoint loads this file automatically on startup via `python-dotenv`.

Create `.env` once (it is gitignored and never committed):

```bash
echo 'SOVEREIGN_NODE_SECRET=your-local-secret' > .env
```

All subsequent `uv run sovereign-node` invocations will pick up the value
automatically without any additional shell configuration.

### Standalone Tool Analysis Mode

To initialize the node in standalone tool analysis mode — bypassing the full
download → analyze pipeline and exercising only the `analyze` tool directly
against an empty session context — run:

```bash
uv run sovereign-node --tool analyze
```

On Windows (PowerShell):

```powershell
uv run sovereign-node --tool analyze
```

If you prefer to pass the secret inline rather than via `.env`:

```bash
SOVEREIGN_NODE_SECRET=<your-secret> uv run sovereign-node --tool analyze
```

### Expected Console Output

A successful standalone invocation produces the following sequence:

```
====================================================
🟢 Sovereign Node initialization sequence successful.
====================================================
🔄 Dispatching single tool execution: 'analyze'...
⚠️ Standalone mode detected: Context empty. Hydrating baseline diagnostic state...

🔒 Authenticated Forensic Receipt Proof:
{
  "timestamp": "2026-05-21T15:00:00.000000+00:00",
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
| `🟢 Sovereign Node initialization sequence successful.` | Ed25519 keypair loaded or generated; `SOVEREIGN_NODE_SECRET` resolved; router and session context initialised without error. |
| `⚠️ Standalone mode detected: Context empty. Hydrating baseline diagnostic state...` | The `analyze` tool detected no upstream `download` result in `context.variables` and self-hydrated a baseline telemetry stream — expected behaviour in single-tool invocations. |
| `"payload_hash": "4fec03e7..."` | SHA-256 digest of the deterministically serialised execution payload.  Identical across repeated invocations of the same tool with the same arguments, proving process-stable hash alignment. |
| `"public_key": "<base64>"` | Base64-encoded raw Ed25519 public key.  Matches `router.key_manager.public_key` exactly; verified by `_audit_receipt` before the process exits, proving zero-indexed ledger tracking. |
| `"signature": "<base64>"` | Ed25519 signature over the canonical manifest `{"metadata": …, "payload_hash": …, "timestamp": …}`.  Any mutation of `metadata`, `payload_hash`, or `timestamp` after issuance causes `verify_receipt` to return `False`, proving active cryptographic envelope sealing. |
| `"execution_success": true` | The tool completed without raising an exception; the receipt is audit-clean. |

### What This Validation Suite Proves

Running `uv run sovereign-node --tool analyze` in isolation confirms three
invariants of the local cryptographic runtime:

1. **Process-stable SHA-256 signature alignment** — the `payload_hash` is
   derived from a deterministically serialised payload dict
   (`json.dumps(sort_keys=True, default=str)`), so the same tool invocation
   always produces the same hash regardless of Python dict insertion order or
   execution environment.

2. **Zero-indexed ledger tracking** — the `execution_depth` counter starts at
   `0` for a fresh `SessionContext` and is incremented unconditionally before
   each tool dispatch, guaranteeing that every receipt — including failed or
   retried ones — occupies a unique ledger slot with no collisions.

3. **Active cryptographic envelope sealing** — the Ed25519 signature covers
   `timestamp`, `payload_hash`, and `metadata` atomically.  The `_audit_receipt`
   helper verifies the key pin, re-derives the payload hash, and checks the
   signature before the process exits.  A tampered or rogue-keypair receipt
   causes a non-zero exit code and a `🚨` fraud alert on `stderr`.
