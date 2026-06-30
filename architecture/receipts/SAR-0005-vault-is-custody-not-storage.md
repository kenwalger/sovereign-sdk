# SAR-0005: Vault Is Custody, Not Storage

## Status

Accepted

## Boundary

Memory → Custody

## Decision

`sovereign-sdk-vault` exists to govern knowledge custody.

It is not merely a storage system.

## Context

Many AI memory systems are effectively databases with retrieval interfaces.

While useful, they do not address questions of:

* Provenance
* Ownership
* Retention
* Lineage
* Trust

The Vault is intended to solve those problems.

## Rationale

Storage answers:

> Where is the data?

Custody answers:

> Why does it exist?
>
> Who created it?
>
> Who may modify it?
>
> How long should it survive?
>
> Can its lineage be verified?

These are fundamentally different concerns.

## Consequences

Accepted:

* Provenance tracking
* Retention policies
* Knowledge lineage
* Cryptographic verification
* Sovereign memory custody

Rejected:

* Generic object storage
* Simple vector databases
* Commodity persistence layers

Storage engines may exist beneath the Vault.

They are not the Vault itself.

## Related Components

* sovereign-sdk-vault
* sovereign-sdk-ledger
* sovereign-sdk-airlock
