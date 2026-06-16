# packages/sovereign-ledger/src/sovereign_ledger/engine.py
import hashlib
import sqlite3
import threading
from pathlib import Path
from types import TracebackType
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
    SELECT RAISE(ROLLBACK, 'Write-Side Custody violation: UPDATE operations are prohibited on forensic_ledger.');
END;

CREATE TRIGGER IF NOT EXISTS prevent_delete_forensic_ledger
BEFORE DELETE ON forensic_ledger
BEGIN
    SELECT RAISE(ROLLBACK, 'Write-Side Custody violation: DELETE operations are prohibited on forensic_ledger.');
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

    Supports the Python context manager protocol.  Use a ``with`` block to
    guarantee connection release even when an unhandled exception terminates
    the application ring::

        with SovereignLedger(db_path=".keys/sovereign_audit.db") as ledger:
            ledger.append_receipt(receipt, sieved_content)

    Each thread that accesses a shared ``SovereignLedger`` instance receives
    its own isolated ``sqlite3.Connection`` via :meth:`_get_conn`, eliminating
    cursor and transaction state sharing across threads.  ``BEGIN IMMEDIATE``
    and ``busy_timeout = 5000`` serialise writes at the SQLite reserved-lock
    level, preventing sibling-fork ``parent_hash`` collisions without requiring
    a Python-layer mutex.

    :param db_path: Filesystem path to the SQLite database file.  Pass
        ``":memory:"`` for an ephemeral in-process store (useful in tests).
    :type db_path: str
    """

    def __init__(self, db_path: str = ".keys/sovereign_audit.db") -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._thread_local = threading.local()
        # Tracks every per-thread connection so close() can release all file
        # descriptors regardless of which thread calls it.
        self._connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()
        # Bootstrap the forensic_ledger schema and triggers on the initialising
        # thread's connection.  File-backed databases persist the schema so
        # subsequent thread connections require only pragma application.
        # In-memory databases are bootstrapped per-connection inside _get_conn()
        # because each thread-local handle maps to a distinct empty SQLite store.
        self._bootstrap_schema()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        # Returns the calling thread's dedicated sqlite3.Connection, creating
        # and registering a new one on first access from this thread.
        conn: sqlite3.Connection | None = getattr(self._thread_local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self._db_path, check_same_thread=False, isolation_level=None
            )
            conn.row_factory = sqlite3.Row
            self._apply_pragmas(conn)
            if self._db_path == ":memory:":
                # Each in-memory connection is an independent empty SQLite store;
                # the schema written by _bootstrap_schema() on the initialising
                # thread does not carry over.  Hydrate every new thread-local
                # handle immediately so workers never hit "no such table".
                conn.executescript(_DDL)
            self._thread_local.conn = conn
            with self._connections_lock:
                self._connections.append(conn)
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        # Convenience accessor; always returns the calling thread's isolated handle.
        return self._get_conn()

    def _apply_pragmas(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        # Retry for up to 5 seconds before surfacing a lock error, supporting
        # concurrent multi-connection write workloads without immediate failure.
        conn.execute("PRAGMA busy_timeout = 5000;")

    def _bootstrap_schema(self) -> None:
        # executescript() explicitly ignores isolation_level and issues its
        # own COMMIT before running the script, so it is safe in autocommit mode.
        self._get_conn().executescript(_DDL)

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
                    str(prev.raw_token_count),                    # "NULL" when NULL
                    str(prev.optimized_token_count),              # "NULL" when NULL
                    f"{float(prev.tax_savings_percentage):.4f}",  # "NULL" when NULL
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
        metadata: dict[str, Any] = receipt.get("metadata") or {}  # :type: Any — caller-defined JSON sub-object; field keys are producer-specific and cannot be statically narrowed at the ledger boundary.
        prose_tax: dict[str, Any] = metadata.get("prose_tax_summary") or {}  # :type: Any — optional telemetry bag with no enforced schema; field presence varies per producing gateway.

        raw_tokens: int | None = prose_tax.get("raw_token_count")
        optimized_tokens: int | None = prose_tax.get("optimized_token_count")
        savings_pct: int | float | None = prose_tax.get("tax_savings_percentage")
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

    def verify_ledger_integrity(
        self, expected_tip_hash: str | None = None
    ) -> bool:
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
                    str(row.raw_token_count),                    # "NULL" when NULL
                    str(row.optimized_token_count),              # "NULL" when NULL
                    f"{float(row.tax_savings_percentage):.4f}",  # "NULL" when NULL
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

        If ``expected_tip_hash`` is supplied, the sweep additionally asserts
        that the ``payload_hash`` of the final ledger row matches the provided
        anchor.  This closes the tail-truncation blind spot: without the anchor,
        an adversary who drops the ``BEFORE DELETE`` trigger and removes one or
        more trailing rows leaves the remaining prefix chain internally
        consistent, so the chain sweep alone cannot detect the deletion.  Pass
        the value returned by the last :meth:`append_receipt` call as the
        anchor.  Returns ``False`` if the ledger is empty when an anchor is
        supplied.

        :param expected_tip_hash: The ``payload_hash`` of the expected last row,
            used as an external anchor to detect tail-truncation attacks.  Pass
            ``None`` (default) to skip the anchor check.
        :type expected_tip_hash: str | None
        :return: ``True`` if every row's recorded ``parent_hash`` matches the
            mathematically re-derived value and the optional tip anchor matches;
            ``False`` on the first detected breach.
        :rtype: bool
        """
        expected_parent = _GENESIS_HASH
        last_payload_hash: str | None = None
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
            last_payload_hash = row["payload_hash"]

        if expected_tip_hash is not None:
            if last_payload_hash is None or last_payload_hash != expected_tip_hash:
                return False

        return True

    def close(self) -> None:
        """Release all thread-local SQLite connection handles tracked by this instance.

        Iterates every connection registered across all threads and closes each
        one, ensuring no file descriptors are leaked regardless of how many
        producer threads have accessed the ledger.  Called automatically by
        :meth:`__exit__` when the instance is used as a context manager.

        :return: None
        :rtype: None
        """
        with self._connections_lock:
            for conn in self._connections:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._connections.clear()

    def __enter__(self) -> "SovereignLedger":
        """Enter the runtime context, returning the ledger instance.

        :return: The ``SovereignLedger`` instance itself, bound by the ``with``
            statement target.
        :rtype: SovereignLedger
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the runtime context, releasing all SQLite connections.

        Called automatically at the close of a ``with`` block regardless of
        whether an exception was raised, ensuring file descriptors are never
        leaked in long-running production server lifecycles.  Exceptions are
        not suppressed; they propagate normally after the connections are closed.

        :param exc_type: Exception class raised inside the ``with`` block, or
            ``None`` if the block exited cleanly.
        :type exc_type: type[BaseException] | None
        :param exc_val: Exception instance, or ``None``.
        :type exc_val: BaseException | None
        :param exc_tb: Traceback object, or ``None``.
        :type exc_tb: TracebackType | None
        :return: None (exceptions are not suppressed).
        :rtype: None
        """
        self.close()
