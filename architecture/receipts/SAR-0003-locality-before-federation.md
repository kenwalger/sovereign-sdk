# SAR-0003: Locality Before Federation

## Status

Accepted

## Boundary

Local Operation → Distributed Operation

## Decision

Every Sovereign component must provide meaningful standalone value before participating in any federated, clustered, or mesh-based architecture.

## Context

Many distributed systems begin by assuming constant connectivity, centralized coordination, and external control planes.

The Sovereign Systems Specification is founded on the opposite assumption:

Connectivity is optional.

Autonomy is mandatory.

## Rationale

A Sovereign Node must remain useful even when isolated.

A system that only functions when connected to a federation is not sovereign.

Local utility must precede network utility.

This principle applies equally to:

* Sensors
* Edge Nodes
* Ledgers
* Memory Systems

## Consequences

Accepted:

* Offline operation
* Local custody
* Local verification
* Autonomous recovery

Rejected:

* Mandatory cloud dependencies
* Always-online architectures
* Centralized control requirements

Federation is an enhancement.

It is never a prerequisite.

## Related Components

* sovereign-sdk-sensor
* sovereign-sdk-edge
* sovereign-mesh
* sovereign-sdk-vault
