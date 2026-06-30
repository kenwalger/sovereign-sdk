# Sovereign SDK Engineering Philosophy

## The Sovereign Creed

The Sovereign Systems Specification is built for high-assurance, local-first environments where network connectivity is intermittent, local storage is constrained, and data integrity is non-negotiable. 

To achieve this, every module added to the `sovereign-sdk-*` workspace must adhere to a strict set of deterministic systems-programming invariants. We do not write software for the cloud happy-path; we engineer software that treats the underlying runtime environment as actively adversarial.

## Core Architectural Invariants

### 1. Zero-Dependency Core Surface
* **The Rule:** Core package libraries (`core`, `sensor`, `edge`, `sieve`, `ledger`) must maintain a minimal, strictly validated third-party dependency footprint. Where possible, standard library primitives must be leveraged.
* **The Rationale:** Minimizing dependencies drastically reduces the supply-chain attack surface, mitigates dependency-hell version locks in enterprise environments, and ensures compliance with limited bare-metal execution runtimes (such as MicroPython on edge microcontrollers).

### 2. Physical and Logical Isolation at Boundaries
* **The Rule:** Every package exists exclusively to govern a single, distinct boundary crossing. Packages must never interleave responsibilities. 
* **The Rationale:** If a component captures data from hardware pins (`sovereign-sdk-sensor`), it must know nothing about HTTP networking. If a component processes string transformations (`sovereign-sdk-sieve`), it must know nothing about database filesystems. Isolation ensures that components can be cleanly swapped, audited, and embedded in isolation.

### 3. Synchronous, On-Disk Log Journaling (The Anti-Memory Principle)
* **The Rule:** State changes, ingestion records, and transactional milestones must be flushed synchronously to non-volatile physical storage *before* any operational lifecycle function yields or returns a success code. Volatile in-memory queuing must always be backed by an immutable on-disk append-only log.
* **The Rationale:** In real-world edge environments, power-loss events and kernel panics are normal occurrences. By treating volatile memory as a transient hazard, we ensure the system can recover to a clean state across arbitrary hardware failures without silent data corruption.

### 4. Strict Non-Repudiation (Point-of-Genesis Sealing)
* **The Rule:** Data captured at the periphery must be cryptographically sealed via on-chip hardware acceleration or lightweight signature wrappers at the exact millisecond it is born.
* **The Rationale:** Centralized cloud databases routinely mutate historical timestamps and audit logs. By signing payloads immediately at the physical ingestion boundary, data provenance becomes mathematically immutable before it ever traverses a network wire.

### 5. Deterministic Failures over Graceful Degradation
* **The Rule:** When a local system constraint is breached (e.g., a filled disk sector, an unresolvable HMAC verification failure, or an invalid payload model state), the SDK must intentionally surface an explicit, non-swallowed exception and halt or quarantine the execution context.
* **The Rationale:** Swallowing runtime errors or substituting null data to maintain uptime is the primary cause of downstream state pollution. We prefer an explicit, safe local shutdown or a clean quarantine branch over an untrustworthy, silent partial execution.