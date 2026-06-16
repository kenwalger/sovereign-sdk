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


def _canonical_preimage(
    signature: str,
    payload_hash: str,
    parent_hash: str,
    timestamp: str,
    raw_token_count: int | None,
    optimized_token_count: int | None,
    tax_savings_percentage: int | float | None,
    sieved_content: str,
) -> str:
    # NUL delimiter closes length-substitution field-boundary attacks; naive
    # concatenation allows "AB"+"CDEF" to collide with "ABC"+"DEF".
    #
    # tax_savings_percentage uses fixed-precision :.4f serialisation so that a
    # Python int (e.g. 25, received at append time) and an SQLite REAL extraction
    # (e.g. 25.0, returned at verify time) both produce the same token "25.0000",
    # preventing a divergence that would silently corrupt every subsequent parent_hash.
    return "\x00".join([
        signature,
        payload_hash,
        parent_hash,
        timestamp,
        "NULL" if raw_token_count is None else str(raw_token_count),
        "NULL" if optimized_token_count is None else str(optimized_token_count),
        "NULL" if tax_savings_percentage is None else f"{float(tax_savings_percentage):.4f}",
        sieved_content,
    ])


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
        row's full eight-column canonical preimage, assembled with a NUL byte
        (``\\x00``) field delimiter to close length-substitution boundary
        attacks::

            SHA-256(
                "\\x00".join([
                    prev.signature,
                    prev.payload_hash,
                    prev.parent_hash,
                    prev.timestamp,
                    str(prev.raw_token_count),          # "NULL" when NULL
                    str(prev.optimized_token_count),    # "NULL" when NULL
                    str(prev.tax_savings_percentage),   # "NULL" when NULL
                    prev.sieved_content,
                ])
            )

        Sealing all eight data columns in the delimited preimage means that any
        out-of-band mutation of any stored value — textual payload, ingestion
        timestamp, FinOps telemetry, or cryptographic fields — immediately breaks
        the chain at the point of corruption and is detected by
        :meth:`verify_ledger_integrity`.  The genesis constant is used as the
        parent for the first entry.  Prose Tax token-economy metrics are
        extracted from ``receipt["metadata"]["prose_tax_summary"]`` when present;
        the three metric columns store ``NULL`` when the key is absent.

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

        raw_tokens: int | None = prose_tax.get("raw_token_count")
        optimized_tokens: int | None = prose_tax.get("optimized_token_count")
        savings_pct: float | None = prose_tax.get("tax_savings_percentage")
        payload_hash: str = receipt["payload_hash"]

        committed = False
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            tip = self._conn.execute(
                "SELECT signature, payload_hash, parent_hash, timestamp, "
                "raw_token_count, optimized_token_count, tax_savings_percentage, sieved_content "
                "FROM forensic_ledger ORDER BY id DESC LIMIT 1"
            ).fetchone()

            if tip is None:
                parent_hash = _GENESIS_HASH
            else:
                parent_hash = hashlib.sha256(
                    _canonical_preimage(
                        tip["signature"],
                        tip["payload_hash"],
                        tip["parent_hash"],
                        tip["timestamp"],
                        tip["raw_token_count"],
                        tip["optimized_token_count"],
                        tip["tax_savings_percentage"],
                        tip["sieved_content"],
                    ).encode("utf-8")
                ).hexdigest()

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

        Streams every row in insertion order via an iterator cursor, re-deriving
        the expected ``parent_hash`` for each entry from the same eight-column
        NUL-delimited canonical preimage used at insertion time::

            SHA-256(
                "\\x00".join([
                    row.signature,
                    row.payload_hash,
                    row.parent_hash,
                    row.timestamp,
                    str(row.raw_token_count),          # "NULL" when NULL
                    str(row.optimized_token_count),    # "NULL" when NULL
                    str(row.tax_savings_percentage),   # "NULL" when NULL
                    row.sieved_content,
                ])
            )

        Iterating via the cursor directly rather than ``fetchall()`` keeps memory
        overhead at O(1) regardless of ledger size; no full row-set is ever
        materialised in Python heap.

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
        expected_parent = _GENESIS_HASH
        for row in self._conn.execute(
            "SELECT signature, payload_hash, parent_hash, timestamp, "
            "raw_token_count, optimized_token_count, tax_savings_percentage, sieved_content "
            "FROM forensic_ledger ORDER BY id ASC"
        ):
            if row["parent_hash"] != expected_parent:
                return False
            expected_parent = hashlib.sha256(
                _canonical_preimage(
                    row["signature"],
                    row["payload_hash"],
                    row["parent_hash"],
                    row["timestamp"],
                    row["raw_token_count"],
                    row["optimized_token_count"],
                    row["tax_savings_percentage"],
                    row["sieved_content"],
                ).encode("utf-8")
            ).hexdigest()

        return True

    def close(self) -> None:
        """Release the SQLite connection.

        :return: None
        :rtype: None
        """
        self._conn.close()
