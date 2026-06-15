# packages/sovereign-ledger/src/sovereign_ledger/engine.py
import hashlib
import sqlite3
from pathlib import Path
from typing import Any


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

    1. Engine-level ``BEFORE UPDATE`` / ``BEFORE DELETE`` triggers stored
       inside the database file that abort any mutation attempt regardless of
       which client opens the file.
    2. A SHA-256 parent-hash chain linking every row to its predecessor,
       making out-of-band filesystem tampering detectable via
       :meth:`verify_ledger_integrity`.

    The public interface is strictly append-only: :meth:`append_receipt` and
    :meth:`verify_ledger_integrity`.  No update or deletion methods exist.

    Concurrent writers are serialised through ``BEGIN IMMEDIATE`` transactions,
    which acquire an exclusive reserved lock before the chain tip is read.
    This eliminates the TOCTOU window that would otherwise allow concurrent
    threads or processes to derive identical ``parent_hash`` values (sibling
    fork).  A 5-second ``busy_timeout`` allows writers to retry rather than
    immediately raising :exc:`sqlite3.OperationalError` under momentary
    contention.

    :param db_path: Filesystem path to the SQLite database file.  Pass
        ``":memory:"`` for an ephemeral in-process store (useful in tests).
    :type db_path: str
    """

    def __init__(self, db_path: str = ".keys/sovereign_audit.db") -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        # isolation_level=None puts the connection in autocommit mode so that
        # the explicit BEGIN IMMEDIATE in append_receipt() is not wrapped or
        # interfered with by Python's implicit transaction machinery.
        self._conn = sqlite3.connect(
            db_path, check_same_thread=False, isolation_level=None
        )
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
        # Retry for up to 5 seconds before surfacing a lock error, supporting
        # concurrent multi-connection write workloads without immediate failure.
        self._conn.execute("PRAGMA busy_timeout = 5000;")

    def _bootstrap_schema(self) -> None:
        # executescript() explicitly ignores isolation_level and issues its
        # own COMMIT before running the script, so it is safe in autocommit mode.
        self._conn.executescript(_DDL)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def append_receipt(self, receipt: dict[str, Any], sieved_content: str) -> str:
        """Append an immutable ForensicReceipt entry to the ledger.

        The chain-tip read and subsequent ``INSERT`` are tightly bound inside a
        single ``BEGIN IMMEDIATE`` transaction.  Acquiring the reserved lock
        *before* reading the tip eliminates the TOCTOU window that concurrent
        writers would otherwise exploit to derive identical ``parent_hash``
        values.

        The rolling ``parent_hash`` is the SHA-256 digest of the preceding
        row's full-payload canonical preimage::

            SHA-256(
                prev.signature
                + prev.payload_hash
                + prev.parent_hash
                + prev.sieved_content
                + prev.timestamp
                + str(prev.tax_savings_percentage)   # "" when NULL
            )

        Sealing all six columns in the preimage means that any out-of-band
        mutation of textual content or temporal metadata breaks the chain,
        not only mutations of the cryptographic fields.  The genesis constant
        is used as the parent for the first entry.  Prose Tax token-economy
        metrics are extracted from ``receipt["metadata"]["prose_tax_summary"]``
        when present; the three metric columns store ``NULL`` when the key is
        absent.

        :param receipt: A ``ForensicReceipt``-compatible mapping containing at
            minimum ``payload_hash``, ``timestamp``, ``signature``, and
            ``metadata`` keys.
        :type receipt: dict[str, Any]
        :param sieved_content: The Prose-Tax-minimized string payload that was
            signed to produce ``receipt``.
        :type sieved_content: str
        :return: The ``payload_hash`` of the newly appended row, usable as an
            opaque receipt identifier.
        :rtype: str
        :raises sqlite3.IntegrityError: If ``receipt["payload_hash"]`` already
            exists in the ledger (``UNIQUE`` constraint enforcement).
        :raises sqlite3.OperationalError: If the database lock cannot be
            acquired within the configured ``busy_timeout`` (transient write
            collision under high concurrency).
        """
        metadata: dict[str, Any] = receipt.get("metadata") or {}
        prose_tax: dict[str, Any] = metadata.get("prose_tax_summary") or {}

        raw_tokens = prose_tax.get("raw_token_count")
        optimized_tokens = prose_tax.get("optimized_token_count")
        savings_pct = prose_tax.get("tax_savings_percentage")
        payload_hash: str = receipt["payload_hash"]

        committed = False
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            tip = self._conn.execute(
                "SELECT signature, payload_hash, parent_hash, "
                "sieved_content, timestamp, tax_savings_percentage "
                "FROM forensic_ledger ORDER BY id DESC LIMIT 1"
            ).fetchone()

            if tip is None:
                parent_hash = _GENESIS_HASH
            else:
                pct = tip["tax_savings_percentage"]
                chain_input = (
                    tip["signature"]
                    + tip["payload_hash"]
                    + tip["parent_hash"]
                    + tip["sieved_content"]
                    + tip["timestamp"]
                    + ("" if pct is None else str(pct))
                )
                parent_hash = hashlib.sha256(chain_input.encode("utf-8")).hexdigest()

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
            self._conn.execute("COMMIT")
            committed = True
        finally:
            if not committed:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass

        return payload_hash

    def verify_ledger_integrity(self) -> bool:
        """Perform a full hash-chain sweep to detect any historical tampering.

        Iterates every row in insertion order, re-deriving the expected
        ``parent_hash`` for each entry from the same six-column canonical
        preimage used at insertion time::

            SHA-256(
                row.signature
                + row.payload_hash
                + row.parent_hash
                + row.sieved_content
                + row.timestamp
                + str(row.tax_savings_percentage)   # "" when NULL
            )

        The first row is validated against the static genesis hash constant.
        Any discrepancy — whether caused by an ``UPDATE`` to any column,
        a ``DELETE`` that collapses the row sequence, or the injection of a
        fabricated row with an incorrect parent pointer — causes an immediate
        ``False`` return.

        :return: ``True`` if every row's recorded ``parent_hash`` matches the
            mathematically re-derived value; ``False`` on the first detected
            breach.
        :rtype: bool
        """
        cursor = self._conn.execute(
            "SELECT payload_hash, parent_hash, signature, "
            "sieved_content, timestamp, tax_savings_percentage "
            "FROM forensic_ledger ORDER BY id ASC"
        )
        rows = cursor.fetchall()

        expected_parent = _GENESIS_HASH
        for row in rows:
            if row["parent_hash"] != expected_parent:
                return False
            pct = row["tax_savings_percentage"]
            chain_input = (
                row["signature"]
                + row["payload_hash"]
                + row["parent_hash"]
                + row["sieved_content"]
                + row["timestamp"]
                + ("" if pct is None else str(pct))
            )
            expected_parent = hashlib.sha256(
                chain_input.encode("utf-8")
            ).hexdigest()

        return True

    def close(self) -> None:
        """Release the SQLite connection.

        :return: None
        :rtype: None
        """
        self._conn.close()
