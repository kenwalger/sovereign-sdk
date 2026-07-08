# GitHub Copilot Instructions

This repository follows the Sovereign Systems Specification.

## Core Principle

Information without provenance is just gossip.

## Design Goals

The system prioritizes:

- Provenance
- Accountability
- Explainability
- Auditability
- Local-first operation

## Development Expectations

When generating code:

- Preserve component boundaries.
- Favor deterministic implementations.
- Keep evidence generation visible.
- Avoid introducing hidden state.
- Maintain compatibility with Forensic Receipt workflows.

## Component Responsibilities

| Component | Responsibility |
|------------|---------------|
| Sensor | Capture observations |
| Edge | Classification and routing |
| Sieve | Prose Tax reduction |
| Ledger | Evidence and receipt generation |
| Airlock | Outbound governance |
| Vault | Long-term memory custody |

## Avoid

- Generic chatbot abstractions
- Hidden AI decision-making
- Cloud-only assumptions
- Mixing governance and evidence layers
- Bypassing provenance capture

## Preferred Architecture

Observation
→ Validation
→ Classification
→ Optimization
→ Evidence
→ Governance
→ Memory
→ Reasoning

Preserve this lifecycle whenever possible.
