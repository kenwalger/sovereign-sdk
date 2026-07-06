# sovereign-sdk-airlock

Model-neutral outbound governance boundary for the Sovereign Systems SDK.

Inspects, evaluates, minimises, and records structured payloads before they cross
a sovereign perimeter and enter an external computational system.

---

## Architecture

`sovereign-sdk-airlock` implements the four-component lifecycle defined in the
Airlock Specification (SAR-0004):

| Component | Responsibility |
|---|---|
| **Boundary Interception** | Capture normalised outbound payloads; transport-agnostic |
| **Policy Engine** | Evaluate local YAML governance rules (`raw`, `fields`, `telemetry` scopes) |
| **Sieve Convergence** | Deterministic context minimisation via `sovereign-sdk-sieve` |
| **Evidence Generation** | Immutable `ForensicReceipt` committed to `sovereign-sdk-ledger` |

---

## Quick Start

```python
import asyncio
from sovereign_ledger import SovereignLedger
from sovereign_airlock import AirlockBoundary, normalize_openai

ledger = SovereignLedger(".keys/sovereign_audit.db")
boundary = AirlockBoundary(
    policy_path="policy.yaml",
    signing_key=".keys/",
    ledger=ledger,
)

request = {
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Analyse the quarterly report."}],
}

async def main():
    result = await boundary.process(normalize_openai(request))
    print(result.sieved_content)
    print(result.telemetry.tax_savings_percentage, "% Prose Tax savings")
    print(result.receipt["metadata"]["payload_hash"])  # raw pre-sieve content hash

asyncio.run(main())
```

---

## Policy Configuration

```yaml
version: "1.0"

global:
  max_token_ceiling: 40000
  prose_tax_warning_threshold: 0.35

rules:

  - name: block_private_keys
    scope: raw
    pattern: "-----BEGIN PRIVATE KEY-----"
    action: deny

  - name: guard_internal_namespaces
    scope: fields
    fields:
      - messages.content
      - tools.description
    pattern: "internal\\.sovereign\\.local"
    action: warn

  - name: excessive_context
    scope: telemetry
    metric: raw_tokens
    threshold: 30000
    action: warn
```

### Policy Actions

| Action | Behaviour |
|---|---|
| `allow` | Explicit pass-through — no effect on verdict |
| `warn` | Appends to `AirlockResult.policy_warnings`; transmission continues |
| `deny` | Raises `AirlockPolicyViolation`; payload blocked |

### Exception Inspection

When a post-sieve `deny` rule fires, any `warn`-action messages accumulated during the
pre-sieve pass are preserved on the exception for diagnostic inspection:

```python
try:
    result = await boundary.process(payload)
except AirlockPolicyViolation as exc:
    print(exc)           # deny violation message
    print(exc.warnings)  # pre-sieve warn messages (list[str], may be empty)
    raise
```

---

## Transport Normalisation

Airlock governs boundaries, not transports. Use the factory functions to convert
provider-specific request formats into the provider-neutral `NormalizedPayload`:

```python
from sovereign_airlock import normalize_openai, normalize_anthropic, normalize_raw

# OpenAI-compatible chat completion request
payload = normalize_openai({"messages": [...], "model": "gpt-4o"})

# Anthropic-compatible messages API request
payload = normalize_anthropic({"messages": [...], "system": "...", "model": "claude-sonnet-4-6"})

# Raw string
payload = normalize_raw("plain text to govern")
```

---

## Invariants

- Zero external network dependencies — all operations execute on local silicon.
- Ledger write failure is non-fatal; outbound transmission is never blocked by a storage anomaly.
- Context minimisation uses `sovereign-sdk-sieve` exclusively — no model-based compression.
- All policy evaluation is deterministic and auditable.
