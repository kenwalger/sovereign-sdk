# Commercial Strategy

## Open Source Wedge

The Sovereign SDK remains fully open source.

Primary objectives:

* Developer adoption
* Architectural credibility
* Ecosystem growth
* Community contributions

Open-source components include:

* [sovereign-sdk-core](https://pypi.org/project/sovereign-sdk-core/)
* [sovereign-sdk-fastapi](https://pypi.org/project/sovereign-sdk-fastapi/)
* [sovereign-sdk-sieve](https://pypi.org/project/sovereign-sdk-sieve/)
* [sovereign-sdk-ledger](https://pypi.org/project/sovereign-sdk-ledger/)
* [sovereign-sdk-sensor](https://pypi.org/project/sovereign-sdk-sensor/)
* [sovereign-sdk-edge](https://pypi.org/project/sovereign-sdk-sieve/)

```mermaid
flowchart TD
    A["[ Open-Source SDK ]<br>(Drives Ingestion, Local-First Adoption)") 
    │--> B["[ Developer Mindshare & Scale ]<br>(Validates Core Boundary Architecture)"]
    B --> C["[ Enterprise Boundary Friction ]<br>(Brings Visibility, Cost, & Compliance Risks)"]
    C --> D["[ Commercial Governance Layer ]<br>(Airlock & Audit Drive Revenue)"]

    style A fill:#f9f9f9,stroke:#333,stroke-width:2px
    style D fill:#e1f5fe,stroke:#0288d1,stroke-width:2px
```

The core primitives (`sensor`, `edge`, `sieve`, `ledger`) must be open-source to establish global architectural credibility and frictionless adoption. Developers use the SDK to build resilient local pipelines without asking for budget approval.

## The Commercial Gating Mechanism

As open-source SDK deployments scale within an organization, a natural point of friction emerges when data prepares to cross the organizational perimeter. This boundary is where monetization occurs.

### 1. Sovereign Airlock (Outbound Gateway)
* **Monetization Model:** Enterprise License / Tiered by Throughput Volume.
* **Value Proposition:** An inline proxy that enforces security baselines, strips proprietary data before API transmission, and guarantees that every external model interaction leaves a non-repudiable cryptographic receipt locally on disk.

### 2. Sovereign Audit (Centralized Observability)
* **Monetization Model:** SaaS Dashboard / Managed Enterprise Control Plane.
* **Value Proposition:** Aggregates localized ledger receipts across thousands of edge units or developer seats into a unified control panel. Provides corporate leadership with real-time analytics on token optimization, "[Prose Tax](https://kenwalger.github.io/sovereign-system-spec/terms/prose-tax.html?utm_source=sovereign-sdk&utm_medium=repository&utm_campaign=sovereign-sdk-repo)" savings, regulatory compliance logs, and cross-team model expenditures.

## Revenue Philosophy

We do not monetize data custody, nor do we tax local execution. We monetize the governance, confidence, and economic optimization of data crossing high-risk boundaries.

