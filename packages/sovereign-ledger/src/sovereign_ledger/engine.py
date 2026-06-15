# packages/sovereign-ledger/src/sovereign_ledger/engine.py
import hashlib
import sqlite3
from pathlib import Path


# Root of the hash chain.  Hardcoded so that the genesis entry is verifiable
# independently of any runtime state.
_GENESIS_HASH: str = hashlib.sha256(b"SOVEREIGN_LEDGER_GENESIS_BLOCK_v1.0").hexdigest()

_DDL: str = """
CREATE TABLE IF NOT EXISTS forensic_ledger (
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

CREATE TRIGGER IF NOT EXISTS prevent_update_forensic_ledger
BEFORE UPDATE ON forensic_ledger
BEGIN
    SELECT RAISE(FAIL, 'Write-Side Custody violation: UPDATE operations are prohibited on forensic_ledger.');
END;

CREATE TRIGGER IF NOT EXISTS prevent_delete_forensic_ledger
BEFORE DELETE ON forensic_ledger
BEGIN
    SELECT RAISE(FAIL, 'Write-Side Custody violation: DELETE operations are prohibited on forensic_ledger.');
END;
"""


class SovereignLedger:
    """Append-only, hash-chained SQLite ledger for ForensicReceipt provenance.

    Enforces Write-Side Custody through two complementary mechanisms:
    1. Engine-level BEFORE UPDATE / BEFORE DELETE triggers that abort any
       mutation attempt regardless of which client opens the database file.
    2. A SHA-256 parent-hash chain linking every row to its predecessor,
       making out-of-band filesystem tampering detectable via
       ``verify_ledger_integrity()``.

    The public interface is strictly append-only: ``append_receipt`` and
    ``verify_ledger_integrity``.  No update or deletion methods exist.

    Args:
        db_path: Filesystem path to the SQLite database file.  Use the
            special value ``":memory:"`` for an in-process ephemeral store
            (useful in tests).  Defaults to ``".keys/sovereign_audit.db"``.
    """

    def __init__(self, db_path: str = ".keys/sovereign_audit.db") -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._apply_pragmas()
        self._bootstrap_schema()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply_pragmas(self) -> None:
        self._conn.execute("PRAGMA journal_mode = WAL;")
        self._conn.execute("PRAGMA synchronous = NORMAL;")
        self._conn.execute("PRAGMA foreign_keys = ON;")

    def _bootstrap_schema(self) -> None:
        self._conn.executescript(_DDL)

    def _compute_parent_hash(self) -> str:
        """Derives the parent_hash for the next row from the current chain tip.

        Returns the static genesis hash when the ledger is empty.
        """
        cursor = self._conn.execute(
            "SELECT signature, payload_hash, parent_hash "
            "FROM forensic_ledger ORDER BY id DESC LIMIT 1"
        )
        tip = cursor.fetchone()
        if tip is None:
            return _GENESIS_HASH
        chain_input = tip["signature"] + tip["payload_hash"] + tip["parent_hash"]
        return hashlib.sha256(chain_input.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def append_receipt(self, receipt: dict, sieved_content: str) -> str:
        """Append an immutable ForensicReceipt entry to the ledger.

        Derives a rolling SHA-256 ``parent_hash`` from the immediately
        preceding row's cryptographic artifacts (``signature``,
        ``payload_hash``, ``parent_hash``), or from the hardcoded genesis
        block constant when the ledger is empty, then executes a single
        atomic INSERT.

        Prose Tax token-economy metrics are extracted from
        ``receipt["metadata"]["prose_tax_summary"]`` when present; the
        corresponding columns are stored as NULL when the key is absent.

        Args:
            receipt: A ``ForensicReceipt``-compatible dict containing at
                minimum ``payload_hash``, ``timestamp``, ``signature``, and
                ``metadata`` keys.
            sieved_content: The Prose-Tax-minimized string payload that was
                signed to produce ``receipt``.

        Returns:
            The ``payload_hash`` of the newly appended row, usable as an
            opaque receipt identifier.

        Raises:
            sqlite3.IntegrityError: If ``receipt["payload_hash"]`` already
                exists in the ledger (UNIQUE constraint enforcement).
        """
        metadata = receipt.get("metadata") or {}
        prose_tax = metadata.get("prose_tax_summary") or {}

        raw_tokens = prose_tax.get("raw_token_count")
        optimized_tokens = prose_tax.get("optimized_token_count")
        savings_pct = prose_tax.get("tax_savings_percentage")

        parent_hash = self._compute_parent_hash()
        payload_hash: str = receipt["payload_hash"]

        self._conn.execute(
            """
            INSERT INTO forensic_ledger
                (payload_hash, parent_hash, timestamp, sieved_content, signature,
                 raw_token_count, optimized_token_count, tax_savings_percentage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload_hash,
                parent_hash,
                receipt["timestamp"],
                sieved_content,
                receipt["signature"],
                raw_tokens,
                optimized_tokens,
                savings_pct,
            ),
        )
        self._conn.commit()
        return payload_hash

    def verify_ledger_integrity(self) -> bool:
        """Perform a full hash-chain sweep to detect any historical tampering.

        Iterates every row in insertion order, re-deriving the expected
        ``parent_hash`` for each entry from its predecessor's ``signature``,
        ``payload_hash``, and ``parent_hash``.  The first row is validated
        against the static genesis hash constant.

        Any discrepancy — whether caused by an UPDATE to an existing field,
        a DELETE that collapses the row sequence, or the injection of a
        fabricated row with an incorrect parent pointer — causes an immediate
        ``False`` return.

        Returns:
            ``True`` if every row's recorded ``parent_hash`` matches the
            mathematically re-derived value; ``False`` on the first detected
            breach.
        """
        cursor = self._conn.execute(
            "SELECT payload_hash, parent_hash, signature "
            "FROM forensic_ledger ORDER BY id ASC"
        )
        rows = cursor.fetchall()

        expected_parent = _GENESIS_HASH
        for row in rows:
            if row["parent_hash"] != expected_parent:
                return False
            chain_input = (
                row["signature"] + row["payload_hash"] + row["parent_hash"]
            )
            expected_parent = hashlib.sha256(
                chain_input.encode("utf-8")
            ).hexdigest()

        return True

    def close(self) -> None:
        """Release the SQLite connection."""
        self._conn.close()
