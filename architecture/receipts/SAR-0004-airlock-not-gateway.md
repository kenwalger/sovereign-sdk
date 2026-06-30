# SAR-0004: Airlock, Not Gateway

## Status

Accepted

## Boundary

Sovereign Perimeter → External System

## Decision

Outbound governance components shall use the term "Airlock" rather than "Gateway."

## Context

The project originally considered naming the outbound boundary component a gateway.

While technically accurate, the term fails to describe the architectural responsibility of the system.

## Rationale

A gateway routes traffic.

An airlock governs transitions.

The outbound boundary is not merely forwarding requests.

It is:

* Evaluating context
* Measuring Prose Tax
* Recording provenance
* Applying policy
* Generating evidence

The airlock metaphor better reflects the deliberate inspection and controlled traversal of a trust boundary.

## Consequences

The component is conceptually positioned as a governance boundary rather than a networking utility.

Future implementations should prioritize:

* Inspection
* Verification
* Accountability

over simple routing.

## Related Components

* sovereign-sdk-airlock
* sovereign-sdk-sieve
* sovereign-sdk-ledger
