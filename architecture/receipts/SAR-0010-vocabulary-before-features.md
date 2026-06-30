# SAR-0010: Vocabulary Before Features

## Status

Accepted

## Boundary

Concept → Implementation

## Decision

New Sovereign SDK components, capabilities, and architectural patterns should emerge from established Sovereign Systems vocabulary before introducing new implementation-specific abstractions.

## Context

As the Sovereign Systems Specification grows, there is a natural tendency to propose new features based on emerging technologies, popular frameworks, or isolated implementation needs.

While many of these proposals may be technically valid, not all of them contribute to the architectural coherence of the system.

The Specification exists to establish a shared language for reasoning about computational sovereignty. The SDK exists to operationalize that language.

## Rationale

Vocabulary is architecture.

Terms such as:

* Point of Genesis
* Write-Side Custody
* Silicon Locality
* Capability Gradient
* Escalation Boundary
* Sovereign Envelope
* Forensic Receipt
* Reasoning Ledger

are not merely glossary entries.

They represent architectural primitives.

When new functionality emerges from existing vocabulary, the system becomes easier to understand, teach, document, and extend. Contributors can reason about proposals using shared concepts rather than implementation details.

Conversely, features that appear without a corresponding architectural concept often create fragmentation, overlapping responsibilities, and long-term maintenance burden.

The question should not be:

> What feature should we build next?

The question should be:

> Which boundary, principle, or architectural concept requires implementation?

## Consequences

### Accepted

* Components that operationalize established Specification concepts.
* New SDK packages that clearly map to existing architectural vocabulary.
* Expansion of existing patterns into executable implementations.
* Introduction of new terminology when a genuine architectural gap exists.

### Rejected

* Feature proposals based solely on framework trends.
* Components that cannot identify a governing boundary.
* Utility libraries that duplicate existing responsibilities.
* Architectural shortcuts that bypass established terminology and concepts.

## Examples

### Accepted

* `sovereign-sdk-sensor` emerging from Point of Genesis.
* `sovereign-sdk-edge` emerging from Silicon Locality and Capability Gradient.
* Future outbound governance systems emerging from Escalation Boundary and Write-Side Custody.

### Rejected

* Generic AI orchestration utilities with no clear boundary ownership.
* Framework-specific integrations presented as architectural primitives.
* Features that cannot explain their relationship to the Specification.

## Principle

The Specification defines the language.

The language defines the architecture.

The architecture defines the implementation.

Not the other way around.
