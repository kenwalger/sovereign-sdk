# packages/sovereign-ledger/tests/test_ledger.py
"""Comprehensive test suite for sovereign-ledger.

Invariants verified across every test class:
- SovereignLedger never exposes UPDATE or DELETE paths on forensic_ledger.
- Engine-level triggers block mutation from any SQLite client, not just the
  Python class itself.
- The SHA-256 parent-hash chain is correctly rooted at the genesis constant
  and correctly links each subsequent row to its predecessor.
- verify_ledger_integrity() returns False on any out-of-band row mutation,
  row deletion, or injected row with a fabricated parent pointer.
- All forensic_ledger columns are stored and retrieved without loss.
"""
import hashlib
import sqlite3
import threading

import pytest

from sovereign_ledger import SovereignLedger

# Mirror of the private constant so tests can assert the genesis root value
# independently of the implementation module.
_GENESIS_HASH = hashlib.sha256(b"SOVEREIGN_LEDGER_GENESIS_BLOCK_v1.0").hexdigest()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_receipt(
    payload_hash: str = "abc123",
    signature: str = "sig_abc",
    timestamp: str = "2026-06-15T12:00:00Z",
    raw_tokens: int = 100,
    optimized_tokens: int = 75,
    savings_pct: float = 25.0,
) -> dict:
    return {
        "timestamp": timestamp,
        "payload_hash": payload_hash,
        "public_key": "base64encodedpublickey==",
        "signature": signature,
        "metadata": {
            "prose_tax_summary": {
                "raw_token_count": raw_tokens,
                "optimized_token_count": optimized_tokens,
                "tax_savings_percentage": savings_pct,
            }
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_ledger():
    """In-memory ledger — fast, side-effect free."""
    ledger = SovereignLedger(":memory:")
    yield ledger
    ledger.close()


@pytest.fixture
def file_ledger(tmp_path):
    """File-backed ledger yielding (ledger, db_path) for adversarial tests."""
    db_path = str(tmp_path / "test_sovereign_audit.db")
    ledger = SovereignLedger(db_path)
    yield ledger, db_path
    ledger.close()


# ---------------------------------------------------------------------------
# TestSovereignLedgerInit
# ---------------------------------------------------------------------------
class TestSovereignLedgerInit:
    def test_forensic_ledger_table_exists(self, mem_ledger):
        cur = mem_ledger._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='forensic_ledger'"
        )
        assert cur.fetchone() is not None

    def test_prevent_update_trigger_exists(self, mem_ledger):
        cur = mem_ledger._conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='trigger' AND name='prevent_update_forensic_ledger'"
        )
        assert cur.fetchone() is not None

    def test_prevent_delete_trigger_exists(self, mem_ledger):
        cur = mem_ledger._conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='trigger' AND name='prevent_delete_forensic_ledger'"
        )
        assert cur.fetchone() is not None

    def test_wal_journal_mode_applied_on_file_db(self, file_ledger):
        ledger, _ = file_ledger
        cur = ledger._conn.execute("PRAGMA journal_mode;")
        assert cur.fetchone()[0] == "wal"

    def test_foreign_keys_pragma_applied(self, mem_ledger):
        cur = mem_ledger._conn.execute("PRAGMA foreign_keys;")
        assert cur.fetchone()[0] == 1

    def test_schema_contains_all_required_columns(self, mem_ledger):
        cur = mem_ledger._conn.execute("PRAGMA table_info(forensic_ledger)")
        columns = {row[1] for row in cur.fetchall()}
        expected = {
            "id",
            "payload_hash",
            "parent_hash",
            "timestamp",
            "sieved_content",
            "signature",
            "raw_token_count",
            "optimized_token_count",
            "tax_savings_percentage",
        }
        assert columns == expected

    def test_idempotent_schema_bootstrap(self, tmp_path):
        db_path = str(tmp_path / "idempotent.db")
        l1 = SovereignLedger(db_path)
        l1.append_receipt(_make_receipt("hash_idem_1", "sig_idem_1"), "content")
        l1.close()
        # Re-opening must not raise or corrupt existing data.
        l2 = SovereignLedger(db_path)
        cur = l2._conn.execute("SELECT COUNT(*) FROM forensic_ledger")
        assert cur.fetchone()[0] == 1
        l2.close()


# ---------------------------------------------------------------------------
# TestAppendReceipt
# ---------------------------------------------------------------------------
class TestAppendReceipt:
    def test_returns_payload_hash(self, mem_ledger):
        receipt = _make_receipt("hash_001", "sig_001")
        assert mem_ledger.append_receipt(receipt, "content") == "hash_001"

    def test_stores_timestamp_correctly(self, mem_ledger):
        receipt = _make_receipt("hash_002", "sig_002", timestamp="2026-01-01T08:30:00Z")
        mem_ledger.append_receipt(receipt, "content")
        cur = mem_ledger._conn.execute(
            "SELECT timestamp FROM forensic_ledger WHERE payload_hash = ?", ("hash_002",)
        )
        assert cur.fetchone()[0] == "2026-01-01T08:30:00Z"

    def test_stores_sieved_content_verbatim(self, mem_ledger):
        receipt = _make_receipt("hash_003", "sig_003")
        mem_ledger.append_receipt(receipt, "run the analysis pipeline now")
        cur = mem_ledger._conn.execute(
            "SELECT sieved_content FROM forensic_ledger WHERE payload_hash = ?", ("hash_003",)
        )
        assert cur.fetchone()[0] == "run the analysis pipeline now"

    def test_stores_signature_verbatim(self, mem_ledger):
        receipt = _make_receipt("hash_004", "AAAA/very+long+base64+sig==")
        mem_ledger.append_receipt(receipt, "content")
        cur = mem_ledger._conn.execute(
            "SELECT signature FROM forensic_ledger WHERE payload_hash = ?", ("hash_004",)
        )
        assert cur.fetchone()[0] == "AAAA/very+long+base64+sig=="

    def test_sequential_appends_produce_autoincrement_ids(self, mem_ledger):
        for i in range(3):
            mem_ledger.append_receipt(_make_receipt(f"hash_seq_{i}", f"sig_{i}"), "content")
        cur = mem_ledger._conn.execute("SELECT id FROM forensic_ledger ORDER BY id")
        assert [row[0] for row in cur.fetchall()] == [1, 2, 3]

    def test_prose_tax_metrics_extracted_from_metadata(self, mem_ledger):
        receipt = _make_receipt(
            "hash_005", "sig_005",
            raw_tokens=400, optimized_tokens=300, savings_pct=25.0,
        )
        mem_ledger.append_receipt(receipt, "content")
        cur = mem_ledger._conn.execute(
            "SELECT raw_token_count, optimized_token_count, tax_savings_percentage "
            "FROM forensic_ledger WHERE payload_hash = ?",
            ("hash_005",),
        )
        row = cur.fetchone()
        assert row[0] == 400
        assert row[1] == 300
        assert row[2] == 25.0

    def test_missing_prose_tax_summary_stores_null_metrics(self, mem_ledger):
        receipt = {
            "timestamp": "2026-06-15T12:00:00Z",
            "payload_hash": "hash_006",
            "public_key": "key==",
            "signature": "sig_006",
            "metadata": {},
        }
        mem_ledger.append_receipt(receipt, "content")
        cur = mem_ledger._conn.execute(
            "SELECT raw_token_count, optimized_token_count, tax_savings_percentage "
            "FROM forensic_ledger WHERE payload_hash = ?",
            ("hash_006",),
        )
        row = cur.fetchone()
        assert row[0] is None
        assert row[1] is None
        assert row[2] is None

    def test_absent_metadata_key_stores_null_metrics(self, mem_ledger):
        receipt = {
            "timestamp": "2026-06-15T12:00:00Z",
            "payload_hash": "hash_007",
            "public_key": "key==",
            "signature": "sig_007",
        }
        mem_ledger.append_receipt(receipt, "content")
        cur = mem_ledger._conn.execute(
            "SELECT raw_token_count FROM forensic_ledger WHERE payload_hash = ?",
            ("hash_007",),
        )
        assert cur.fetchone()[0] is None


# ---------------------------------------------------------------------------
# TestHashChain
# ---------------------------------------------------------------------------
class TestHashChain:
    def test_genesis_hash_is_correct_sha256_preimage(self):
        expected = hashlib.sha256(b"SOVEREIGN_LEDGER_GENESIS_BLOCK_v1.0").hexdigest()
        assert _GENESIS_HASH == expected

    def test_first_row_parent_hash_equals_genesis(self, mem_ledger):
        mem_ledger.append_receipt(_make_receipt("hash_A", "sig_A"), "content")
        cur = mem_ledger._conn.execute(
            "SELECT parent_hash FROM forensic_ledger WHERE id = 1"
        )
        assert cur.fetchone()[0] == _GENESIS_HASH

    def test_second_row_parent_chains_from_first_row(self, mem_ledger):
        mem_ledger.append_receipt(_make_receipt("hash_B1", "sig_B1"), "content1")
        mem_ledger.append_receipt(_make_receipt("hash_B2", "sig_B2"), "content2")

        cur = mem_ledger._conn.execute(
            "SELECT signature, payload_hash, parent_hash, "
            "sieved_content, timestamp, tax_savings_percentage "
            "FROM forensic_ledger WHERE id = 1"
        )
        row1 = cur.fetchone()
        pct = row1["tax_savings_percentage"]
        expected = hashlib.sha256(
            (
                row1["signature"]
                + row1["payload_hash"]
                + row1["parent_hash"]
                + row1["sieved_content"]
                + row1["timestamp"]
                + ("" if pct is None else str(pct))
            ).encode()
        ).hexdigest()

        cur2 = mem_ledger._conn.execute(
            "SELECT parent_hash FROM forensic_ledger WHERE id = 2"
        )
        assert cur2.fetchone()[0] == expected

    def test_third_row_parent_chains_from_second_row(self, mem_ledger):
        for i in range(3):
            mem_ledger.append_receipt(_make_receipt(f"hash_C{i}", f"sig_C{i}"), "c")

        cur = mem_ledger._conn.execute(
            "SELECT signature, payload_hash, parent_hash, "
            "sieved_content, timestamp, tax_savings_percentage "
            "FROM forensic_ledger WHERE id = 2"
        )
        row2 = cur.fetchone()
        pct = row2["tax_savings_percentage"]
        expected = hashlib.sha256(
            (
                row2["signature"]
                + row2["payload_hash"]
                + row2["parent_hash"]
                + row2["sieved_content"]
                + row2["timestamp"]
                + ("" if pct is None else str(pct))
            ).encode()
        ).hexdigest()

        cur3 = mem_ledger._conn.execute(
            "SELECT parent_hash FROM forensic_ledger WHERE id = 3"
        )
        assert cur3.fetchone()[0] == expected

    def test_hash_chain_is_deterministic_across_instances(self, tmp_path):
        parent_hashes = []
        for i in range(2):
            db = SovereignLedger(str(tmp_path / f"det_{i}.db"))
            db.append_receipt(
                {
                    "timestamp": "2026-06-15T00:00:00Z",
                    "payload_hash": "deterministic_hash",
                    "public_key": "key==",
                    "signature": "deterministic_sig",
                    "metadata": {},
                },
                "content",
            )
            cur = db._conn.execute(
                "SELECT parent_hash FROM forensic_ledger WHERE id = 1"
            )
            parent_hashes.append(cur.fetchone()[0])
            db.close()
        assert parent_hashes[0] == parent_hashes[1]

    def test_parent_hash_changes_when_signature_changes(self, tmp_path):
        db1 = SovereignLedger(str(tmp_path / "chain_1.db"))
        db2 = SovereignLedger(str(tmp_path / "chain_2.db"))

        db1.append_receipt(_make_receipt("hash_X", "sig_X"), "content")
        db2.append_receipt(_make_receipt("hash_X", "sig_Y"), "content")

        db1.append_receipt(_make_receipt("hash_X2", "sig_X2"), "content")
        db2.append_receipt(_make_receipt("hash_X2", "sig_Y2"), "content")

        cur1 = db1._conn.execute("SELECT parent_hash FROM forensic_ledger WHERE id = 2")
        cur2 = db2._conn.execute("SELECT parent_hash FROM forensic_ledger WHERE id = 2")

        # Different row-1 signatures must yield different parent_hashes for row 2.
        assert cur1.fetchone()[0] != cur2.fetchone()[0]
        db1.close()
        db2.close()


# ---------------------------------------------------------------------------
# TestImmutabilityTriggers  (adversarial)
# ---------------------------------------------------------------------------
class TestImmutabilityTriggers:
    """Adversarial tests confirming that engine-level triggers block all
    mutation attempts against forensic_ledger, whether the attempt originates
    from the ledger's own connection or from an independent raw sqlite3 client.

    SQLite maps RAISE(FAIL, ...) to SQLITE_CONSTRAINT, which Python's sqlite3
    module raises as sqlite3.IntegrityError.  All trigger tests therefore match
    against sqlite3.DatabaseError — the common base class — to remain portable
    across SQLite builds regardless of whether a given version surfaces the
    trigger abort as OperationalError or IntegrityError.
    """

    def test_update_via_ledger_connection_raises(self, mem_ledger):
        mem_ledger.append_receipt(_make_receipt("hash_T1", "sig_T1"), "content")
        with pytest.raises(sqlite3.DatabaseError):
            mem_ledger._conn.execute(
                "UPDATE forensic_ledger SET signature = 'evil' WHERE id = 1"
            )

    def test_delete_via_ledger_connection_raises(self, mem_ledger):
        mem_ledger.append_receipt(_make_receipt("hash_T2", "sig_T2"), "content")
        with pytest.raises(sqlite3.DatabaseError):
            mem_ledger._conn.execute("DELETE FROM forensic_ledger WHERE id = 1")

    def test_update_via_external_connection_raises(self, file_ledger):
        ledger, db_path = file_ledger
        ledger.append_receipt(_make_receipt("hash_T3", "sig_T3"), "content")

        ext = sqlite3.connect(db_path)
        with pytest.raises(sqlite3.DatabaseError):
            ext.execute(
                "UPDATE forensic_ledger SET signature = 'evil' WHERE id = 1"
            )
        ext.close()

    def test_delete_via_external_connection_raises(self, file_ledger):
        ledger, db_path = file_ledger
        ledger.append_receipt(_make_receipt("hash_T4", "sig_T4"), "content")

        ext = sqlite3.connect(db_path)
        with pytest.raises(sqlite3.DatabaseError):
            ext.execute("DELETE FROM forensic_ledger WHERE id = 1")
        ext.close()

    def test_update_error_message_cites_custody_violation(self, mem_ledger):
        mem_ledger.append_receipt(_make_receipt("hash_T5", "sig_T5"), "content")
        try:
            mem_ledger._conn.execute(
                "UPDATE forensic_ledger SET payload_hash = 'modified' WHERE id = 1"
            )
            pytest.fail("Expected a DatabaseError from the Write-Side Custody trigger")
        except sqlite3.DatabaseError as exc:
            assert "Write-Side Custody violation" in str(exc)

    def test_delete_error_message_cites_custody_violation(self, mem_ledger):
        mem_ledger.append_receipt(_make_receipt("hash_T6", "sig_T6"), "content")
        try:
            mem_ledger._conn.execute("DELETE FROM forensic_ledger WHERE id = 1")
            pytest.fail("Expected a DatabaseError from the Write-Side Custody trigger")
        except sqlite3.DatabaseError as exc:
            assert "Write-Side Custody violation" in str(exc)

    def test_append_still_succeeds_after_blocked_update_attempt(self, mem_ledger):
        mem_ledger.append_receipt(_make_receipt("hash_T7", "sig_T7"), "content")
        with pytest.raises(sqlite3.DatabaseError):
            mem_ledger._conn.execute(
                "UPDATE forensic_ledger SET signature = 'evil' WHERE id = 1"
            )
        # The ledger must remain functional for new appends after a blocked attempt.
        result = mem_ledger.append_receipt(_make_receipt("hash_T8", "sig_T8"), "content")
        assert result == "hash_T8"

    def test_ledger_row_count_unchanged_after_blocked_delete(self, mem_ledger):
        for i in range(3):
            mem_ledger.append_receipt(_make_receipt(f"hash_T9{i}", f"sig_T9{i}"), "c")
        with pytest.raises(sqlite3.DatabaseError):
            mem_ledger._conn.execute("DELETE FROM forensic_ledger WHERE id = 2")
        cur = mem_ledger._conn.execute("SELECT COUNT(*) FROM forensic_ledger")
        assert cur.fetchone()[0] == 3


# ---------------------------------------------------------------------------
# TestVerifyLedgerIntegrity
# ---------------------------------------------------------------------------
class TestVerifyLedgerIntegrity:
    def test_empty_ledger_returns_true(self, mem_ledger):
        assert mem_ledger.verify_ledger_integrity() is True

    def test_single_record_returns_true(self, mem_ledger):
        mem_ledger.append_receipt(_make_receipt("hash_V1", "sig_V1"), "content")
        assert mem_ledger.verify_ledger_integrity() is True

    def test_five_chained_records_return_true(self, mem_ledger):
        for i in range(5):
            mem_ledger.append_receipt(
                _make_receipt(f"hash_V{i + 10:03d}", f"sig_V{i + 10:03d}"), f"c{i}"
            )
        assert mem_ledger.verify_ledger_integrity() is True

    def test_ten_chained_records_return_true(self, mem_ledger):
        for i in range(10):
            mem_ledger.append_receipt(
                _make_receipt(f"hash_G{i:03d}", f"sig_G{i:03d}"), "content"
            )
        assert mem_ledger.verify_ledger_integrity() is True

    def test_outofband_signature_tamper_breaks_chain(self, file_ledger):
        """Dropping the trigger via a raw connection and mutating a historical
        signature must cause verify_ledger_integrity to return False because
        the descendant row's parent_hash no longer matches.
        """
        ledger, db_path = file_ledger
        ledger.append_receipt(_make_receipt("hash_OOB1", "sig_OOB1"), "content")
        ledger.append_receipt(_make_receipt("hash_OOB2", "sig_OOB2"), "content")
        assert ledger.verify_ledger_integrity() is True

        raw = sqlite3.connect(db_path)
        raw.execute("DROP TRIGGER IF EXISTS prevent_update_forensic_ledger")
        raw.execute(
            "UPDATE forensic_ledger SET signature = 'tampered_sig' WHERE id = 1"
        )
        raw.commit()
        raw.close()

        assert ledger.verify_ledger_integrity() is False

    def test_outofband_payload_hash_tamper_breaks_integrity(self, file_ledger):
        ledger, db_path = file_ledger
        ledger.append_receipt(_make_receipt("hash_OOB3", "sig_OOB3"), "content")
        ledger.append_receipt(_make_receipt("hash_OOB4", "sig_OOB4"), "content")
        assert ledger.verify_ledger_integrity() is True

        raw = sqlite3.connect(db_path)
        raw.execute("DROP TRIGGER IF EXISTS prevent_update_forensic_ledger")
        raw.execute(
            "UPDATE forensic_ledger SET payload_hash = 'rewritten_hash' WHERE id = 1"
        )
        raw.commit()
        raw.close()

        assert ledger.verify_ledger_integrity() is False

    def test_outofband_parent_hash_injection_detected(self, file_ledger):
        """Overwriting the first row's parent_hash with a fabricated value must
        be caught by the genesis-root anchor on the first iteration.
        """
        ledger, db_path = file_ledger
        ledger.append_receipt(_make_receipt("hash_OOB5", "sig_OOB5"), "content")
        assert ledger.verify_ledger_integrity() is True

        raw = sqlite3.connect(db_path)
        raw.execute("DROP TRIGGER IF EXISTS prevent_update_forensic_ledger")
        raw.execute(
            "UPDATE forensic_ledger SET parent_hash = 'fabricated_genesis_0000' WHERE id = 1"
        )
        raw.commit()
        raw.close()

        assert ledger.verify_ledger_integrity() is False

    def test_outofband_row_deletion_collapses_chain(self, file_ledger):
        """Deleting the middle row from a 3-row chain means row 3's parent_hash
        was derived from row 2 (now absent), so it cannot match the hash derived
        from row 1 during verification.
        """
        ledger, db_path = file_ledger
        for i in range(3):
            ledger.append_receipt(
                _make_receipt(f"hash_DEL{i}", f"sig_DEL{i}"), "content"
            )
        assert ledger.verify_ledger_integrity() is True

        raw = sqlite3.connect(db_path)
        raw.execute("DROP TRIGGER IF EXISTS prevent_delete_forensic_ledger")
        raw.execute("DELETE FROM forensic_ledger WHERE id = 2")
        raw.commit()
        raw.close()

        assert ledger.verify_ledger_integrity() is False

    def test_outofband_injected_row_with_wrong_parent_detected(self, file_ledger):
        """Inserting a fabricated row directly via a raw connection using an
        incorrect parent_hash must be caught during the sweep.
        """
        ledger, db_path = file_ledger
        ledger.append_receipt(_make_receipt("hash_INJ1", "sig_INJ1"), "content")

        raw = sqlite3.connect(db_path)
        raw.execute(
            """
            INSERT INTO forensic_ledger
                (payload_hash, parent_hash, timestamp, sieved_content, signature)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "hash_INJECTED",
                "0000000000000000000000000000000000000000000000000000000000000000",
                "2026-01-01T00:00:00Z",
                "injected content",
                "injected_sig",
            ),
        )
        raw.commit()
        raw.close()

        assert ledger.verify_ledger_integrity() is False

    def test_outofband_content_tamper_breaks_chain(self, file_ledger):
        """Mutating only the sieved_content column of a historical row — leaving
        all hash and signature columns intact — must break the chain because
        sieved_content is now sealed inside the SHA-256 parent_hash preimage.
        """
        ledger, db_path = file_ledger
        ledger.append_receipt(_make_receipt("hash_SC1", "sig_SC1"), "original sieved content")
        ledger.append_receipt(_make_receipt("hash_SC2", "sig_SC2"), "content 2")
        assert ledger.verify_ledger_integrity() is True

        raw = sqlite3.connect(db_path)
        raw.execute("DROP TRIGGER IF EXISTS prevent_update_forensic_ledger")
        raw.execute(
            "UPDATE forensic_ledger SET sieved_content = 'injected replacement text' WHERE id = 1"
        )
        raw.commit()
        raw.close()

        assert ledger.verify_ledger_integrity() is False

    def test_outofband_timestamp_tamper_breaks_chain(self, file_ledger):
        """Mutating only the timestamp column of a historical row — leaving all
        hash and signature columns intact — must break the chain because
        timestamp is now sealed inside the SHA-256 parent_hash preimage.
        """
        ledger, db_path = file_ledger
        ledger.append_receipt(_make_receipt("hash_TS1", "sig_TS1"), "content 1")
        ledger.append_receipt(_make_receipt("hash_TS2", "sig_TS2"), "content 2")
        assert ledger.verify_ledger_integrity() is True

        raw = sqlite3.connect(db_path)
        raw.execute("DROP TRIGGER IF EXISTS prevent_update_forensic_ledger")
        raw.execute(
            "UPDATE forensic_ledger SET timestamp = '1970-01-01T00:00:00Z' WHERE id = 1"
        )
        raw.commit()
        raw.close()

        assert ledger.verify_ledger_integrity() is False

    def test_closed_and_reopened_ledger_verifies_correctly(self, tmp_path):
        db_path = str(tmp_path / "reopen.db")
        ledger = SovereignLedger(db_path)
        for i in range(4):
            ledger.append_receipt(
                _make_receipt(f"hash_RO{i}", f"sig_RO{i}"), "content"
            )
        ledger.close()

        ledger2 = SovereignLedger(db_path)
        assert ledger2.verify_ledger_integrity() is True
        ledger2.close()


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------
class TestEdgeCases:
    def test_duplicate_payload_hash_raises_integrity_error(self, mem_ledger):
        receipt = _make_receipt("hash_DUP", "sig_DUP")
        mem_ledger.append_receipt(receipt, "content")
        with pytest.raises(sqlite3.IntegrityError):
            mem_ledger.append_receipt(receipt, "content_again")

    def test_empty_string_sieved_content_is_stored(self, mem_ledger):
        receipt = _make_receipt("hash_EMPTY", "sig_EMPTY")
        result = mem_ledger.append_receipt(receipt, "")
        cur = mem_ledger._conn.execute(
            "SELECT sieved_content FROM forensic_ledger WHERE payload_hash = ?",
            ("hash_EMPTY",),
        )
        assert result == "hash_EMPTY"
        assert cur.fetchone()[0] == ""

    def test_unicode_sieved_content_round_trips_correctly(self, mem_ledger):
        content = "日本語テスト 🔒 Ünïcödë — 中文 — Αρχαία Ελληνική"
        receipt = _make_receipt("hash_UNI", "sig_UNI")
        mem_ledger.append_receipt(receipt, content)
        cur = mem_ledger._conn.execute(
            "SELECT sieved_content FROM forensic_ledger WHERE payload_hash = ?",
            ("hash_UNI",),
        )
        assert cur.fetchone()[0] == content

    def test_zero_token_counts_stored_as_integers(self, mem_ledger):
        receipt = _make_receipt("hash_ZERO", "sig_ZERO", raw_tokens=0, optimized_tokens=0, savings_pct=0.0)
        mem_ledger.append_receipt(receipt, "content")
        cur = mem_ledger._conn.execute(
            "SELECT raw_token_count, optimized_token_count, tax_savings_percentage "
            "FROM forensic_ledger WHERE payload_hash = ?",
            ("hash_ZERO",),
        )
        row = cur.fetchone()
        assert row[0] == 0
        assert row[1] == 0
        assert row[2] == 0.0

    def test_high_savings_percentage_stored_as_real(self, mem_ledger):
        receipt = _make_receipt("hash_HIGH", "sig_HIGH", raw_tokens=1000, optimized_tokens=1, savings_pct=99.9)
        mem_ledger.append_receipt(receipt, "content")
        cur = mem_ledger._conn.execute(
            "SELECT tax_savings_percentage FROM forensic_ledger WHERE payload_hash = ?",
            ("hash_HIGH",),
        )
        assert abs(cur.fetchone()[0] - 99.9) < 1e-9

    def test_close_then_new_instance_appends_correctly(self, tmp_path):
        db_path = str(tmp_path / "reuse.db")
        l1 = SovereignLedger(db_path)
        l1.append_receipt(_make_receipt("hash_L1", "sig_L1"), "content")
        l1.close()

        l2 = SovereignLedger(db_path)
        result = l2.append_receipt(_make_receipt("hash_L2", "sig_L2"), "content")
        assert result == "hash_L2"
        cur = l2._conn.execute("SELECT COUNT(*) FROM forensic_ledger")
        assert cur.fetchone()[0] == 2
        l2.close()


# ---------------------------------------------------------------------------
# TestConcurrentAppend
# ---------------------------------------------------------------------------
class TestConcurrentAppend:
    """Stress-test the BEGIN IMMEDIATE write serialisation guarantee.

    Each thread opens its own SovereignLedger connection to the same file-backed
    database — the most adversarial realistic concurrent-producer scenario.  A
    threading.Barrier holds all threads at the starting gate until every one is
    ready, maximising lock contention.  After all threads complete, the full
    hash chain must be perfectly linear with no sibling parent_hash forks.
    """

    def test_concurrent_appends_produce_linear_chain(self, tmp_path):
        db_path = str(tmp_path / "concurrent.db")

        # Bootstrap the schema once before any worker spawns.
        primary = SovereignLedger(db_path)
        primary.close()

        N = 8
        errors: list[Exception] = []
        barrier = threading.Barrier(N)

        def append_one(i: int) -> None:
            ledger = SovereignLedger(db_path)
            try:
                # Hold at the barrier so all N threads race simultaneously.
                barrier.wait()
                ledger.append_receipt(
                    _make_receipt(f"hash_CONC_{i:03d}", f"sig_CONC_{i:03d}"),
                    f"content_{i}",
                )
            except Exception as exc:
                errors.append(exc)
            finally:
                ledger.close()

        threads = [threading.Thread(target=append_one, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Unexpected errors during concurrent append: {errors}"

        verifier = SovereignLedger(db_path)
        cur = verifier._conn.execute("SELECT COUNT(*) FROM forensic_ledger")
        assert cur.fetchone()[0] == N
        # BEGIN IMMEDIATE serialises writes — the chain must be perfectly linear.
        assert verifier.verify_ledger_integrity() is True
        verifier.close()
