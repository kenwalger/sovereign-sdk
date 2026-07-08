# CLAUDE.md

## Purpose

This repository implements components of the Sovereign Systems Specification.

The specification's primary thesis is:

> Information without provenance is just gossip.

All implementation decisions should preserve provenance, accountability, and evidence generation.

---

## Architectural Principles

### Memory Begins Before Retrieval

Do not treat retrieval as memory.

Memory is established during ingestion through validation, classification, provenance capture, and evidence recording.

---

### Provenance Is Mandatory

All significant system outputs should be traceable to their source.

Prefer explicit evidence chains over inferred explanations.

---

### Forensic Receipts Are First-Class Artifacts

Forensic Receipts are not optional metadata.

They are core system outputs.

When adding features, preserve receipt generation whenever possible.

---

### Deterministic Before Probabilistic

Prefer deterministic logic for:

- Validation
- Classification
- Governance
- Policy Enforcement
- Evidence Generation

LLMs should augment reasoning, not replace deterministic system controls.

---

### Local First

Prefer local execution.

Do not introduce cloud dependencies unless explicitly requested.

Do not assume internet connectivity.

---

### Separation of Responsibilities

Maintain strict boundaries between SDK components.

Examples:

- Sensor captures observations.
- Edge classifies and routes.
- Sieve reduces Prose Tax.
- Ledger records evidence.
- Airlock governs outbound crossings.
- Vault manages long-term memory.

Do not collapse responsibilities between components.

---

## Development Guidance

When proposing changes:

1. Preserve existing architectural boundaries.
2. Prefer simple implementations.
3. Favor explainability over cleverness.
4. Keep provenance visible.
5. Avoid introducing hidden side effects.

---

## Anti-Patterns

Do not:

- Replace Sieve with LLM summarization.
- Allow Ledger to make policy decisions.
- Bypass receipt generation.
- Hide evidence behind generated answers.
- Introduce opaque automation without auditability.
- Turn the project into a generic chatbot framework.

---

## Preferred Outcome

Every feature should improve one or more of:

- Provenance
- Accountability
- Explainability
- Governance
- Memory Quality
