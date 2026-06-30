# SAR-0008: Deterministic Context Reduction over Lossy Compression

## Status
Accepted

## Boundary
Prompt → Structure

## Decision
The `sovereign-sdk-sieve` component must rely exclusively on deterministic, auditable text-transformation primitives (regex libraries, structural pruning, semantic token heuristics) to minimize the "Prose Tax." It shall not use non-deterministic model abstractions to compress strings.

## Context
When reducing prompt bloat to save outbound token costs, a common temptation is to use a small, local Language Model (SLM) to summarize or compress the raw input before shipping it to an external frontier API.

## Rationale
Using an AI to optimize an input destined for another AI introduces a layer of non-deterministic translation. This breaks the primary objective of Sovereign Systems: trustworthy, auditable computation. If the optimization layer can introduce hallucinations or change the semantic meaning of a prompt, the subsequent `ForensicReceipt` is no longer a verifiable record of true system state. Sieve must remain an auditable function.

## Consequences
* **Accepted:** Heuristic token accounting, structural text stripping, and static conversational boilerplate extraction patterns.
* **Rejected:** Using neural text-summarization or generative lossy compression layers within the inline prompt purification pipeline.