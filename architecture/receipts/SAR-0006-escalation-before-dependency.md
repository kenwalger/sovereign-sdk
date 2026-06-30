# SAR-0006: Escalation Before Dependency

## Status

Accepted

## Boundary

Capability Gradient → Escalation Boundary

## Decision

When a node cannot safely complete a task, it should escalate the task rather than become dependent upon a more capable system.

## Context

Modern AI architectures often assume immediate delegation to larger external models whenever uncertainty appears.

This creates hidden dependencies and erodes local autonomy.

## Rationale

The goal of the Capability Gradient is not to prevent escalation.

The goal is to make escalation explicit.

A Sovereign Node should understand:

* Why escalation occurred
* What capability was missing
* What evidence triggered the decision

Escalation is a controlled boundary crossing.

Dependency is architectural surrender.

## Consequences

Accepted:

* Structured escalation
* Explicit capability checks
* Documented delegation paths

Rejected:

* Silent fallback behavior
* Hidden cloud dependencies
* Automatic trust expansion

Every escalation should produce evidence.

## Related Components

* sovereign-sdk-edge
* sovereign-sdk-airlock
* sovereign-node
* sovereign-mesh
