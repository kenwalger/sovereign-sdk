# Sovereign Systems Architecture Workspace

A high-integrity, local-first monorepo platform enforcing the architectural boundaries of the **Sovereign Systems Specification**. This workspace contains the development frameworks, primitive tools, and local execution barriers designed to eliminate the Prose Tax, guarantee cryptographic provenance, and establish append-only chains of custody for agentic workflows entirely on local silicon.

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
