# SAR-0007: Zero-Dependency Core Primitives

## Status
Accepted

## Boundary
Supply Chain → Runtime Footprint

## Decision
Core runtime libraries (`core`, `sensor`, `edge`, `sieve`, `ledger`) must maintain a strict zero-dependency or near-zero-dependency footprint, leveraging standard library primitives wherever computationally viable.

## Context
Modern software development is plagued by heavy dependency trees. Introducing packages like `pydantic`, `transformers`, or deep network frameworks into the foundational layers creates immediate enterprise friction: version locking, massive image sizes, and expanding supply-chain attack surfaces.

## Rationale
To ensure the Sovereign SDK can be embedded anywhere—from high-performance developer workstations down to resource-constrained microcontrollers running MicroPython (the `sensor` boundary)—the baseline primitives must remain incredibly lightweight. Security and compliance software cannot audit an asset if it introduces a thousand nested, unvetted dependencies of its own.

## Consequences
* **Accepted:** Standard library solutions, pure-Python fallback implementations, and narrow framework-specific adapters shipped exclusively as optional extras (e.g., `sovereign-sdk-fastapi`).
* **Rejected:** Incorporating heavy machine learning runtime engines or massive third-party data validation utilities directly into the core execution path.
* A dependency introduced into a boundary component becomes part of the boundary's trust surface.