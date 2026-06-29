# sovereign-ledger

**Immutable, local-first, hash-chained SQLite provenance engine for ForensicReceipt Write-Side Custody.**

`sovereign-ledger` is a zero-external-dependency Python package that stores every
`ForensicReceipt` produced by `sovereign-core` in a tamper-evident, append-only SQLite
database. It enforces Write-Side Custody through two complementary mechanisms: engine-level
SQL triggers that abort any `UPDATE` or `DELETE` at the SQLite layer, and a SHA-256 hash
chain that makes out-of-band filesystem tampering mathematically detectable.

---

## Installation

```bash
pip install sovereign-sdk-ledger
```

Requires Python 3.12 or later. Zero runtime dependencies beyond the Python standard library.

---

## Quick start

```python
from sovereign_ledger import SovereignLedger

# Open (or create) the audit database
with SovereignLedger(db_path=".keys/sovereign_audit.db") as ledger:

    # Append a signed ForensicReceipt after a sieve-and-sign pass
    tip = ledger.append_receipt(receipt, sieved_content)

    # Verify the full hash chain at any time
    assert ledger.verify_ledger_integrity()                       # True on an untampered ledger

    # Pin the sweep to a known tip to detect tail-truncation attacks
    assert ledger.verify_ledger_integrity(expected_tip_hash=tip)  # False if last row was deleted
```

---

## API reference

### `SovereignLedger(db_path=".keys/sovereign_audit.db")`

Opens the SQLite database at `db_path`, applies WAL journal mode, `NORMAL` synchronous
enforcement, and strict foreign-key constraints, then bootstraps the `forensic_ledger`
schema and Write-Side Custody triggers if they do not already exist. Pass `":memory:"` for
an ephemeral in-process store.

Supports the Python context manager protocol — use a `with` block to guarantee all
thread-local connection handles are released on exit.

### `append_receipt(receipt: dict, sieved_content: str) -> str`

Derives a rolling SHA-256 `parent_hash` from the immediately preceding row's full
eight-column canonical preimage (or from the static genesis constant for the first entry)
and inserts an immutable row inside a `BEGIN IMMEDIATE` transaction. Returns the
`payload_hash` as an opaque receipt identifier.

Raises `sqlite3.IntegrityError` if `receipt["payload_hash"]` is already present (UNIQUE
constraint). Raises `sqlite3.OperationalError` if the database lock cannot be acquired
within the configured 5-second `busy_timeout`.

### `verify_ledger_integrity(expected_tip_hash=None) -> bool`

Performs an O(n) streaming cursor sweep, re-deriving the expected `parent_hash` for every
row from its predecessor. Returns `True` if every entry is intact; `False` on the first
detected breach.

If `expected_tip_hash` is supplied, the sweep additionally asserts that the `payload_hash`
of the final ledger row matches the provided anchor, closing the tail-truncation blind spot
where an adversary drops the `BEFORE DELETE` trigger and removes trailing rows that leave
the surviving prefix chain internally consistent.

### `close()`

Releases all thread-local SQLite connection handles tracked by this instance. The closed
flag is set atomically inside the connection-registry lock so that any concurrent thread
attempting `_get_conn()` after teardown receives a `SovereignStorageError` rather than
a leaked or dangling connection handle.

### `SovereignStorageError`

`RuntimeError` subclass raised when any ledger operation is attempted on an instance that
has been explicitly closed. Import alongside `SovereignLedger`:

```python
from sovereign_ledger import SovereignLedger, SovereignStorageError
```

---

## Cryptographic invariants

### Write-Side Custody triggers

Two `BEFORE UPDATE` and `BEFORE DELETE` SQL triggers are stored inside the `.db` file
itself:

```sql
CREATE TRIGGER prevent_update_forensic_ledger
BEFORE UPDATE ON forensic_ledger
BEGIN
    SELECT RAISE(ROLLBACK, 'Write-Side Custody violation: UPDATE operations are prohibited on forensic_ledger.');
END;
```

Any client that opens the file — Python code, a desktop SQL browser, a raw
`sqlite3.connect()` call — receives a `sqlite3.IntegrityError` and has its entire
enclosing transaction rolled back at the SQLite engine layer before any mutation lands.

### SHA-256 hash chain

Each row stores a `parent_hash` field that is the SHA-256 digest of the immediately
preceding row's complete eight-column canonical preimage, assembled with a NUL byte
(`\x00`) delimiter between every field:

```
parent_hash[N] = SHA-256(
    "\x00".join([
        row[N-1].signature,
        row[N-1].payload_hash,
        row[N-1].parent_hash,
        row[N-1].timestamp,
        str(row[N-1].raw_token_count),                    # "NULL" when NULL
        str(row[N-1].optimized_token_count),              # "NULL" when NULL
        f"{float(row[N-1].tax_savings_percentage):.4f}",  # "NULL" when NULL
        row[N-1].sieved_content,
    ])
)
```

The NUL delimiter closes length-substitution field-boundary attacks. Fixed-precision
`:.4f` serialisation for `tax_savings_percentage` ensures that a Python `int` supplied at
append time and a SQLite `REAL` extracted at verification time both produce the identical
canonical token, preventing silent chain divergence.

Any out-of-band mutation of any stored value — textual payload, ingestion timestamp,
FinOps telemetry, or cryptographic fields — immediately breaks the chain and is detected
by `verify_ledger_integrity()`.

---

## Thread safety

Each thread that accesses a shared `SovereignLedger` instance receives its own isolated
`sqlite3.Connection` via the internal `_get_conn()` method. `BEGIN IMMEDIATE` and a
5-second `busy_timeout` serialise writes at the SQLite reserved-lock level, preventing
sibling-fork `parent_hash` collisions without requiring a Python-layer mutex.

The closed-state flag is set atomically inside the connection-registry lock during
`close()`, eliminating the race window where a thread could slip past the guard and
register a dangling connection handle after teardown.

---

## Integration pattern

`sovereign-ledger` slots into the end of the Sovereign pipeline:

```
[Inbound Payload]
    → sovereign-sieve  (Prose Tax reduction)
    → sovereign-core   (Ed25519 sign → ForensicReceipt)
    → sovereign-ledger (append_receipt → immutable audit record)
```

```python
from sovereign_core import SovereignGateway
from sovereign_ledger import SovereignLedger

gateway = SovereignGateway(signing_key=".keys/sovereign_identity.pem")

with SovereignLedger(db_path=".keys/sovereign_audit.db") as ledger:
    response = await gateway.sieve_and_sign(raw_payload)
    tip = ledger.append_receipt(response.receipt, response.content)
    assert ledger.verify_ledger_integrity(expected_tip_hash=tip)
```
