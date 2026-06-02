# sovereign-sieve

**Zero-dependency standalone Prose Tax token optimization utility.**

`sovereign-sieve` extracts the payload-cleansing mechanics from the Sovereign Systems SDK into a pure, synchronous micro-library with no web framework or ML library dependencies. Use it in scripts, batch pipelines, and offline data-processing workers to strip conversational boilerplate and measure immediate token savings — all on local silicon.

---

## Installation

```bash
pip install sovereign-sieve
```

Or, within a `uv` workspace:

```bash
uv add sovereign-sieve
```

---

## Quick Start

### `pure_sieve` — drop-in string cleaner

```python
from sovereign_sieve import pure_sieve

raw = "Hello! Please just help me analyze this dataset."
clean = pure_sieve(raw)
print(clean)
# → "! help me analyze this dataset."
```

### `sieve_with_metrics` — cleaner + FinOps telemetry

```python
from sovereign_sieve import sieve_with_metrics

result = sieve_with_metrics(
    "Hi! I hope this helps. Please just run the pipeline."
)
print(result.text)
# → "! helps. run the pipeline."

print(result.raw_token_count)        # estimated tokens before sieve
print(result.optimized_token_count)  # estimated tokens after sieve
print(result.tax_savings_percentage) # e.g. 66.6667 (%)
```

### Offline batch pipeline

```python
from sovereign_sieve import pure_sieve

records = load_jsonl("prompts.jsonl")
cleaned = [pure_sieve(r["text"]) for r in records]
save_jsonl("prompts_clean.jsonl", cleaned)
```

---

## What gets stripped

| Category | Examples stripped | Examples preserved |
|---|---|---|
| Greeting tokens | `hi`, `hello`, `hey`, `greetings` | `hi-fi`, `hello-world` |
| Hedging adverbs | `please`, `just`, `simply`, `actually`, `basically`, `probably` | `just-in-time`, `simply-typed` |
| Affirmation filler | `of course`, `certainly`, `absolutely`, `sure` (standalone) | `make sure to`, `be sure to`, `certainly-not` |
| Preamble phrases | `I hope this`, `I hope that`, `I hope you` | trailing sentence content preserved |
| Degenerate markdown | `****`, `_____` (4+ bare markers) | `**bold**`, `*italic*`, `***bold-italic***` |
| Duplicate headings | consecutive identical `## Heading\n## Heading` | headings differing in case or level |

---

## SieveOutput fields

```python
from sovereign_sieve import SieveOutput

result: SieveOutput = sieve_with_metrics("Hello! Please help me now.")
result.text                  # str  — minimized payload
result.raw_token_count       # int  — byte-density token estimate before sieve
result.optimized_token_count # int  — byte-density token estimate after sieve
result.tax_savings_percentage # float — reduction percentage, 4 decimal places
```

Token counts use the UTF-8 byte-density heuristic (÷ 4), matching the average density of the cl100k_base tokenizer across mixed English/code content. No external tokenizer is invoked.

---

## Key manager usage (sovereign-core)

For cryptographic signing of sieved payloads, use `SovereignGateway` from `sovereign-core`:

```python
import asyncio
from sovereign_core.gateway import SovereignGateway

async def main():
    gateway = SovereignGateway(signing_key=".keys/sovereign_identity.pem")
    result = await gateway.sieve_and_sign("Hello! Please just help me.")
    print(result.content)          # minimized text
    print(result.receipt["signature"])  # Ed25519 signature

asyncio.run(main())
```

---

## License

MIT
