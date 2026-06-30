# SAR-0002: Ledger Is Evidence, Not Policy

## Status

Accepted

## Boundary

Execution → Evidence

## Decision

`sovereign-sdk-ledger` records, signs, and preserves evidence of computational events.

It does not authorize actions, enforce policy, or determine operational permissions.

## Context

As the Sovereign SDK evolved, multiple architectural discussions proposed expanding the ledger to include authorization logic, policy enforcement, and approval workflows.

While these capabilities are valuable, they represent a fundamentally different concern.

The ledger exists to answer:

> What happened?

It does not exist to answer:

> Should this happen?

## Rationale

Evidence systems and policy systems operate under different trust assumptions.

A ledger must remain an objective historical artifact.

If policy enforcement becomes embedded inside the ledger itself, the system loses the separation between:

* Decision
* Action
* Evidence

Maintaining this distinction preserves forensic integrity.

## Consequences

Accepted:

* Forensic receipts
* Reasoning ledgers
* Cryptographic signatures
* Immutable event history

Rejected:

* Authorization engines
* Access control
* Approval workflows
* Governance policy enforcement

These concerns belong to future custody and governance layers.

## Related Components

* sovereign-sdk-ledger
* sovereign-sdk-airlock
* sovereign-sdk-vault
