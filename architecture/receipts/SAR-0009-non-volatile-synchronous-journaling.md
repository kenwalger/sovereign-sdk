# SAR-0009: Non-Volatile Synchronous Journaling

## Status
Accepted

## Boundary
Volatile Memory → Durable Storage

## Decision
Every state transition, ingestion milestone, and operational exception within the edge pipeline must be flushed synchronously to non-volatile physical storage before an internal function loop yields or returns success.

## Context
Typical edge architectures use in-memory message queues to buffer data streams before background threads write them to disk. While fast, this introduces an unacceptable data loss vector during power drops or core panics.

## Rationale
In real-world peripheral environments—such as a factory floor (`manufacturing`) or an un-grid-tethered field cluster (`iot`)—hardware restarts, kernel panics, and unexpected power disconnections are standard operating conditions. By treating active process memory as a transient hazard, the system ensures complete post-crash data recovery without silent record omissions.

## Consequences
* **Accepted:** Atomic file promotion patterns (`os.replace`), descriptor-level permission locks, and immediate local append-only logging to JSONL buffers.
* **Rejected:** Relying on unbacked, volatile in-memory queues or batching pools to manage core ingestion receipts.