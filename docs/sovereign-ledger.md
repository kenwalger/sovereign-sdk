# sovereign-ledger

**Immutable, local-first, hash-chained SQLite provenance engine for ForensicReceipt
Write-Side Custody.**

`sovereign-ledger` is a zero-external-dependency workspace package that stores every
`ForensicReceipt` produced by `sovereign-core` in a tamper-evident, append-only
SQLite database.  It enforces Write-Side Custody through two complementary mechanisms:
engine-level SQL triggers that abort any `UPDATE` or `DELETE` at the SQLite layer, and
a SHA-256 hash chain that makes out-of-band filesystem tampering mathematically
detectable.

---

## Quick start

```python
from sovereign_ledger import SovereignLedger

# Open (or create) the audit database — context manager guarantees connection release
with SovereignLedger(db_path=".keys/sovereign_audit.db") as ledger:
    # Append a signed ForensicReceipt after a sieve-and-sign pass
    ledger.append_receipt(receipt, sieved_content)

    # Verify the full chain integrity at any time
    ok = ledger.verify_ledger_integrity()                       # True on an untampered ledger

    # Pin the sweep to a known tip to detect tail-truncation attacks
    tip = ledger.append_receipt(receipt, sieved_content)        # capture after last append
    ok  = ledger.verify_ledger_integrity(expected_tip_hash=tip) # False if last row was deleted
```

---

## Installation

`sovereign-ledger` ships as part of the Sovereign Systems SDK monorepo workspace and
requires no runtime dependencies beyond the Python standard library.

```bash
# Inside the workspace
uv sync
```

---

## Design invariants

### 1. Append-only interface

The public API exposes exactly two methods: `append_receipt` and
`verify_ledger_integrity`.  There are no update, delete, truncate, or patch methods.
The database schema enforces this at the engine level — not just the Python layer.

### 2. Engine-level mutation guards

Two `BEFORE UPDATE` and `BEFORE DELETE` SQL triggers are stored inside the database
file itself:

```sql
CREATE TRIGGER prevent_update_forensic_ledger
BEFORE UPDATE ON forensic_ledger
BEGIN
    SELECT RAISE(ROLLBACK, 'Write-Side Custody violation: UPDATE operations are prohibited on forensic_ledger.');
END;
```

Any client that opens the `.db` file — including desktop SQL browsers — will receive
a `sqlite3.IntegrityError` (SQLITE_CONSTRAINT) on any mutation attempt.
`RAISE(ROLLBACK, ...)` additionally terminates and rolls back the entire enclosing
transaction, preventing post-hoc injection via a subsequent `COMMIT` after catching
the trigger error.

### 3. SHA-256 hash chain — full-row payload sealing with field delimiters

Every row stores a `parent_hash` field that is the SHA-256 digest of the immediately
preceding row's **complete eight-column canonical preimage**, assembled with a NUL byte
(`\x00`) delimiter between every field.  The first row uses a hardcoded genesis
constant as its parent:

```
parent_hash[0]  = SHA-256("SOVEREIGN_LEDGER_GENESIS_BLOCK_v1.0")

parent_hash[N]  = SHA-256(
                      "\x00".join([
                          row[N-1].signature,
                          row[N-1].payload_hash,
                          row[N-1].parent_hash,
                          row[N-1].timestamp,
                          str(row[N-1].raw_token_count),                    ← "NULL" when NULL
                          str(row[N-1].optimized_token_count),              ← "NULL" when NULL
                          f"{float(row[N-1].tax_savings_percentage):.4f}",  ← "NULL" when NULL
                          row[N-1].sieved_content,
                      ])
                  )
```

Sealing all eight data columns in the NUL-delimited preimage provides two layers of
tamper evidence:

- **Complete row coverage** — any out-of-band mutation of any stored value, including
  the textual payload (`sieved_content`), the ingestion timestamp, FinOps telemetry
  (`raw_token_count`, `optimized_token_count`, `tax_savings_percentage`), or
  cryptographic fields, immediately breaks the chain and is detected by
  `verify_ledger_integrity()`.
- **Field-boundary protection** — the NUL delimiter closes length-substitution attacks
  where an adversary shifts content between adjacent fields while keeping their
  concatenation identical (e.g. `"AB"+"CDEF"` vs `"ABC"+"DEF"` produce identical
  naive concatenations but distinct delimited preimages).

### 4. Zero network calls

All operations are purely local.  No telemetry, no external API, no cloud service is
invoked at any point during initialization, append, or verification.

---

## Schema

```sql
CREATE TABLE forensic_ledger (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    payload_hash           TEXT    UNIQUE NOT NULL,
    parent_hash            TEXT    NOT NULL,
    timestamp              TEXT    NOT NULL,
    sieved_content         TEXT    NOT NULL,
    signature              TEXT    NOT NULL,
    raw_token_count        INTEGER,
    optimized_token_count  INTEGER,
    tax_savings_percentage REAL
);
```

Prose Tax token-economy metrics (`raw_token_count`, `optimized_token_count`,
`tax_savings_percentage`) are populated from `receipt["metadata"]["prose_tax_summary"]`
when present, and stored as `NULL` when the key is absent.

---

## SQLite pragmas

The following pragmas are applied at every connection initialization:

| Pragma | Value | Effect |
|---|---|---|
| `journal_mode` | `WAL` | Write-Ahead Logging for concurrent read access |
| `synchronous` | `NORMAL` | Durable on application crash. In WAL mode, transactions may be lost on OS crash or sudden power failure before a checkpoint occurs. |
| `foreign_keys` | `ON` | Enforce relational integrity constraints |

---

## API reference

### `SovereignLedger(db_path=".keys/sovereign_audit.db")`

Opens the SQLite database at `db_path`, applies connection pragmas, and bootstraps
the `forensic_ledger` schema and triggers if they do not already exist.  Pass
`":memory:"` for an ephemeral in-process store.

### `append_receipt(receipt: dict, sieved_content: str) -> str`

Computes the rolling `parent_hash`, inserts a new row, and returns the
`payload_hash` as an opaque receipt identifier.

Raises `sqlite3.IntegrityError` if `receipt["payload_hash"]` is already present
(UNIQUE constraint).

### `verify_ledger_integrity(expected_tip_hash=None) -> bool`

Performs an O(n) cursor sweep, re-deriving the expected `parent_hash` for every row
from its predecessor.  Returns `True` if every entry is intact; `False` on the first
detected breach.

If `expected_tip_hash` is supplied, the sweep additionally asserts that the
`payload_hash` of the final ledger row matches the provided anchor.  This closes the
**tail-truncation blind spot**: without the anchor, an adversary who drops the
`BEFORE DELETE` trigger and removes trailing rows leaves the surviving prefix chain
internally consistent, so the chain sweep alone cannot detect the deletion.

```python
# Capture the tip after the last append.
tip = ledger.append_receipt(receipt, sieved_content)

# Later: verify with the anchor to detect any tail-row deletion.
ok = ledger.verify_ledger_integrity(expected_tip_hash=tip)
```

Returns `False` if the ledger is empty when an anchor is supplied.

### `close()`

Releases all thread-local SQLite connection handles tracked by this instance.  Iterates
every connection registered across all threads and closes each one, ensuring no file
descriptors are leaked regardless of how many producer threads have accessed the ledger.

---

## Threat model

| Attack vector | Defense |
|---|---|
| `UPDATE` via ORM or admin tool | `BEFORE UPDATE` trigger fires `RAISE(ROLLBACK, ...)`, raising `sqlite3.IntegrityError` and aborting the entire enclosing transaction |
| `DELETE` via ORM or admin tool | `BEFORE DELETE` trigger fires `RAISE(ROLLBACK, ...)`, raising `sqlite3.IntegrityError` and aborting the entire enclosing transaction |
| Raw binary file edit (hex editor) | Hash chain breaks; `verify_ledger_integrity()` returns `False` |
| Out-of-band `sieved_content` edit | 8-field NUL-delimited preimage seals textual payload; successor `parent_hash` mismatches |
| Out-of-band `timestamp` edit | 8-field NUL-delimited preimage seals ingestion timestamp; successor `parent_hash` mismatches |
| Out-of-band `raw_token_count` edit | 8-field NUL-delimited preimage seals FinOps token count; successor `parent_hash` mismatches |
| Out-of-band `optimized_token_count` edit | 8-field NUL-delimited preimage seals FinOps token count; successor `parent_hash` mismatches |
| Out-of-band `tax_savings_percentage` edit | 8-field NUL-delimited preimage seals FinOps savings metric; successor `parent_hash` mismatches |
| Field-boundary / length-substitution attack | NUL `\x00` delimiter between every field makes shifted splits produce a distinct preimage byte stream |
| Mid-chain row deletion (trigger dropped) | Successor row's `parent_hash` mismatches; sweep returns `False` |
| Fabricated row injected with wrong parent | Parent pointer diverges from re-derived chain; sweep returns `False` |
| Tail-row deletion (no anchor) | Surviving prefix chain is internally consistent; undetectable by sweep alone — requires external tip-hash anchor |
| Tail-row deletion with `expected_tip_hash` | `last_payload_hash != expected_tip_hash` → `verify_ledger_integrity()` returns `False` |
| Tail-row injection (attacker computes valid parent hash for fabricated row) | Interior chain sweep passes because the appended row is cryptographically consistent; requires `expected_tip_hash` anchor — unauthorized extension detected when last `payload_hash` diverges from the operator-held tip |
| Replay / reorder attack | `AUTOINCREMENT` id sequence + chained parent hash prevents silent reordering |

---

## Integration pattern

`sovereign-ledger` slots into the end of the Sovereign pipeline:

```
[Inbound Payload]
    → sovereign-sieve (Prose Tax reduction)
    → sovereign-core  (Ed25519 sign → ForensicReceipt)
    → sovereign-ledger (append_receipt → immutable audit record)
```

```python
from sovereign_core import SovereignGateway
from sovereign_ledger import SovereignLedger

gateway = SovereignGateway(signing_key=".keys/sovereign_identity.pem")

with SovereignLedger(db_path=".keys/sovereign_audit.db") as ledger:
    response = await gateway.sieve_and_sign(raw_payload)
    ledger.append_receipt(response.receipt, response.content)
```

