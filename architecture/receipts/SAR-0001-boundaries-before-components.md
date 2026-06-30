# SAR-0001: Boundaries Before Components

## Status
Accepted

## Boundary
Conceptual → Implementation

## Decision
Every Sovereign SDK component must govern, preserve, or memorialize a specific computational boundary.

## Context
The SDK now spans ingestion, edge buffering, outbound governance, and future vault custody. Without a boundary-first rule, future components risk becoming generic AI utilities rather than Sovereign Systems primitives.

## Rationale
A component belongs in the SDK only if it answers one of these questions:

- What boundary does it govern?
- What crossing does it validate?
- What evidence does it preserve?

## Consequences
This accepts narrow, opinionated packages and rejects broad utility packages.

## Related Components
- sovereign-sdk-sensor
- sovereign-sdk-edge
- sovereign-sdk-sieve
- sovereign-sdk-ledger
- sovereign-sdk-airlock