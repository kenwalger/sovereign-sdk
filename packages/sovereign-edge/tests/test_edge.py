# packages/sovereign-edge/tests/test_edge.py
"""Desktop validation suite for sovereign-edge.

Invariants verified across every test:
- SensorFrame.from_bytes correctly deserializes all seven sovereign-sensor wire
  frame keys into a typed dataclass with no mutation.
- SensorFrame.text_content() produces deterministic, sort-keyed JSON regardless
  of payload insertion order.
- OffGridBuffer.push() enqueues entries for asynchronous JSONL persistence and
  returns immediately; size reflects in-flight entries before disk commit.
- OffGridBuffer.flush() blocks until all enqueued entries are committed to disk.
- OffGridBuffer.drain() flushes, returns entries sorted by sequence, and atomically
  clears the buffer file so no entry is replayed unless explicitly re-queued.
- OffGridBuffer.size reflects the live entry count (on-disk + in-flight) without
  modifying the buffer.
- EdgePipeline.process() deserializes the wire frame, sieves the observation
  payload, mints a valid ForensicReceipt, commits to the ledger on the happy
  path, and returns EdgeResult with buffered=False.
- EdgePipeline.process() catches SovereignStorageError and sqlite3.Error when
  the ledger is unreachable, writes to the off-grid buffer, and returns
  EdgeResult with buffered=True.
- EdgePipeline.process() falls back to raw text_content() and marks
  sieve_fault=True in receipt metadata when sieve_with_metrics raises.
- EdgePipeline.drain_buffer() commits all buffered receipts to an open ledger
  and returns their payload_hash strings.
- EdgePipeline.drain_buffer() re-queues entries that still cannot reach the
  ledger, leaving buffer_depth unchanged.
- Ledger integrity is preserved after a full process→buffer→drain cycle.
"""
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

os.environ.setdefault("SOVEREIGN_NODE_SECRET", "sovereign-edge-test-passphrase-v1")

from sovereign_core.crypto import SovereignKeyManager
from sovereign_ledger import SovereignLedger, SovereignStorageError
from sovereign_sensor import bootstrap_sensor_node
from sovereign_sensor.drivers.software_fallback import SoftwareFallbackDriver

from sovereign_edge import (
    EdgePipeline,
    EdgeResult,
    OffGridBuffer,
    SensorFrame,
    SovereignConfigurationError,
    SovereignDoubleFaultError,
)

_NODE_ID: str = "edge-test-node-001"
_TIMESTAMP: str = "2026-06-19T00:00:00Z"
_PAYLOAD: dict[str, Any] = {"sensor": "temperature", "unit": "C", "value": 23}
_KEY_PATH: str = SoftwareFallbackDriver.MOCK_KEY_SENTINEL
_SENSOR_SECRET: bytes = SoftwareFallbackDriver._MOCK_KEY


def _seal_frame(tmp_path: Path, payload: dict[str, Any] | None = None) -> bytes:
    """Seal a sensor wire frame using the software fallback driver and mock key."""
    envelope = bootstrap_sensor_node(
        _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
    )
    return envelope.seal(_TIMESTAMP, payload or _PAYLOAD)


# ---------------------------------------------------------------------------
# TestModuleImport
# ---------------------------------------------------------------------------

class TestModuleImport:
    """Verify that the sovereign_edge package imports without error at the top level."""

    def test_sovereign_edge_top_level_import(self) -> None:
        """Importing EdgePipeline and SensorFrame must not raise any exception.

        Guards against annotation evaluation regressions — e.g. ``MappingProxyType``
        subscripting that fails at class-definition time in older Python runtimes —
        that would surface as :exc:`ImportError` or :exc:`TypeError` at module load,
        silently blocking the entire test suite from collecting.

        :return: None
        :rtype: None
        """
        from sovereign_edge import EdgePipeline, SensorFrame as _SensorFrame  # noqa: F401
        assert EdgePipeline is not None, "EdgePipeline must be importable from sovereign_edge"
        assert _SensorFrame is not None, "SensorFrame must be importable from sovereign_edge"


# ---------------------------------------------------------------------------
# TestSensorFrame
# ---------------------------------------------------------------------------

class TestSensorFrame:
    """Verify deserialization and text_content() semantics."""

    def test_from_bytes_returns_sensor_frame_instance(self, tmp_path: Path) -> None:
        """from_bytes must return a SensorFrame for a valid sealed wire frame."""
        frame = SensorFrame.from_bytes(_seal_frame(tmp_path))
        assert isinstance(frame, SensorFrame)

    def test_from_bytes_preserves_node_id(self, tmp_path: Path) -> None:
        """Deserialized 'n' field must match the originating node identifier."""
        frame = SensorFrame.from_bytes(_seal_frame(tmp_path))
        assert frame.n == _NODE_ID

    def test_from_bytes_preserves_timestamp(self, tmp_path: Path) -> None:
        """Deserialized 't' field must equal the timestamp supplied to seal()."""
        frame = SensorFrame.from_bytes(_seal_frame(tmp_path))
        assert frame.t == _TIMESTAMP

    def test_from_bytes_preserves_payload(self, tmp_path: Path) -> None:
        """Deserialized 'd' field must equal the original observation dict."""
        frame = SensorFrame.from_bytes(_seal_frame(tmp_path))
        assert frame.d == _PAYLOAD

    def test_from_bytes_sequence_is_integer(self, tmp_path: Path) -> None:
        """'q' field must deserialize as an integer, not a string."""
        frame = SensorFrame.from_bytes(_seal_frame(tmp_path))
        assert isinstance(frame.q, int)
        assert frame.q >= 1

    def test_from_bytes_algorithm_field_is_hmac_sha256(self, tmp_path: Path) -> None:
        """Software fallback driver must declare 'hmac-sha256' in the 'alg' field."""
        frame = SensorFrame.from_bytes(_seal_frame(tmp_path))
        assert frame.alg == "hmac-sha256"

    def test_from_bytes_signature_is_non_empty_string(self, tmp_path: Path) -> None:
        """'s' field must be a non-empty string after deserialization."""
        frame = SensorFrame.from_bytes(_seal_frame(tmp_path))
        assert isinstance(frame.s, str)
        assert len(frame.s) > 0

    def test_text_content_returns_valid_json(self, tmp_path: Path) -> None:
        """text_content() must produce a parseable JSON string."""
        frame = SensorFrame.from_bytes(_seal_frame(tmp_path))
        parsed = json.loads(frame.text_content())
        assert isinstance(parsed, dict)

    def test_text_content_is_sort_keyed(self, tmp_path: Path) -> None:
        """text_content() must produce alphabetically sorted keys regardless of 'd' insertion order."""
        payload_ab: dict[str, Any] = {"a": 1, "z": 99, "m": "mid"}
        payload_reversed: dict[str, Any] = {"z": 99, "m": "mid", "a": 1}

        envelope = bootstrap_sensor_node(
            _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
        )
        frame_ab = SensorFrame.from_bytes(envelope.seal(_TIMESTAMP, payload_ab))

        envelope_b = bootstrap_sensor_node(
            _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence_b")
        )
        frame_rev = SensorFrame.from_bytes(envelope_b.seal(_TIMESTAMP, payload_reversed))

        assert frame_ab.text_content() == frame_rev.text_content()

    def test_from_bytes_raises_on_invalid_json(self) -> None:
        """from_bytes must raise json.JSONDecodeError for non-JSON input."""
        import json as _json
        with pytest.raises(_json.JSONDecodeError):
            SensorFrame.from_bytes(b"not-json")

    def test_from_bytes_raises_on_missing_mandatory_key(self) -> None:
        """from_bytes must raise KeyError when any of the seven mandatory keys is absent."""
        incomplete = json.dumps({"v": 1, "n": "node", "t": "2026"}).encode("utf-8")
        with pytest.raises(KeyError):
            SensorFrame.from_bytes(incomplete)

    def test_from_bytes_raises_on_unsupported_version(self) -> None:
        """from_bytes must raise ValueError when the wire frame declares a protocol
        version other than 1; future or unknown versions are rejected immediately
        to prevent silent misinterpretation of structurally incompatible envelopes."""
        payload: bytes = json.dumps({
            "v": 2, "n": "node", "t": "2026-06-23T00:00:00Z", "q": 1,
            "alg": "hmac-sha256", "d": {}, "s": "aabbcc",
        }).encode("utf-8")
        with pytest.raises(ValueError, match="Unsupported wire format version"):
            SensorFrame.from_bytes(payload)

    def test_from_bytes_raises_on_wrong_field_type(self) -> None:
        """from_bytes must raise TypeError when a critical field carries the wrong
        runtime type; a string value for the integer 'q' field must be blocked at
        the deserialization boundary before the frame reaches the pipeline."""
        payload: bytes = json.dumps({
            "v": 1, "n": "node", "t": "2026-06-23T00:00:00Z", "q": "not-an-int",
            "alg": "hmac-sha256", "d": {}, "s": "aabbcc",
        }).encode("utf-8")
        with pytest.raises(TypeError):
            SensorFrame.from_bytes(payload)

    def test_sensor_frame_is_immutable(self, tmp_path: Path) -> None:
        """@dataclass(frozen=True) must prevent post-construction field assignment;
        SensorFrame instances are wire protocol values and must not be mutated
        after deserialization."""
        import dataclasses
        frame = SensorFrame.from_bytes(_seal_frame(tmp_path))
        with pytest.raises(dataclasses.FrozenInstanceError):
            frame.n = "mutated-node-id"  # type: ignore[misc]

    def test_from_bytes_d_field_is_immutable_mapping(self, tmp_path: Path) -> None:
        """SensorFrame.d must be a read-only MappingProxyType; in-place key
        assignment must raise TypeError, closing the shallow-freeze bypass where
        a mutable dict reference inside a frozen dataclass could be mutated
        in-place without triggering FrozenInstanceError."""
        frame = SensorFrame.from_bytes(_seal_frame(tmp_path))
        with pytest.raises(TypeError):
            frame.d["injected_key"] = "malicious_value"  # type: ignore[index]

    def test_text_content_preserves_float_literal_format(self) -> None:
        """text_content() must preserve float values exactly as received so that the
        edge-side HMAC preimage is byte-identical to the string the sensor signed.
        Coercing float(1.0) to int 1 produces a preimage mismatch for any sensor
        that serialized the payload field as a floating-point literal."""
        raw_float: bytes = json.dumps({
            "v": 1, "n": _NODE_ID, "t": _TIMESTAMP, "q": 1,
            "alg": "hmac-sha256", "d": {"value": 1.0}, "s": "a" * 64,
        }).encode()
        raw_int: bytes = json.dumps({
            "v": 1, "n": _NODE_ID, "t": _TIMESTAMP, "q": 1,
            "alg": "hmac-sha256", "d": {"value": 1}, "s": "a" * 64,
        }).encode()
        frame_float = SensorFrame.from_bytes(raw_float)
        frame_int = SensorFrame.from_bytes(raw_int)
        assert "1.0" in frame_float.text_content(), (
            "float(1.0) must appear as '1.0' in text_content() to preserve "
            "the exact preimage the sensor signed"
        )
        assert frame_float.text_content() != frame_int.text_content(), (
            "text_content() must not coerce float(1.0) to int 1; distinct wire "
            "representations must produce distinct preimage strings"
        )


# ---------------------------------------------------------------------------
# TestOffGridBuffer
# ---------------------------------------------------------------------------

class TestOffGridBuffer:
    """Verify push, drain, and size semantics of the durable JSONL buffer."""

    def _make_receipt(self, tag: str = "test") -> dict[str, Any]:
        return {
            "timestamp": _TIMESTAMP,
            "payload_hash": f"hash_{tag}",
            "public_key": "base64key==",
            "signature": f"sig_{tag}",
            "metadata": {},
        }

    def test_size_is_zero_when_buffer_absent(self, tmp_path: Path) -> None:
        """size must return 0 when the buffer file has not yet been created."""
        buf = OffGridBuffer(str(tmp_path / "nonexistent.jsonl"))
        try:
            assert buf.size == 0
        finally:
            buf.close()

    def test_drain_returns_empty_list_when_buffer_absent(self, tmp_path: Path) -> None:
        """drain() must return an empty list when the buffer file does not exist."""
        buf = OffGridBuffer(str(tmp_path / "nonexistent.jsonl"))
        try:
            assert buf.drain() == []
        finally:
            buf.close()

    def test_push_increments_size(self, tmp_path: Path) -> None:
        """size must reflect the number of entries written via push()."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        try:
            buf.push(self._make_receipt("A"), "content A")
            assert buf.size == 1
            buf.push(self._make_receipt("B"), "content B")
            assert buf.size == 2
        finally:
            buf.close()

    def test_drain_returns_all_entries(self, tmp_path: Path) -> None:
        """drain() must return every (receipt, sieved_content) pair previously pushed."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        try:
            buf.push(self._make_receipt("X"), "payload X")
            buf.push(self._make_receipt("Y"), "payload Y")
            entries = buf.drain()
            assert len(entries) == 2
        finally:
            buf.close()

    def test_drain_returns_entries_in_fifo_order(self, tmp_path: Path) -> None:
        """drain() must preserve push() insertion order."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        try:
            buf.push(self._make_receipt("first"), "first content")
            buf.push(self._make_receipt("second"), "second content")
            entries = buf.drain()
            assert entries[0][0]["payload_hash"] == "hash_first"
            assert entries[1][0]["payload_hash"] == "hash_second"
        finally:
            buf.close()

    def test_drain_clears_buffer_after_read(self, tmp_path: Path) -> None:
        """size must be 0 after drain() empties the buffer."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        try:
            buf.push(self._make_receipt("Z"), "content Z")
            buf.drain()
            assert buf.size == 0
        finally:
            buf.close()

    def test_drain_preserves_sieved_content_verbatim(self, tmp_path: Path) -> None:
        """drain() must return the exact sieved_content string supplied to push()."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        try:
            content = "temperature reading: 42°C — no filler here"
            buf.push(self._make_receipt("V"), content)
            entries = buf.drain()
            assert entries[0][1] == content
        finally:
            buf.close()

    def test_push_after_drain_works(self, tmp_path: Path) -> None:
        """push() must succeed after drain() has cleared the buffer."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        try:
            buf.push(self._make_receipt("A"), "content A")
            buf.drain()
            buf.push(self._make_receipt("B"), "content B")
            assert buf.size == 1
            entries = buf.drain()
            assert entries[0][0]["payload_hash"] == "hash_B"
        finally:
            buf.close()

    def test_drain_skips_malformed_json_lines(self, tmp_path: Path) -> None:
        """drain() must quarantine corrupt lines in dead_letter and return only valid entries."""
        buf_path = tmp_path / "buf.jsonl"
        buf = OffGridBuffer(str(buf_path))
        try:
            buf.push(self._make_receipt("good"), "good content")
            buf.flush()  # ensure background writer closes its file handle before we append
            with open(buf_path, "a", encoding="utf-8") as fh:
                fh.write("{corrupt-json\n")
            entries = buf.drain()
            assert len(entries) == 1
            assert entries[0][0]["payload_hash"] == "hash_good"
        finally:
            buf.close()

    def test_drain_size_zero_after_committed_line_corrupted_on_disk(
        self, tmp_path: Path
    ) -> None:
        """size must reach exactly 0 after drain() when a fsync'd line is subsequently
        corrupted on disk.  The _committed decrement must cover total disk line count
        (valid + dead-letter), not only successfully parsed lines; otherwise _committed
        stays at 1 for an empty file, producing permanent counter drift."""
        buf_path = tmp_path / "buf.jsonl"
        buf = OffGridBuffer(str(buf_path))
        try:
            buf.push(self._make_receipt("good"), "good content")
            buf.flush()
            assert buf.size == 1  # _committed == 1 after successful fsync
            buf_path.write_text("{corrupted-line\n", encoding="utf-8")
            entries = buf.drain()
            assert entries == []
            assert buf.size == 0
            assert buf.dead_letter_count == 1
        finally:
            buf.close()

    def test_recover_staging_quarantines_corrupt_file(self, tmp_path: Path) -> None:
        """OffGridBuffer.__init__ must raise SovereignStorageError and quarantine the
        staging file as *.staging.corrupt when the file contains bytes that cannot be
        decoded as UTF-8.  Silently skipping a corrupt staging read would allow an
        undetected data-loss condition to masquerade as a clean boot and leave staged
        entries permanently invisible to every subsequent drain pass.

        :param tmp_path: Pytest-provided isolated temporary directory.
        :type tmp_path: Path
        """
        buf_path: Path = tmp_path / "buf.jsonl"
        staging_path: Path = Path(str(buf_path) + ".staging")
        corrupt_path: Path = Path(str(staging_path) + ".corrupt")

        staging_path.write_bytes(b"\xff\xfe invalid utf-8 \x80\x81")

        with pytest.raises(SovereignStorageError):
            OffGridBuffer(str(buf_path))

        assert corrupt_path.exists(), (
            "staging file must be quarantined as .staging.corrupt when reading fails; "
            "a missing quarantine file indicates the error was silently suppressed"
        )
        assert not staging_path.exists(), (
            "original staging file must be renamed to .staging.corrupt, not left at "
            "the original path where it could be re-encountered on the next boot attempt"
        )

    def test_init_cleans_up_lock_on_staging_recovery_failure(self, tmp_path: Path) -> None:
        """OffGridBuffer.__init__ must delete the .lock file when _recover_staging()
        raises SovereignStorageError so that a subsequent construction attempt on the
        same path can acquire the lock and succeed.  Without this cleanup the .lock file
        is left on disk holding the current PID; every future construction attempt reads
        a live PID, determines the process is still running, and raises RuntimeError
        rather than recovering — permanently locking the buffer path for the lifetime of
        the process that failed at init.

        :param tmp_path: Pytest-provided isolated temporary directory.
        :type tmp_path: Path
        """
        buf_path: Path = tmp_path / "buf.jsonl"
        staging_path: Path = Path(str(buf_path) + ".staging")
        lock_path: Path = Path(str(buf_path) + ".lock")
        corrupt_path: Path = Path(str(staging_path) + ".corrupt")

        staging_path.write_bytes(b"\xff\xfe invalid utf-8 \x80\x81")

        with pytest.raises(SovereignStorageError):
            OffGridBuffer(str(buf_path))

        assert not lock_path.exists(), (
            ".lock file must be deleted when __init__ raises; an orphaned lock prevents "
            "every subsequent construction attempt from acquiring the lock"
        )
        assert corrupt_path.exists(), "staging file must be quarantined as .staging.corrupt"

        # With the lock gone and .staging absent (quarantined to .staging.corrupt), a
        # second construction on the same path must succeed cleanly.
        corrupt_path.unlink()
        buf: OffGridBuffer = OffGridBuffer(str(buf_path))
        try:
            assert buf.size == 0
        finally:
            buf.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_ledger() -> SovereignLedger:
    """In-memory ledger — fast, side-effect free."""
    ledger = SovereignLedger(":memory:")
    yield ledger
    ledger.close()


@pytest.fixture
def edge_pipeline(mem_ledger: SovereignLedger, tmp_path: Path) -> EdgePipeline:
    """EdgePipeline wired to an in-memory ledger with isolated key and buffer paths.

    Yields the pipeline and closes it in teardown so the background buffer writer
    thread is always joined before the test process continues, preventing thread
    leakage across the suite.  The mem_ledger fixture is torn down after this one
    (pytest respects dependency order), so drain_buffer() can safely attempt ledger
    commits during the close() pass.
    """
    pipeline = EdgePipeline(
        ledger=mem_ledger,
        signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
        buffer_path=str(tmp_path / ".edge_buffer.jsonl"),
        sensor_secret=_SENSOR_SECRET,
    )
    yield pipeline
    pipeline.close()


# ---------------------------------------------------------------------------
# TestEdgePipelineProcess
# ---------------------------------------------------------------------------

class TestEdgePipelineProcess:
    """Verify happy-path ingestion: deserialize → sieve → sign → ledger commit."""

    def test_process_returns_edge_result(
        self, edge_pipeline: EdgePipeline, tmp_path: Path
    ) -> None:
        """process() must return an EdgeResult instance."""
        result = edge_pipeline.process(_seal_frame(tmp_path))
        assert isinstance(result, EdgeResult)

    def test_process_happy_path_is_not_buffered(
        self, edge_pipeline: EdgePipeline, tmp_path: Path
    ) -> None:
        """process() with an open ledger must set buffered=False."""
        result = edge_pipeline.process(_seal_frame(tmp_path))
        assert result.buffered is False

    def test_process_returns_non_empty_payload_hash(
        self, edge_pipeline: EdgePipeline, tmp_path: Path
    ) -> None:
        """payload_hash in EdgeResult must be a non-empty hex string."""
        result = edge_pipeline.process(_seal_frame(tmp_path))
        assert isinstance(result.payload_hash, str)
        assert len(result.payload_hash) == 64

    def test_process_receipt_contains_mandatory_forensic_keys(
        self, edge_pipeline: EdgePipeline, tmp_path: Path
    ) -> None:
        """The minted ForensicReceipt must contain all five mandatory keys."""
        result = edge_pipeline.process(_seal_frame(tmp_path))
        assert set(result.receipt.keys()) >= {
            "timestamp", "payload_hash", "public_key", "signature", "metadata"
        }

    def test_process_receipt_metadata_source_is_sovereign_edge(
        self, edge_pipeline: EdgePipeline, tmp_path: Path
    ) -> None:
        """Receipt metadata must carry 'source': 'SovereignEdge'."""
        result = edge_pipeline.process(_seal_frame(tmp_path))
        assert result.receipt["metadata"]["source"] == "SovereignEdge"

    def test_process_receipt_metadata_contains_sensor_node(
        self, edge_pipeline: EdgePipeline, tmp_path: Path
    ) -> None:
        """Receipt metadata must embed the originating sensor node identifier."""
        result = edge_pipeline.process(_seal_frame(tmp_path))
        assert result.receipt["metadata"]["sensor_node"] == _NODE_ID

    def test_process_receipt_metadata_contains_prose_tax_summary(
        self, edge_pipeline: EdgePipeline, tmp_path: Path
    ) -> None:
        """Receipt metadata must carry a prose_tax_summary sub-dict with four keys."""
        result = edge_pipeline.process(_seal_frame(tmp_path))
        summary = result.receipt["metadata"]["prose_tax_summary"]
        assert set(summary.keys()) >= {
            "raw_token_count", "optimized_token_count",
            "tax_savings_percentage", "tokens_eliminated",
        }

    def test_process_sieved_content_is_string(
        self, edge_pipeline: EdgePipeline, tmp_path: Path
    ) -> None:
        """sieved_content in EdgeResult must be a string."""
        result = edge_pipeline.process(_seal_frame(tmp_path))
        assert isinstance(result.sieved_content, str)

    def test_process_raw_token_count_is_non_negative(
        self, edge_pipeline: EdgePipeline, tmp_path: Path
    ) -> None:
        """raw_token_count must be a non-negative integer."""
        result = edge_pipeline.process(_seal_frame(tmp_path))
        assert isinstance(result.raw_token_count, int)
        assert result.raw_token_count >= 0

    def test_process_optimized_token_count_does_not_exceed_raw(
        self, edge_pipeline: EdgePipeline, tmp_path: Path
    ) -> None:
        """optimized_token_count must be at most raw_token_count."""
        result = edge_pipeline.process(_seal_frame(tmp_path))
        assert result.optimized_token_count <= result.raw_token_count

    def test_process_commits_receipt_to_ledger(
        self, edge_pipeline: EdgePipeline, mem_ledger: SovereignLedger, tmp_path: Path
    ) -> None:
        """process() must insert exactly one row into the ledger on the happy path."""
        result = edge_pipeline.process(_seal_frame(tmp_path))
        assert result.buffered is False
        assert mem_ledger.verify_ledger_integrity(expected_tip_hash=result.payload_hash) is True

    def test_process_ledger_row_matches_result_payload_hash(
        self, edge_pipeline: EdgePipeline, mem_ledger: SovereignLedger, tmp_path: Path
    ) -> None:
        """The payload_hash stored in the ledger must match EdgeResult.payload_hash."""
        result = edge_pipeline.process(_seal_frame(tmp_path))
        assert mem_ledger.verify_ledger_integrity(expected_tip_hash=result.payload_hash) is True

    def test_process_ledger_integrity_holds_after_single_frame(
        self, edge_pipeline: EdgePipeline, mem_ledger: SovereignLedger, tmp_path: Path
    ) -> None:
        """Ledger hash chain must verify cleanly after one process() call."""
        edge_pipeline.process(_seal_frame(tmp_path))
        assert mem_ledger.verify_ledger_integrity() is True

    def test_process_ledger_integrity_holds_after_three_frames(
        self, edge_pipeline: EdgePipeline, mem_ledger: SovereignLedger, tmp_path: Path
    ) -> None:
        """Ledger hash chain must remain linear after three sequential process() calls."""
        for _ in range(3):
            edge_pipeline.process(_seal_frame(tmp_path))
        assert mem_ledger.verify_ledger_integrity() is True

    def test_process_receipt_metadata_contains_sequence(
        self, edge_pipeline: EdgePipeline, tmp_path: Path
    ) -> None:
        """Receipt metadata must carry the sensor frame sequence number for ordered replay."""
        result = edge_pipeline.process(_seal_frame(tmp_path))
        assert "sequence" in result.receipt["metadata"]
        assert isinstance(result.receipt["metadata"]["sequence"], int)
        assert result.receipt["metadata"]["sequence"] >= 1

    def test_process_receipt_signature_is_verifiable(
        self, edge_pipeline: EdgePipeline, tmp_path: Path
    ) -> None:
        """The Ed25519 signature in the minted receipt must pass SovereignKeyManager.verify_receipt.

        The edge payload signed by generate_receipt is reconstructed from the same
        frame bytes supplied to process() so that sensor_signature, sequence, and all
        other fields are identical to those used at signing time.
        """
        from sovereign_sieve import sieve_with_metrics

        frame_bytes: bytes = _seal_frame(tmp_path)
        result = edge_pipeline.process(frame_bytes)

        frame = SensorFrame.from_bytes(frame_bytes)
        sieve_out = sieve_with_metrics(frame.text_content())
        reconstructed_payload: dict[str, Any] = {
            "algorithm": frame.alg,
            "node_id": frame.n,
            "sensor_signature": frame.s,
            "sensor_timestamp": frame.t,
            "sequence": frame.q,
            "sieved_content": sieve_out.text,
        }
        assert SovereignKeyManager.verify_receipt(result.receipt, reconstructed_payload) is True

    def test_process_rejects_forged_sensor_signature(
        self, edge_pipeline: EdgePipeline, tmp_path: Path
    ) -> None:
        """process() must raise ValueError when the sensor frame signature fails
        HMAC-SHA256 verification; the forged payload must be rejected before reaching
        the sieve or ledger.

        :param edge_pipeline: Pipeline under test, provisioned with the mock HMAC secret.
        :type edge_pipeline: EdgePipeline
        :param tmp_path: Pytest-provided isolated temporary directory.
        :type tmp_path: Path
        """
        frame_bytes: bytes = _seal_frame(tmp_path)
        frame_dict: dict[str, Any] = json.loads(frame_bytes.decode("utf-8"))
        frame_dict["s"] = "00" * (len(frame_dict["s"]) // 2)
        forged_bytes: bytes = json.dumps(
            frame_dict, separators=(",", ":"), sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        with pytest.raises(ValueError, match="signature verification failed"):
            edge_pipeline.process(forged_bytes)

    def test_process_rejects_unsupported_algorithm(
        self, edge_pipeline: EdgePipeline, tmp_path: Path
    ) -> None:
        """process() must raise ValueError when sensor_secret is provisioned and the
        frame declares an algorithm other than hmac-sha256; no frame may bypass
        HMAC verification by spoofing the alg field to an unsupported identifier.

        :param edge_pipeline: Pipeline under test, provisioned with the mock HMAC secret.
        :type edge_pipeline: EdgePipeline
        :param tmp_path: Pytest-provided isolated temporary directory.
        :type tmp_path: Path
        """
        frame_bytes: bytes = _seal_frame(tmp_path)
        frame_dict: dict[str, Any] = json.loads(frame_bytes.decode("utf-8"))
        frame_dict["alg"] = "ecdsa-p256"
        spoofed_bytes: bytes = json.dumps(
            frame_dict, separators=(",", ":"), sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        with pytest.raises(ValueError, match="Unsupported or unauthenticated algorithm"):
            edge_pipeline.process(spoofed_bytes)

    def test_process_evicts_duplicate_submission_without_buffering(
        self,
        edge_pipeline: EdgePipeline,
        mem_ledger: SovereignLedger,
        tmp_path: Path,
    ) -> None:
        """process() must return buffered=False when append_receipt raises
        sqlite3.IntegrityError (duplicate payload_hash), matching the silent-eviction
        contract used during drain_buffer() replay loops; a duplicate must not be
        routed to the off-grid buffer.

        :param edge_pipeline: Pipeline under test wired to an in-memory ledger.
        :type edge_pipeline: EdgePipeline
        :param mem_ledger: In-memory ledger whose append_receipt is patched to raise.
        :type mem_ledger: SovereignLedger
        :param tmp_path: Pytest-provided isolated temporary directory.
        :type tmp_path: Path
        """
        with patch.object(
            mem_ledger,
            "append_receipt",
            side_effect=sqlite3.IntegrityError("UNIQUE constraint failed"),
        ):
            result: EdgeResult = edge_pipeline.process(_seal_frame(tmp_path))
        assert result.buffered is False, (
            "duplicate submission must return buffered=False; IntegrityError means "
            "the receipt is already in the ledger and must not be re-queued"
        )
        assert edge_pipeline.buffer_depth == 0, (
            "buffer_depth must remain 0 after a duplicate submission — the entry must "
            "be evicted, not buffered"
        )


# ---------------------------------------------------------------------------
# TestEdgePipelineBuffering
# ---------------------------------------------------------------------------

class TestEdgePipelineBuffering:
    """Verify off-grid buffering when the ledger is unreachable."""

    def test_process_sets_buffered_true_when_ledger_closed(self, tmp_path: Path) -> None:
        """process() must set buffered=True and not raise when the ledger is closed."""
        ledger = SovereignLedger(":memory:")
        pipeline = EdgePipeline(
            ledger=ledger,
            signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
            buffer_path=str(tmp_path / ".edge_buffer.jsonl"),
            sensor_secret=_SENSOR_SECRET,
        )
        ledger.close()
        try:
            result = pipeline.process(_seal_frame(tmp_path))
            assert result.buffered is True
        finally:
            pipeline.close()

    def test_process_increments_buffer_depth_on_ledger_error(self, tmp_path: Path) -> None:
        """buffer_depth must reflect each receipt queued after a ledger failure."""
        ledger = SovereignLedger(":memory:")
        pipeline = EdgePipeline(
            ledger=ledger,
            signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
            buffer_path=str(tmp_path / ".edge_buffer.jsonl"),
            sensor_secret=_SENSOR_SECRET,
        )
        ledger.close()
        try:
            pipeline.process(_seal_frame(tmp_path))
            assert pipeline.buffer_depth == 1
        finally:
            pipeline.close()

    def test_process_returns_payload_hash_even_when_buffered(self, tmp_path: Path) -> None:
        """payload_hash must be the ForensicReceipt hash even when the receipt is buffered."""
        ledger = SovereignLedger(":memory:")
        pipeline = EdgePipeline(
            ledger=ledger,
            signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
            buffer_path=str(tmp_path / ".edge_buffer.jsonl"),
            sensor_secret=_SENSOR_SECRET,
        )
        ledger.close()
        try:
            result = pipeline.process(_seal_frame(tmp_path))
            assert len(result.payload_hash) == 64
        finally:
            pipeline.close()

    def test_buffer_depth_zero_before_any_error(
        self, edge_pipeline: EdgePipeline
    ) -> None:
        """buffer_depth must be 0 before any processing errors occur."""
        assert edge_pipeline.buffer_depth == 0

    def test_close_is_idempotent(
        self, edge_pipeline: EdgePipeline
    ) -> None:
        """pipeline.close() must not deadlock or corrupt state when invoked multiple
        consecutive times; each call after the first must bypass the shutdown sequence
        and return immediately without raising.

        The fixture teardown also invokes close() after the test body, exercising a
        third consecutive call and confirming that OffGridBuffer.close() correctly
        detects the already-terminated worker thread via the internal
        ``_worker_running`` state flag instead of ``is_alive()``, and skips sentinel
        placement on all subsequent calls.

        :param edge_pipeline: Pipeline whose buffer worker thread is under test.
        :type edge_pipeline: EdgePipeline
        """
        edge_pipeline.close()
        edge_pipeline.close()

    def test_close_propagates_buffer_write_error_as_runtime_error(
        self, tmp_path: Path
    ) -> None:
        """EdgePipeline.close() must propagate RuntimeError from OffGridBuffer.close()
        when un-journaled write errors survive the internal drain_buffer() pass.

        Setup: close the ledger to force buffering, pre-seal both frames outside the
        patch context so sequence-file writes succeed, then patch builtins.open with
        ENOSPC only for the primary buffer file to make the background writer place the
        receipt in _write_errors instead of on disk.  The quarantine path is excluded
        from the patch so the quarantine write succeeds and worker_failed remains False.
        A second identical patch during close() ensures the drain_buffer() re-queue also
        fails on the primary buffer, keeping _write_errors non-empty when buffer.close()
        inspects it and raises."""
        import builtins as _builtins

        _real_open = _builtins.open
        buffer_path: str = str(tmp_path / ".edge_buffer.jsonl")

        def _fail_on_primary_append(*args: Any, **kwargs: Any) -> Any:
            path_arg: str = str(args[0]) if args else str(kwargs.get("file", ""))
            mode: str = args[1] if len(args) > 1 else kwargs.get("mode", "r")
            if "a" in mode and not path_arg.endswith(".quarantine"):
                raise OSError("ENOSPC: no space left on device")
            return _real_open(*args, **kwargs)

        ledger = SovereignLedger(":memory:")
        pipeline = EdgePipeline(
            ledger=ledger,
            signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        frame_a: bytes = _seal_frame(tmp_path)
        pipeline.process(frame_a)  # warm up key manager before any open() patch
        ledger.close()

        frame_b: bytes = _seal_frame(tmp_path)
        with patch("builtins.open", side_effect=_fail_on_primary_append):
            pipeline.process(frame_b)
            pipeline._buffer.flush()

        assert pipeline._buffer.write_error_count == 1

        with patch("builtins.open", side_effect=_fail_on_primary_append):
            with pytest.raises(RuntimeError, match="un-journaled"):
                pipeline.close()

    def test_close_chains_buffer_exc_from_drain_exc(self, tmp_path: Path) -> None:
        """When drain_buffer() raises and buffer.close() also raises, the buffer
        RuntimeError must be chained from the drain RuntimeError via __cause__ so
        that the root cause is not suppressed in the teardown traceback."""
        ledger = SovereignLedger(":memory:")
        pipeline = EdgePipeline(
            ledger=ledger,
            signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
            buffer_path=str(tmp_path / ".edge_buffer.jsonl"),
            sensor_secret=_SENSOR_SECRET,
        )
        drain_error: RuntimeError = RuntimeError("drain failed — ledger unreachable")
        buffer_error: RuntimeError = RuntimeError("buffer close failed — un-journaled receipts remain")

        original_buffer_close = pipeline._buffer.close

        def _raising_close() -> None:
            original_buffer_close()
            raise buffer_error

        with patch.object(pipeline, "drain_buffer", side_effect=drain_error):
            with patch.object(pipeline._buffer, "close", side_effect=_raising_close):
                with pytest.raises(RuntimeError, match="buffer close failed") as exc_info:
                    pipeline.close()

        assert exc_info.value.__cause__ is drain_error, (
            "buffer RuntimeError must be chained from drain RuntimeError via __cause__; "
            "root-cause exception was suppressed"
        )
        ledger.close()

    def test_process_buffers_on_application_level_ledger_exception(self, tmp_path: Path) -> None:
        """Any non-IntegrityError exception from append_receipt — including application-level
        validation failures such as ValueError — must route the ForensicReceipt to the
        off-grid buffer rather than propagating to the caller.

        The catch handler in process() must match Exception (not just SovereignStorageError
        or sqlite3.Error) so that unforeseen ledger-layer faults trigger the same
        buffer-fallback path as recognised storage errors, eliminating silent drops.

        :param tmp_path: Pytest-provided isolated temporary directory.
        :type tmp_path: Path
        """
        ledger: SovereignLedger = SovereignLedger(":memory:")
        pipeline: EdgePipeline = EdgePipeline(
            ledger=ledger,
            signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
            buffer_path=str(tmp_path / ".edge_buffer.jsonl"),
            sensor_secret=_SENSOR_SECRET,
        )
        try:
            _seal_frame(tmp_path)  # warm up sequence file before patching
            with patch.object(
                ledger,
                "append_receipt",
                side_effect=ValueError("ledger schema validation failed"),
            ):
                result: EdgeResult = pipeline.process(_seal_frame(tmp_path))
            assert result.buffered is True, (
                "process() must route the receipt to the off-grid buffer when "
                "append_receipt raises a non-storage application-level exception; "
                "ValueError must not propagate to the caller"
            )
            assert pipeline.buffer_depth > 0, (
                "buffer_depth must be non-zero after the receipt was diverted "
                "to the off-grid buffer on an application-level ledger fault"
            )
        finally:
            pipeline.close()
        ledger.close()

    def test_process_raises_sovereign_double_fault_error_on_double_failure(
        self, tmp_path: Path
    ) -> None:
        """process() must raise SovereignDoubleFaultError — with the signed receipt
        attached via .receipt — when the ledger raises SovereignStorageError and the
        subsequent buffer push also raises.  The signed receipt must be fully intact
        and extractable from the exception so the host can route it via an alternative
        channel rather than losing the payload entirely."""
        ledger = SovereignLedger(":memory:")
        pipeline = EdgePipeline(
            ledger=ledger,
            signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
            buffer_path=str(tmp_path / ".edge_buffer.jsonl"),
            sensor_secret=_SENSOR_SECRET,
        )
        frame_bytes: bytes = _seal_frame(tmp_path)
        pipeline.process(frame_bytes)  # warm up key manager before any patching

        ledger.close()
        frame_bytes = _seal_frame(tmp_path)
        with patch.object(
            pipeline._buffer, "push", side_effect=RuntimeError("buffer closed")
        ):
            with pytest.raises(SovereignDoubleFaultError) as exc_info:
                pipeline.process(frame_bytes)

        dfe: SovereignDoubleFaultError = exc_info.value
        assert isinstance(dfe.receipt, dict), "receipt attribute must be a dict"
        assert "payload_hash" in dfe.receipt, "receipt must carry payload_hash key"
        assert isinstance(dfe.receipt["payload_hash"], str)
        assert len(dfe.receipt["payload_hash"]) == 64
        assert isinstance(dfe.__cause__, RuntimeError)
        assert isinstance(dfe.ledger_error, (SovereignStorageError, sqlite3.Error)), (
            "ledger_error must preserve the root-cause ledger exception from the failed "
            "ledger commit so the full two-tier failure is visible without relying on "
            "implicit __context__ suppression from the raise-from chain"
        )
        pipeline._buffer.close()
        ledger.close()


# ---------------------------------------------------------------------------
# TestEdgePipelineDrainBuffer
# ---------------------------------------------------------------------------

class TestEdgePipelineDrainBuffer:
    """Verify drain_buffer() flushes buffered receipts to a recovered ledger."""

    def test_drain_buffer_commits_buffered_receipt(self, tmp_path: Path) -> None:
        """drain_buffer() must successfully commit a previously buffered receipt
        to a freshly opened ledger and return the payload_hash."""
        buffer_path = str(tmp_path / ".edge_buffer.jsonl")
        key_path = str(tmp_path / ".keys" / "edge_identity.pem")

        # Phase 1: buffer a receipt by closing the ledger before process().
        closed_ledger = SovereignLedger(":memory:")
        pipeline_a = EdgePipeline(
            ledger=closed_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        closed_ledger.close()
        try:
            result = pipeline_a.process(_seal_frame(tmp_path))
            pipeline_a._buffer.flush()
            assert result.buffered is True
            buffered_hash = result.payload_hash
        finally:
            pipeline_a.close()

        # Phase 2: drain via a fresh pipeline wired to a new open ledger.
        open_ledger = SovereignLedger(str(tmp_path / "recovery.db"))
        pipeline_b = EdgePipeline(
            ledger=open_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        try:
            committed = pipeline_b.drain_buffer()
            assert buffered_hash in committed
        finally:
            pipeline_b.close()
            open_ledger.close()

    def test_drain_buffer_clears_buffer_on_success(self, tmp_path: Path) -> None:
        """buffer_depth must be 0 after a successful drain_buffer() pass."""
        buffer_path = str(tmp_path / ".edge_buffer.jsonl")
        key_path = str(tmp_path / ".keys" / "edge_identity.pem")

        closed_ledger = SovereignLedger(":memory:")
        pipeline_a = EdgePipeline(
            ledger=closed_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        closed_ledger.close()
        try:
            pipeline_a.process(_seal_frame(tmp_path))
            pipeline_a._buffer.flush()
        finally:
            pipeline_a.close()

        open_ledger = SovereignLedger(str(tmp_path / "recovery2.db"))
        pipeline_b = EdgePipeline(
            ledger=open_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        try:
            pipeline_b.drain_buffer()
            assert pipeline_b.buffer_depth == 0
        finally:
            pipeline_b.close()
            open_ledger.close()

    def test_drain_buffer_requeues_on_persistent_ledger_failure(
        self, tmp_path: Path
    ) -> None:
        """drain_buffer() must re-queue entries that still cannot reach the ledger."""
        buffer_path = str(tmp_path / ".edge_buffer.jsonl")
        key_path = str(tmp_path / ".keys" / "edge_identity.pem")

        # Buffer a receipt with a closed ledger.
        closed_ledger_a = SovereignLedger(":memory:")
        pipeline_a = EdgePipeline(
            ledger=closed_ledger_a,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        closed_ledger_a.close()
        try:
            pipeline_a.process(_seal_frame(tmp_path))
            pipeline_a._buffer.flush()
            assert pipeline_a.buffer_depth == 1
        finally:
            pipeline_a.close()

        # Attempt drain with another closed ledger — must re-queue.
        closed_ledger_b = SovereignLedger(":memory:")
        pipeline_b = EdgePipeline(
            ledger=closed_ledger_b,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        closed_ledger_b.close()
        try:
            committed = pipeline_b.drain_buffer()
            assert committed == []
            assert pipeline_b.buffer_depth == 1
        finally:
            pipeline_b.close()

    def test_drain_buffer_returns_empty_list_when_nothing_buffered(
        self, edge_pipeline: EdgePipeline
    ) -> None:
        """drain_buffer() must return an empty list when the buffer is empty."""
        assert edge_pipeline.drain_buffer() == []

    def test_drain_buffer_ledger_integrity_after_flush(self, tmp_path: Path) -> None:
        """The recovery ledger's hash chain must pass integrity verification after
        drain_buffer() commits all buffered receipts."""
        buffer_path = str(tmp_path / ".edge_buffer.jsonl")
        key_path = str(tmp_path / ".keys" / "edge_identity.pem")

        closed_ledger = SovereignLedger(":memory:")
        pipeline_a = EdgePipeline(
            ledger=closed_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        closed_ledger.close()
        try:
            # Buffer two receipts.
            pipeline_a.process(_seal_frame(tmp_path))
            pipeline_a._buffer.flush()
            pipeline_a.process(_seal_frame(tmp_path))
            pipeline_a._buffer.flush()
        finally:
            pipeline_a.close()

        recovery_ledger = SovereignLedger(str(tmp_path / "integrity_recovery.db"))
        pipeline_b = EdgePipeline(
            ledger=recovery_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        try:
            committed = pipeline_b.drain_buffer()
            assert len(committed) == 2
            assert recovery_ledger.verify_ledger_integrity() is True
        finally:
            pipeline_b.close()
            recovery_ledger.close()

    def test_drain_buffer_evicts_permanently_on_non_storage_exception(
        self, tmp_path: Path
    ) -> None:
        """Per-entry non-storage exceptions in the replay loop must be permanently evicted,
        not re-queued.  A ValueError raised by append_receipt is caught by the inner
        ``except Exception as permanent_err:`` clause; the entry is silently discarded and
        the loop advances to the next entry without propagating the exception.

        Setup: buffer 3 receipts.  On replay, entries 1 and 3 commit successfully; entry 2
        raises ValueError.  drain_buffer() must return without raising, committed must
        contain exactly 2 hashes (entries 1 and 3), and buffer_depth must be 0 — entry 2
        was evicted, not re-queued.

        :param tmp_path: Pytest-provided isolated temporary directory.
        :type tmp_path: Path
        """
        import io
        import sys as _sys
        buffer_path: str = str(tmp_path / ".edge_buffer.jsonl")
        key_path: str = str(tmp_path / ".keys" / "edge_identity.pem")

        closed_ledger: SovereignLedger = SovereignLedger(":memory:")
        pipeline_a: EdgePipeline = EdgePipeline(
            ledger=closed_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        closed_ledger.close()
        try:
            pipeline_a.process(_seal_frame(tmp_path, {"seq": 1}))
            pipeline_a.process(_seal_frame(tmp_path, {"seq": 2}))
            pipeline_a.process(_seal_frame(tmp_path, {"seq": 3}))
            pipeline_a._buffer.flush()
            assert pipeline_a._buffer.size == 3
        finally:
            pipeline_a._buffer.close()

        open_ledger: SovereignLedger = SovereignLedger(str(tmp_path / "recovery.db"))
        pipeline_b: EdgePipeline = EdgePipeline(
            ledger=open_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        call_count: list[int] = [0]
        real_append = open_ledger.append_receipt

        def _fail_on_second(receipt: dict[str, Any], content: str) -> str:
            call_count[0] += 1
            if call_count[0] == 2:
                raise ValueError("permanent schema validation failure")
            return real_append(receipt, content)

        stderr_sink: io.StringIO = io.StringIO()
        try:
            with patch.object(open_ledger, "append_receipt", side_effect=_fail_on_second):
                with patch("sys.stderr", stderr_sink):
                    committed: list[str] = pipeline_b.drain_buffer()

            assert len(committed) == 2, (
                f"Expected 2 committed hashes (entries 1 and 3); got {len(committed)} — "
                "entry 2 (ValueError) must be evicted, not re-queued"
            )
            assert pipeline_b.buffer_depth == 0, (
                f"buffer_depth={pipeline_b.buffer_depth} — evicted entry must not be "
                "re-queued to the buffer; only transient storage faults are re-queued"
            )
        finally:
            pipeline_b._buffer.close()
            open_ledger.close()

    def test_drain_buffer_permanent_fault_emits_critical_log(
        self, tmp_path: Path
    ) -> None:
        """Per-entry permanent eviction must write a SOVEREIGN-EDGE CRITICAL line to
        sys.stderr containing the exception type, message, and payload_hash so that a
        process supervisor can identify and recover the dropped receipt out-of-band.

        :param tmp_path: Pytest-provided isolated temporary directory.
        :type tmp_path: Path
        """
        import io
        buffer_path: str = str(tmp_path / ".edge_buffer.jsonl")
        key_path: str = str(tmp_path / ".keys" / "edge_identity.pem")

        closed_ledger: SovereignLedger = SovereignLedger(":memory:")
        pipeline: EdgePipeline = EdgePipeline(
            ledger=closed_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        closed_ledger.close()
        try:
            pipeline.process(_seal_frame(tmp_path, {"seq": 1}))
            pipeline._buffer.flush()
        finally:
            pipeline._buffer.close()

        open_ledger: SovereignLedger = SovereignLedger(str(tmp_path / "recovery.db"))
        pipeline2: EdgePipeline = EdgePipeline(
            ledger=open_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        stderr_sink: io.StringIO = io.StringIO()
        try:
            with patch.object(
                open_ledger,
                "append_receipt",
                side_effect=TypeError("receipt dict missing required field"),
            ):
                with patch("sys.stderr", stderr_sink):
                    pipeline2.drain_buffer()
        finally:
            pipeline2._buffer.close()
            open_ledger.close()

        stderr_output: str = stderr_sink.getvalue()
        assert "SOVEREIGN-EDGE CRITICAL" in stderr_output, (
            "stderr must contain the CRITICAL prefix so process supervisors can filter "
            f"permanent eviction events; got: {stderr_output!r}"
        )
        assert "TypeError" in stderr_output, (
            f"stderr must name the exception type for diagnosis; got: {stderr_output!r}"
        )
        assert "receipt dict missing required field" in stderr_output, (
            f"stderr must include the exception message; got: {stderr_output!r}"
        )

    def test_drain_buffer_survives_push_failure_on_requeue(
        self, tmp_path: Path
    ) -> None:
        """drain_buffer() must iterate over the entire requeue list before raising when
        push() fails; aborting at the first failure would silently orphan every remaining
        item in the local requeue variable, making them unrecoverable."""
        ledger = SovereignLedger(":memory:")
        pipeline = EdgePipeline(
            ledger=ledger,
            signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
            buffer_path=str(tmp_path / ".edge_buffer.jsonl"),
            sensor_secret=_SENSOR_SECRET,
        )
        try:
            # Phase 1: force two receipts into the buffer by making append_receipt fail.
            with patch.object(
                ledger, "append_receipt",
                side_effect=SovereignStorageError("ledger temporarily unreachable"),
            ):
                pipeline.process(_seal_frame(tmp_path, {"seq": 1}))
                pipeline.process(_seal_frame(tmp_path, {"seq": 2}))
            pipeline._buffer.flush()

            push_call_count: list[int] = [0]

            def _failing_push(receipt: dict[str, Any], content: str) -> None:
                push_call_count[0] += 1
                raise RuntimeError("simulated buffer unavailable during requeue")

            # Phase 2: drain_buffer with the ledger still down and push() always failing.
            with patch.object(
                ledger, "append_receipt",
                side_effect=SovereignStorageError("ledger still unreachable"),
            ):
                with patch.object(pipeline._buffer, "push", side_effect=_failing_push):
                    with pytest.raises(RuntimeError, match="could not re-queue"):
                        pipeline.drain_buffer()

            assert push_call_count[0] == 2, (
                f"Expected push() called 2 times; got {push_call_count[0]} — "
                "drain_buffer() aborted at first push failure and orphaned the rest"
            )
        finally:
            pipeline._buffer.close()
            ledger.close()

    def test_drain_buffer_raises_runtime_error_on_buffer_read_failure(
        self, tmp_path: Path
    ) -> None:
        """drain_buffer() must raise RuntimeError chained from OSError when
        OffGridBuffer.drain() raises OSError on the file read; the pipeline must halt
        replay and surface the disk failure to the operator rather than silently
        returning an empty committed list."""
        import pathlib

        buffer_path: str = str(tmp_path / ".edge_buffer.jsonl")
        key_path: str = str(tmp_path / ".keys" / "edge_identity.pem")

        closed_ledger: SovereignLedger = SovereignLedger(":memory:")
        pipeline: EdgePipeline = EdgePipeline(
            ledger=closed_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        closed_ledger.close()
        try:
            pipeline.process(_seal_frame(tmp_path))
            pipeline._buffer.flush()
            assert pipeline._buffer.size == 1
        finally:
            pipeline.close()

        open_ledger: SovereignLedger = SovereignLedger(str(tmp_path / "recovery.db"))
        pipeline_b: EdgePipeline = EdgePipeline(
            ledger=open_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        try:
            with patch.object(
                pathlib.Path, "read_text", side_effect=OSError("simulated read error")
            ):
                with pytest.raises(RuntimeError, match="off-grid buffer file operation failed") as exc_info:
                    pipeline_b.drain_buffer()
            assert isinstance(exc_info.value.__cause__, OSError)
        finally:
            pipeline_b._buffer.drain()  # clear buffer so close() does not raise
            pipeline_b._buffer.close()
            open_ledger.close()

    def test_drain_buffer_raises_on_rotation_failure(self, tmp_path: Path) -> None:
        """drain_buffer() must raise RuntimeError (not return an empty committed list)
        when os.replace fails during the atomic buffer-file rotation inside
        OffGridBuffer.drain().  Buffered entries must be preserved on disk so that a
        subsequent drain_buffer() call can recover them once the filesystem is repaired."""
        buffer_path: str = str(tmp_path / ".edge_buffer.jsonl")
        key_path: str = str(tmp_path / ".keys" / "edge_identity.pem")

        # Phase 1: buffer one receipt via a closed ledger.
        closed_ledger: SovereignLedger = SovereignLedger(":memory:")
        pipeline_a: EdgePipeline = EdgePipeline(
            ledger=closed_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        closed_ledger.close()
        try:
            pipeline_a.process(_seal_frame(tmp_path))
            pipeline_a._buffer.flush()
        finally:
            pipeline_a._buffer.close()

        # Phase 2: attempt recovery but simulate an os.replace crash mid-rotation.
        open_ledger: SovereignLedger = SovereignLedger(str(tmp_path / "recovery.db"))
        pipeline_b: EdgePipeline = EdgePipeline(
            ledger=open_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        try:
            with patch("sovereign_edge.buffer.os.replace", side_effect=OSError("Simulated rotation crash")):
                with pytest.raises(RuntimeError, match="off-grid buffer file operation failed") as exc_info:
                    pipeline_b.drain_buffer()
            assert isinstance(exc_info.value.__cause__, OSError), (
                "RuntimeError must be chained from the OSError raised by os.replace "
                "so operators can inspect the underlying filesystem error"
            )
            # Phase 3: verify entries survived on disk — recovery must succeed after
            # the simulated crash clears, confirming no silent discard occurred.
            committed: list[str] = pipeline_b.drain_buffer()
            assert len(committed) == 1, (
                f"Expected 1 committed receipt after rotation recovery; got {len(committed)} — "
                "the rotation crash may have silently discarded the buffered entry"
            )
        finally:
            pipeline_b._buffer.close()
            open_ledger.close()

    def test_drain_buffer_evicts_duplicate_receipt_on_integrity_error(self, tmp_path: Path) -> None:
        """drain_buffer() must evict a receipt that the ledger rejects with
        sqlite3.IntegrityError (duplicate payload_hash) without re-buffering it,
        preventing infinite replay loops where a duplicate is perpetually
        re-queued on every drain_buffer() invocation."""
        buffer_path: str = str(tmp_path / ".edge_buffer.jsonl")
        key_path: str = str(tmp_path / ".keys" / "edge_identity.pem")

        closed_ledger: SovereignLedger = SovereignLedger(":memory:")
        pipeline_a: EdgePipeline = EdgePipeline(
            ledger=closed_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        closed_ledger.close()
        try:
            pipeline_a.process(_seal_frame(tmp_path))
            pipeline_a._buffer.flush()
        finally:
            pipeline_a._buffer.close()

        open_ledger: SovereignLedger = SovereignLedger(str(tmp_path / "recovery.db"))
        pipeline_b: EdgePipeline = EdgePipeline(
            ledger=open_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        try:
            with patch.object(
                open_ledger,
                "append_receipt",
                side_effect=sqlite3.IntegrityError("UNIQUE constraint failed"),
            ):
                committed: list[str] = pipeline_b.drain_buffer()
            assert committed == [], "IntegrityError duplicate must not appear in committed list"
            assert pipeline_b.buffer_depth == 0, (
                f"buffer_depth={pipeline_b.buffer_depth} — duplicate was re-queued "
                "instead of being evicted; re-buffering causes infinite replay loops"
            )
        finally:
            pipeline_b._buffer.close()
            open_ledger.close()

    def test_drain_buffer_preserves_staging_file_on_mid_replay_crash(self, tmp_path: Path) -> None:
        """drain_buffer() must preserve the .staging file when an exception aborts the
        ledger replay loop mid-stream.  commit_drain() must not be called on any error
        path so that un-replayed entries survive a hard crash and can be recovered on
        the subsequent drain_buffer() invocation."""
        buffer_path: str = str(tmp_path / ".edge_buffer.jsonl")
        staging_path: Path = Path(buffer_path + ".staging")
        key_path: str = str(tmp_path / ".keys" / "edge_identity.pem")

        # Phase 1: buffer two receipts via a closed ledger so both land on disk.
        closed_ledger: SovereignLedger = SovereignLedger(":memory:")
        pipeline_a: EdgePipeline = EdgePipeline(
            ledger=closed_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        closed_ledger.close()
        try:
            pipeline_a.process(_seal_frame(tmp_path))
            pipeline_a.process(_seal_frame(tmp_path))
            pipeline_a._buffer.flush()
        finally:
            pipeline_a._buffer.close()

        # Phase 2: crash the replay loop after the first entry is committed.
        # The staging file must survive because commit_drain() must not be called.
        open_ledger: SovereignLedger = SovereignLedger(str(tmp_path / "recovery.db"))
        pipeline_b: EdgePipeline = EdgePipeline(
            ledger=open_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        try:
            call_count: list[int] = [0]
            original_append = open_ledger.append_receipt

            def _crash_on_second(
                receipt_dict: dict[str, Any], sieved_content: str
            ) -> str:
                call_count[0] += 1
                if call_count[0] >= 2:
                    raise RuntimeError("simulated mid-replay crash")
                return original_append(receipt_dict, sieved_content)

            with patch.object(open_ledger, "append_receipt", side_effect=_crash_on_second):
                with pytest.raises(RuntimeError, match="simulated mid-replay crash"):
                    pipeline_b.drain_buffer()

            assert staging_path.exists(), (
                "staging file must survive a mid-replay crash; commit_drain() must only "
                "be called when all entries are confirmed committed or re-queued"
            )
            staging_lines: list[str] = [
                ln for ln in staging_path.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            assert len(staging_lines) == 2, (
                f"staging file must contain both original entries (got {len(staging_lines)}); "
                "un-replayed rows must not be vaporized on a mid-stream crash"
            )

            # Phase 3: verify that the second drain_buffer() recovers and commits
            # the re-queued entry (entry 1) that was re-buffered during the crash path.
            pipeline_b._buffer.flush()
            recovered: list[str] = pipeline_b.drain_buffer()
            assert len(recovered) == 1, (
                f"second drain_buffer() must commit the surviving re-queued entry; "
                f"got {len(recovered)} committed entries"
            )
            assert not staging_path.exists(), (
                "staging file must be deleted by commit_drain() after successful replay"
            )
        finally:
            pipeline_b._buffer.close()
            open_ledger.close()

    def test_drain_buffer_deduplicates_on_post_crash_restart(self, tmp_path: Path) -> None:
        """A crash-restart cycle must not cause already-committed receipts to be
        submitted to the ledger a second time.

        When drain_buffer() aborts mid-replay, :meth:`_recover_staging` merges the
        surviving staging file (containing all original entries) back into the active
        buffer on the next boot.  Re-submitted entries that are already in the ledger
        trigger ``sqlite3.IntegrityError``; the inner replay loop must catch that error
        and silently evict the duplicate rather than re-queuing it, ensuring each
        receipt is persisted exactly once across a crash-restart boundary.

        Phase 1: buffer 2 receipts via a closed ledger so both land on disk.
        Phase 2: crash the replay loop after committing entry 1; staging file is
        preserved, entry 2 is re-queued to the active buffer.
        Phase 3: construct a new pipeline, triggering ``_recover_staging()`` to merge
        staging (entries 1+2) into active (entry 2), producing three lines.
        ``drain_buffer()`` must commit exactly 1 new entry — the second receipt — while
        silently evicting the duplicate first receipt via ``IntegrityError``.

        :param tmp_path: Pytest-provided isolated temporary directory.
        :type tmp_path: Path
        """
        buffer_path: str = str(tmp_path / ".edge_buffer.jsonl")
        staging_path: Path = Path(buffer_path + ".staging")
        key_path: str = str(tmp_path / ".keys" / "edge_identity.pem")

        # Phase 1: force 2 receipts into the buffer via a closed ledger.
        closed_ledger: SovereignLedger = SovereignLedger(":memory:")
        pipeline_a: EdgePipeline = EdgePipeline(
            ledger=closed_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        closed_ledger.close()
        try:
            pipeline_a.process(_seal_frame(tmp_path))
            pipeline_a.process(_seal_frame(tmp_path))
            pipeline_a._buffer.flush()
        finally:
            pipeline_a._buffer.close()

        # Phase 2: crash the replay loop after committing entry 1 so that the
        # staging file survives and entry 2 is re-queued to the active buffer.
        recovery_ledger: SovereignLedger = SovereignLedger(str(tmp_path / "recovery.db"))
        pipeline_b: EdgePipeline = EdgePipeline(
            ledger=recovery_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        call_count: list[int] = [0]
        original_append = recovery_ledger.append_receipt

        def _crash_on_second(
            receipt_dict: dict[str, Any], sieved_content: str
        ) -> str:
            call_count[0] += 1
            if call_count[0] >= 2:
                raise RuntimeError("simulated mid-replay crash")
            return original_append(receipt_dict, sieved_content)

        try:
            with patch.object(recovery_ledger, "append_receipt", side_effect=_crash_on_second):
                with pytest.raises(RuntimeError, match="simulated mid-replay crash"):
                    pipeline_b.drain_buffer()
            pipeline_b._buffer.flush()
            assert staging_path.exists(), "staging file must survive mid-replay crash"
        finally:
            pipeline_b._buffer.close()

        # Phase 3: new pipeline triggers _recover_staging() to merge staging (entries
        # 1+2) into the active buffer (entry 2), producing 3 total lines.
        # drain_buffer() must commit exactly 1 new entry — entry 2 — and silently
        # evict entry 1 (already committed) and the duplicate entry 2 via IntegrityError.
        pipeline_c: EdgePipeline = EdgePipeline(
            ledger=recovery_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
            sensor_secret=_SENSOR_SECRET,
        )
        try:
            committed: list[str] = pipeline_c.drain_buffer()
            assert len(committed) == 1, (
                f"Expected exactly 1 new commit after crash-restart deduplication; "
                f"got {len(committed)} — entry already committed to the ledger before "
                "the crash must be silently evicted via IntegrityError, not re-committed"
            )
        finally:
            pipeline_c._buffer.close()
            recovery_ledger.close()


# ---------------------------------------------------------------------------
# TestOffGridBufferAsync
# ---------------------------------------------------------------------------

class TestOffGridBufferAsync:
    """Verify backpressure isolation and chronological replay ordering."""

    def _make_receipt(self, tag: str = "test", sequence: int = 0) -> dict[str, Any]:
        return {
            "timestamp": _TIMESTAMP,
            "payload_hash": f"hash_{tag}",
            "public_key": "base64key==",
            "signature": f"sig_{tag}",
            "metadata": {"sequence": sequence} if sequence else {},
        }

    def test_size_reflects_inflight_entry_before_flush(self, tmp_path: Path) -> None:
        """size must count in-flight queue entries before they reach disk."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        try:
            buf.push(self._make_receipt("A"), "content A")
            assert buf.size >= 1
        finally:
            buf.close()

    def test_flush_ensures_entry_is_on_disk(self, tmp_path: Path) -> None:
        """flush() must block until the background writer commits the entry to disk."""
        buf_path = tmp_path / "buf.jsonl"
        buf = OffGridBuffer(str(buf_path))
        try:
            buf.push(self._make_receipt("A"), "content A")
            buf.flush()
            assert buf_path.exists()
            non_empty = [ln for ln in buf_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            assert len(non_empty) == 1
        finally:
            buf.close()

    def test_drain_sorts_entries_ascending_by_sequence(self, tmp_path: Path) -> None:
        """drain() must return entries in ascending sequence order regardless of push order."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        try:
            r_late = self._make_receipt("late", sequence=5)
            r_early = self._make_receipt("early", sequence=2)
            buf.push(r_late, "late content")
            buf.push(r_early, "early content")
            entries = buf.drain()
            assert len(entries) == 2
            assert entries[0][0]["payload_hash"] == "hash_early"
            assert entries[1][0]["payload_hash"] == "hash_late"
        finally:
            buf.close()

    def test_drain_stable_sort_preserves_fifo_for_equal_sequence(
        self, tmp_path: Path
    ) -> None:
        """drain() must preserve FIFO push order when entries share the same sequence key."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        try:
            buf.push(self._make_receipt("first"), "first content")
            buf.push(self._make_receipt("second"), "second content")
            entries = buf.drain()
            assert entries[0][0]["payload_hash"] == "hash_first"
            assert entries[1][0]["payload_hash"] == "hash_second"
        finally:
            buf.close()

    def test_drain_tolerates_non_integer_sequence_value(self, tmp_path: Path) -> None:
        """drain() must not raise when a sequence value cannot be cast to int; entry
        falls back to sort key 0 rather than aborting the entire drain pass."""
        buf_path = tmp_path / "buf.jsonl"
        buf = OffGridBuffer(str(buf_path))
        try:
            r_corrupt = {
                "timestamp": _TIMESTAMP,
                "payload_hash": "hash_corrupt_seq",
                "public_key": "key==",
                "signature": "sig_c",
                "metadata": {"sequence": "not-a-number"},
            }
            r_normal = self._make_receipt("normal", sequence=3)
            buf.push(r_corrupt, "corrupt seq content")
            buf.push(r_normal, "normal content")
            entries = buf.drain()
            assert len(entries) == 2
        finally:
            buf.close()

    def test_size_correct_after_drain_preserves_concurrent_committed_count(
        self, tmp_path: Path
    ) -> None:
        """_committed is decremented by the drained count, not zeroed, so entries
        committed after flush() but before the file swap are not lost from the counter."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        try:
            buf.push(self._make_receipt("A"), "content A")
            buf.drain()
            buf.push(self._make_receipt("B"), "content B")
            assert buf.size == 1
        finally:
            buf.close()

    def test_concurrent_push_vs_close_no_orphan_entries(self, tmp_path: Path) -> None:
        """Every push() racing against close() must be resolved without orphaning entries.

        Verifies the _drain_lock sentinel-placement invariant: push() and close() both
        acquire _drain_lock before touching the queue, so the sentinel is always the last
        item the worker processes.  After the worker joins, queue.unfinished_tasks must be
        zero — any non-zero value indicates an entry was enqueued after the sentinel and
        the worker exited without calling task_done() for it.  All accepted pushes must be
        fully accounted for between the on-disk JSONL lines and write_error_count.
        """
        buf_path = tmp_path / "buf.jsonl"
        buf = OffGridBuffer(str(buf_path))

        n_threads: int = 20
        accepted: list[int] = []
        rejected: list[int] = []
        result_lock: threading.Lock = threading.Lock()

        def attempt_push(index: int) -> None:
            try:
                buf.push(
                    self._make_receipt(f"node_{index}", sequence=index),
                    f"content {index}",
                )
                with result_lock:
                    accepted.append(index)
            except RuntimeError:
                with result_lock:
                    rejected.append(index)

        push_threads: list[threading.Thread] = [
            threading.Thread(target=attempt_push, args=(i,), daemon=True)
            for i in range(n_threads)
        ]
        for t in push_threads:
            t.start()

        # Race close() against the active push threads — the critical window under test.
        try:
            buf.close()
        except RuntimeError:
            pass  # write errors are not expected in tmp_path but are acceptable here

        for t in push_threads:
            t.join()

        # Every push was deterministically resolved — no thread crashed silently.
        assert len(accepted) + len(rejected) == n_threads, (
            f"accepted={len(accepted)}, rejected={len(rejected)}, expected total={n_threads}"
        )

        # No orphan entries: _pending reaches zero when the worker's finally block decrements
        # it for every item including the sentinel; a non-zero value means an entry was
        # enqueued behind the sentinel and the worker exited without resolving it.
        assert buf._pending == 0, (
            f"buffer has {buf._pending} in-flight entries after close(); "
            "an entry was enqueued behind the sentinel or task_done() was not called"
        )

        # All accepted entries are on disk or preserved in write_errors — none vaporized.
        on_disk_count: int = 0
        if buf_path.exists():
            on_disk_count = sum(
                1
                for ln in buf_path.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            )
        assert on_disk_count + buf.write_error_count == len(accepted), (
            f"on_disk={on_disk_count}, write_errors={buf.write_error_count}, "
            f"accepted={len(accepted)}: entry count mismatch"
        )

    def test_concurrent_close_no_counter_drift(self, tmp_path: Path) -> None:
        """Calling close() from multiple threads simultaneously must not inflate _pending:
        the is_alive() check, _closed flag update, and sentinel placement must form a
        single atomic operation so exactly one sentinel is ever placed regardless of
        how many threads race into close() concurrently.  A drifted counter surfaces
        as size() > 0 after all close threads complete."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        buf.push(self._make_receipt("A", sequence=1), "content A")
        buf.flush()
        buf.drain()  # clear _committed so size reflects only _pending after close

        close_threads: list[threading.Thread] = [
            threading.Thread(target=buf.close, daemon=True) for _ in range(8)
        ]
        for t in close_threads:
            t.start()
        for t in close_threads:
            t.join(timeout=5.0)

        assert all(not t.is_alive() for t in close_threads), (
            "One or more close() threads deadlocked under concurrent teardown"
        )
        assert buf.size == 0, (
            f"size() drifted to {buf.size} after concurrent close() — "
            "multiple sentinels placed, _pending incremented more than once"
        )


# ---------------------------------------------------------------------------
# TestEdgePipelineSieveFault
# ---------------------------------------------------------------------------

class TestEdgePipelineSieveFault:
    """Verify that a sieve-layer runtime failure triggers the safe fallback path."""

    def test_sieve_fault_marks_receipt_metadata(
        self, edge_pipeline: EdgePipeline, tmp_path: Path
    ) -> None:
        """process() must stamp sieve_fault=True in receipt metadata when sieve raises."""
        with patch(
            "sovereign_edge.pipeline.sieve_with_metrics",
            side_effect=RuntimeError("sieve broke"),
        ):
            result = edge_pipeline.process(_seal_frame(tmp_path))
        assert result.receipt["metadata"].get("sieve_fault") is True

    def test_sieve_fault_falls_back_to_raw_text(
        self, edge_pipeline: EdgePipeline, tmp_path: Path
    ) -> None:
        """process() must use raw text_content() as sieved_content when sieve raises."""
        frame_bytes: bytes = _seal_frame(tmp_path)
        with patch(
            "sovereign_edge.pipeline.sieve_with_metrics",
            side_effect=RuntimeError("sieve broke"),
        ):
            result = edge_pipeline.process(frame_bytes)
        frame = SensorFrame.from_bytes(frame_bytes)
        assert result.sieved_content == frame.text_content()

    def test_sieve_fault_tax_savings_is_zero(
        self, edge_pipeline: EdgePipeline, tmp_path: Path
    ) -> None:
        """tax_savings_percentage must be 0.0 when the sieve stage fails."""
        with patch(
            "sovereign_edge.pipeline.sieve_with_metrics",
            side_effect=RuntimeError("sieve broke"),
        ):
            result = edge_pipeline.process(_seal_frame(tmp_path))
        assert result.tax_savings_percentage == 0.0

    def test_sieve_fault_still_commits_to_ledger(
        self, edge_pipeline: EdgePipeline, mem_ledger: SovereignLedger, tmp_path: Path
    ) -> None:
        """process() must still commit the receipt to the ledger even after a sieve fault."""
        with patch(
            "sovereign_edge.pipeline.sieve_with_metrics",
            side_effect=RuntimeError("sieve broke"),
        ):
            result = edge_pipeline.process(_seal_frame(tmp_path))
        assert result.buffered is False
        assert mem_ledger.verify_ledger_integrity(expected_tip_hash=result.payload_hash) is True


# ---------------------------------------------------------------------------
# TestOffGridBufferWriteErrors
# ---------------------------------------------------------------------------

class TestOffGridBufferWriteErrors:
    """Verify that background disk write failures are tracked and recoverable via drain()."""

    def _make_receipt(self, tag: str = "test", sequence: int = 1) -> dict[str, Any]:
        return {
            "timestamp": _TIMESTAMP,
            "payload_hash": f"hash_{tag}",
            "public_key": "base64key==",
            "signature": f"sig_{tag}",
            "metadata": {"sequence": sequence},
        }

    def test_disk_write_error_increments_write_error_count(self, tmp_path: Path) -> None:
        """A background write failure must be reflected in write_error_count rather than
        silently dropped.  __init__ creates the parent directory; removing it immediately
        afterwards forces FileNotFoundError (an OSError subclass) on every write attempt."""
        buf_dir: Path = tmp_path / "buf_dir"
        buf = OffGridBuffer(str(buf_dir / "buffer.jsonl"))
        (buf_dir / "buffer.jsonl.lock").unlink()  # remove lock so rmdir() can proceed
        buf_dir.rmdir()  # remove the directory to trigger FileNotFoundError on every write
        buf.push(self._make_receipt("A"), "content A")
        buf.flush()
        assert buf.write_error_count == 1
        buf.drain()  # clear write errors so close() does not raise
        buf.close()

    def test_size_includes_write_error_entries(self, tmp_path: Path) -> None:
        """size must count write-error entries so buffer_depth remains accurate after a
        disk failure; the entry that could not be fsync'd must not vanish from the tally."""
        buf_dir: Path = tmp_path / "buf_dir"
        buf = OffGridBuffer(str(buf_dir / "buffer.jsonl"))
        (buf_dir / "buffer.jsonl.lock").unlink()
        buf_dir.rmdir()
        buf.push(self._make_receipt("A"), "content A")
        buf.flush()
        assert buf.size == 1
        buf.drain()  # clear write errors so close() does not raise
        buf.close()

    def test_drain_returns_write_error_entries(self, tmp_path: Path) -> None:
        """drain() must include write-error entries in its return value so the pipeline
        can commit them to the ledger; write_error_count must reach zero after a successful
        drain pass."""
        buf_dir: Path = tmp_path / "buf_dir"
        receipt = self._make_receipt("A")
        buf = OffGridBuffer(str(buf_dir / "buffer.jsonl"))
        (buf_dir / "buffer.jsonl.lock").unlink()
        buf_dir.rmdir()
        buf.push(receipt, "content A")
        buf.flush()
        entries = buf.drain()
        assert len(entries) == 1
        assert entries[0][0] == receipt
        assert entries[0][1] == "content A"
        assert buf.write_error_count == 0
        buf.close()

    def test_close_raises_when_write_errors_remain(self, tmp_path: Path) -> None:
        """close() must raise RuntimeError rather than silently discard un-journaled
        receipts.  A patched OSError on open() simulates ENOSPC during the background
        write; the fault is tracked in _write_errors and close() surfaces it by raising
        so the host application is forced to acknowledge the data before shutdown."""
        buf = OffGridBuffer(str(tmp_path / "buffer.jsonl"))
        receipt = self._make_receipt("A")
        with patch("builtins.open", side_effect=OSError("ENOSPC: no space left on device")):
            buf.push(receipt, "content A")
            buf.flush()
        assert buf.write_error_count == 1
        with pytest.raises(RuntimeError, match="un-journaled"):
            buf.close()

    def test_push_raises_after_close(self, tmp_path: Path) -> None:
        """push() must raise RuntimeError when called on a closed buffer rather than
        enqueue into a dead queue; enqueueing after close would increment _pending but
        never receive task_done(), causing any subsequent flush() to block indefinitely."""
        buf = OffGridBuffer(str(tmp_path / "buffer.jsonl"))
        buf.close()
        with pytest.raises(RuntimeError, match="closed"):
            buf.push(self._make_receipt("post-close"), "content")

    def test_worker_non_oserror_failure_does_not_hang(self, tmp_path: Path) -> None:
        """A non-OSError exception inside the background writer must set worker_failed,
        drain remaining queue items so that flush() returns without blocking, and allow
        close() to complete without deadlocking.  push() must raise immediately after."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        receipt = self._make_receipt("A")
        with patch("builtins.open", side_effect=RuntimeError("unexpected worker crash")):
            buf.push(receipt, "content A")
            buf.flush()  # must not hang even though the worker crashed
        assert buf.worker_failed is True
        with pytest.raises(RuntimeError, match="background writer"):
            buf.push(self._make_receipt("B"), "content B")
        buf.drain()  # recover write-error entries so close() succeeds
        buf.close()  # must not hang

    def test_drain_concurrent_with_worker_crash_no_deadlock(self, tmp_path: Path) -> None:
        """When drain() holds _drain_lock and is blocked in queue.join(), a simultaneous
        non-OSError worker failure must not deadlock: the evacuation loop must call
        task_done() for all queued items without acquiring _drain_lock so that
        queue.join() unblocks and drain() completes within the five-second timeout.
        Regressions in the evacuation path (e.g. re-introducing _drain_lock acquisition)
        would cause this test to hang indefinitely."""
        import builtins as _builtins
        _real_open = _builtins.open

        def _fail_on_append(*args: Any, **kwargs: Any) -> Any:
            mode: str = args[1] if len(args) > 1 else kwargs.get("mode", "r")
            if "a" in mode:
                raise RuntimeError("simulated catastrophic writer failure")
            return _real_open(*args, **kwargs)

        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        receipt_a = self._make_receipt("A", sequence=1)
        receipt_b = self._make_receipt("B", sequence=2)
        drain_thread: threading.Thread = threading.Thread(target=buf.drain, daemon=True)
        with patch("builtins.open", side_effect=_fail_on_append):
            buf.push(receipt_a, "content A")
            buf.push(receipt_b, "content B")
            drain_thread.start()
            drain_thread.join(timeout=5.0)
        assert not drain_thread.is_alive(), (
            "drain() deadlocked — evacuation loop must not acquire _drain_lock "
            "while drain() holds it"
        )
        assert buf.worker_failed is True
        buf.close()

    def test_push_toctou_worker_crash_no_dangling_items(self, tmp_path: Path) -> None:
        """16 concurrent push() calls racing against a non-OSError worker crash must leave
        _pending == 0 and every accepted push reflected in _write_errors after all threads
        resolve.

        The crashing open() call sets ``crash_imminent`` before raising, releasing the
        racer threads into the liveness-gate window simultaneously via
        ``threading.Barrier``.  Without the fix — where ``queue.put()`` lives outside
        ``_count_lock`` — a racer can pass the ``_worker_failed`` check, release the lock,
        wait while the worker's evacuation loop drains the queue to empty, and then call
        ``put()``, leaving an orphaned item with no ``task_done()`` ever called.  With the
        fix, the check and ``put()`` share a single ``_count_lock`` acquisition, so every
        racer either enqueues atomically before the evacuation can start, or sees
        ``_worker_failed = True`` after the evacuation holds the lock and raises.

        :param tmp_path: Pytest-provided isolated temporary directory.
        :type tmp_path: Path
        """
        import builtins as _builtins
        _real_open = _builtins.open

        n_concurrent: int = 16
        crash_imminent: threading.Event = threading.Event()
        all_racing: threading.Barrier = threading.Barrier(n_concurrent, timeout=10.0)

        def _crash_on_append(*args: Any, **kwargs: Any) -> Any:
            mode: str = args[1] if len(args) > 1 else kwargs.get("mode", "r")
            if "a" in mode:
                crash_imminent.set()
                raise RuntimeError("synthetic non-OSError writer crash")
            return _real_open(*args, **kwargs)

        accepted: list[int] = []
        rejected: list[int] = []
        r_lock: threading.Lock = threading.Lock()

        buf: OffGridBuffer = OffGridBuffer(str(tmp_path / "buf.jsonl"))

        def _racer(index: int) -> None:
            crash_imminent.wait()
            all_racing.wait()
            try:
                buf.push(
                    self._make_receipt(f"r{index}", sequence=index + 1),
                    f"content {index}",
                )
                with r_lock:
                    accepted.append(index)
            except RuntimeError:
                with r_lock:
                    rejected.append(index)

        threads: list[threading.Thread] = [
            threading.Thread(target=_racer, args=(i,), daemon=True)
            for i in range(n_concurrent)
        ]
        for t in threads:
            t.start()

        with patch("builtins.open", side_effect=_crash_on_append):
            buf.push(self._make_receipt("seed", sequence=0), "seed content")
            buf.flush()

        for t in threads:
            t.join(timeout=10.0)

        assert all(not t.is_alive() for t in threads), "racer thread timed out"
        assert len(accepted) + len(rejected) == n_concurrent, "not all racer threads resolved"

        assert buf._pending == 0, (
            f"_pending={buf._pending}: dangling item — push() passed the liveness gate "
            "and deposited into the queue after the evacuation loop completed; "
            "the _worker_failed check and queue.put() must share a single _count_lock section"
        )

        buf_path: Path = tmp_path / "buf.jsonl"
        on_disk: int = 0
        if buf_path.exists():
            on_disk = sum(
                1 for ln in buf_path.read_text(encoding="utf-8").splitlines() if ln.strip()
            )
        assert on_disk + buf.write_error_count + buf.dead_letter_count >= len(accepted), (
            f"accepted={len(accepted)} but on_disk={on_disk} + "
            f"write_errors={buf.write_error_count} + dead_letter={buf.dead_letter_count}; "
            "an accepted push was not captured in write_errors, dead_letter, or on disk"
        )

        buf.drain()
        buf.close()

    def test_close_skips_sentinel_during_worker_os_exit_gap(self, tmp_path: Path) -> None:
        """close() must guard on _worker_running rather than is_alive() to prevent
        sentinel injection into an abandoned queue during the OS-thread-exit gap.

        After a non-OSError crash the worker clears ``_worker_running = False`` under
        ``_count_lock`` before its final return.  A concurrent :meth:`close` that
        evaluates ``_worker_running`` under the same lock reads ``False`` and skips
        sentinel injection entirely.  Evaluating ``is_alive()`` instead produces a
        timing gap: the Python thread function has returned but the OS has not yet
        marked the thread dead, so ``is_alive()`` still returns ``True``.  In that
        window, :meth:`close` injects ``queue.put(None)`` which increments
        ``unfinished_tasks`` without a living consumer to call ``task_done()``, stalling
        any subsequent :meth:`flush` → ``queue.join()`` call indefinitely.

        ``is_alive()`` is patched to return ``True`` after the worker has fully exited
        to simulate that gap deterministically without relying on scheduler timing.

        :param tmp_path: Pytest-provided isolated temporary directory.
        :type tmp_path: Path
        """
        import builtins as _builtins
        _real_open = _builtins.open

        def _crash_on_append(*args: Any, **kwargs: Any) -> Any:
            mode: str = args[1] if len(args) > 1 else kwargs.get("mode", "r")
            if "a" in mode:
                raise RuntimeError("synthetic non-OSError crash")
            return _real_open(*args, **kwargs)

        buf: OffGridBuffer = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        with patch("builtins.open", side_effect=_crash_on_append):
            buf.push(self._make_receipt("A"), "content A")
            buf.flush()

        buf._worker_thread.join(timeout=5.0)
        assert not buf._worker_thread.is_alive(), "worker thread did not exit within 5 s"
        assert not buf._worker_running, "_worker_running not cleared before thread return"
        assert buf._pending == 0, f"_pending={buf._pending} after crash+evacuation"

        buf.drain()  # clear _write_errors so close() does not raise

        # Patch is_alive() to return True, reproducing the OS-exit gap where the thread
        # function has returned but the OS has not yet unregistered the thread.
        close_done: threading.Event = threading.Event()

        def _call_close() -> None:
            buf.close()
            close_done.set()

        with patch.object(buf._worker_thread, "is_alive", return_value=True):
            threading.Thread(target=_call_close, daemon=True).start()
            assert close_done.wait(timeout=3.0), (
                "close() stalled — is_alive() returning True triggered sentinel "
                "injection into an abandoned queue; guard must use _worker_running"
            )

        assert buf._pending == 0, (
            f"_pending={buf._pending} — sentinel inflated the counter without "
            "a consumer to call task_done()"
        )

        # Confirm queue.join() does not block (unfinished_tasks must be 0)
        flush_done: threading.Event = threading.Event()
        threading.Thread(
            target=lambda: (buf.flush(), flush_done.set()), daemon=True
        ).start()
        assert flush_done.wait(timeout=2.0), (
            "flush() stalled after close() — sentinel was injected and "
            "unfinished_tasks is non-zero with no living consumer"
        )

    def test_drain_read_failed_flag_set_on_oserror(self, tmp_path: Path) -> None:
        """drain_read_failed must become True and drain() must re-raise the OSError when
        Path.read_text raises after the buffer file is confirmed to exist; the pipeline
        orchestrator must catch the OSError and the flag allows distinguishing a genuine
        empty drain from a filesystem-blocked drain."""
        import pathlib
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        buf.push(self._make_receipt("A"), "content A")
        buf.flush()
        with patch.object(pathlib.Path, "read_text", side_effect=OSError("Permission denied")):
            with pytest.raises(OSError, match="Permission denied"):
                buf.drain()
        assert buf.drain_read_failed is True
        buf.drain()  # recover on-disk entry so close() does not raise
        buf.close()

    def test_drain_read_failed_flag_resets_on_successful_drain(self, tmp_path: Path) -> None:
        """drain_read_failed must revert to False on the next successful drain() after a
        prior OSError so that a transient filesystem fault does not permanently mark the
        diagnostic telemetry as failed once the storage tier recovers."""
        import pathlib
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        buf.push(self._make_receipt("B"), "content B")
        buf.flush()
        with patch.object(pathlib.Path, "read_text", side_effect=OSError("Transient I/O error")):
            with pytest.raises(OSError):
                buf.drain()
        assert buf.drain_read_failed is True, (
            "drain_read_failed should be True immediately after the OSError"
        )
        buf.push(self._make_receipt("B"), "content B")
        buf.flush()
        buf.drain()
        assert buf.drain_read_failed is False, (
            "drain_read_failed must reset to False after a successful drain "
            "— stale True flag persists transient fault as a false-positive"
        )
        buf.close()

    def test_crash_evacuation_racing_close_leaves_pending_at_zero(
        self, tmp_path: Path
    ) -> None:
        """close() called concurrently with a non-OSError worker crash must leave
        _pending at exactly zero.  When close() places a sentinel while _worker_running
        is True (race window between evacuation loop releasing _count_lock and the
        finally block), the evacuation's finally block must drain and decrement _pending
        for that sentinel so the counter does not drift and stall flush().

        :param tmp_path: Pytest-provided isolated temporary directory.
        :type tmp_path: Path
        """
        import builtins as _builtins
        _real_open = _builtins.open

        def _crash_on_append(*args: Any, **kwargs: Any) -> Any:
            mode: str = args[1] if len(args) > 1 else kwargs.get("mode", "r")
            if "a" in mode:
                raise RuntimeError("synthetic non-OSError worker crash")
            return _real_open(*args, **kwargs)

        buf: OffGridBuffer = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        with patch("builtins.open", side_effect=_crash_on_append):
            buf.push(self._make_receipt("A"), "content A")
            buf.flush()

        assert buf.worker_failed is True

        close_errors: list[Exception] = []
        error_lock: threading.Lock = threading.Lock()

        def _try_close() -> None:
            try:
                buf.close()
            except RuntimeError as exc:
                with error_lock:
                    close_errors.append(exc)

        threads: list[threading.Thread] = [
            threading.Thread(target=_try_close, daemon=True) for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert all(not t.is_alive() for t in threads), (
            "close() thread deadlocked — flush() in join() stalled with "
            "_pending > 0 indicating an unaccounted sentinel"
        )
        assert buf._pending == 0, (
            f"_pending={buf._pending} after crash + concurrent close(); "
            "sentinel placed by a racing close() was not decremented in the "
            "evacuation finally block"
        )

    def test_racing_close_sentinel_drained_by_evacuation(self, tmp_path: Path) -> None:
        """Sentinel placed by close() inside _count_lock must be consumed by the
        evacuation finally block when close() races with an in-progress worker crash,
        preventing an orphaned sentinel from stalling flush() → queue.join().

        The sentinel is placed atomically with _closed=True under _count_lock.  The
        evacuation finally block re-acquires _count_lock, observes _closed=True, and
        drains the sentinel before clearing _worker_running=False.  The outer
        try/finally belt-and-suspenders sweep handles any sentinel that survives past
        the evacuation path.  Together these guards guarantee _pending reaches zero and
        close() returns without blocking even when racing with an active crash.

        :param tmp_path: Pytest-provided isolated temporary directory.
        :type tmp_path: Path
        """
        import builtins as _builtins
        _real_open = _builtins.open

        crash_entered: threading.Event = threading.Event()
        release_crash: threading.Event = threading.Event()

        def _gated_crash(*args: Any, **kwargs: Any) -> Any:
            mode: str = args[1] if len(args) > 1 else kwargs.get("mode", "r")
            if "a" in mode:
                crash_entered.set()
                release_crash.wait(timeout=5.0)
                raise RuntimeError("gated synthetic crash for sentinel race")
            return _real_open(*args, **kwargs)

        buf: OffGridBuffer = OffGridBuffer(str(tmp_path / "race_buf.jsonl"))
        close_done: threading.Event = threading.Event()

        def _close_buf() -> None:
            try:
                buf.close()
            except RuntimeError:
                pass  # expected: un-journaled entries remain; we are testing non-hang
            finally:
                close_done.set()

        with patch("builtins.open", side_effect=_gated_crash):
            buf.push(self._make_receipt("race", sequence=1), "race content")
            crash_entered.wait(timeout=5.0)
            close_thread: threading.Thread = threading.Thread(
                target=_close_buf, daemon=True
            )
            close_thread.start()
            release_crash.set()

        assert close_done.wait(timeout=5.0), (
            "close() stalled — sentinel placed by racing close() was not consumed by "
            "the evacuation finally block or the outer try/finally sweep; "
            "flush() -> queue.join() blocked indefinitely with _pending > 0"
        )
        assert buf._pending == 0, (
            f"_pending={buf._pending} after crash + racing close(); "
            "sentinel or entry was not decremented — counter drift would stall "
            "any subsequent flush() call"
        )

    def test_double_write_fault_emits_to_stderr(self, tmp_path: Path) -> None:
        """When both the primary JSONL write and the quarantine file write fail, the raw
        entry must be written to sys.stderr with an unbuffered flush so the container or
        process supervisor can capture and recover the payload out-of-band.  The
        background worker must mark itself as failed (worker_failed=True) rather than
        leaving the entry to sit in a volatile in-memory list with no external signal.

        Setup: the buffer directory is removed after construction so every file open()
        call — both the primary write to the JSONL file and the quarantine write to
        {path}.quarantine — raises FileNotFoundError (a subclass of OSError), triggering
        the double-fault path in _disk_writer.

        :param tmp_path: Pytest-provided isolated temporary directory.
        :type tmp_path: Path
        """
        import io
        buf_dir: Path = tmp_path / "dbl"
        buf_dir.mkdir()
        buf_path: Path = buf_dir / "buf.jsonl"
        buf: OffGridBuffer = OffGridBuffer(str(buf_path))
        lock_path: Path = Path(str(buf_path) + ".lock")
        receipt: dict[str, Any] = self._make_receipt("double-fault", sequence=7)
        stderr_sink: io.StringIO = io.StringIO()
        lock_path.unlink()
        buf_dir.rmdir()
        with patch("sys.stderr", stderr_sink):
            buf.push(receipt, "double-fault-content")
            buf.flush()
        assert buf.worker_failed, (
            "worker must mark itself as failed after a double write-fault "
            "(both primary write and quarantine write raised)"
        )
        assert "double-fault" in stderr_sink.getvalue(), (
            "sys.stderr must contain the raw entry payload after a double write-fault "
            "so the supervisor can capture and recover the receipt that could not be "
            "persisted to either the JSONL file or the quarantine file"
        )
        buf.drain()
        buf.close()


class TestEdgePipelineSecureInit:
    """Verify that EdgePipeline enforces secure-by-default initialization."""

    def test_pipeline_raises_configuration_error_without_secret(self, tmp_path: Path) -> None:
        """Constructing EdgePipeline without sensor_secret and without allow_unauthenticated=True
        must raise SovereignConfigurationError immediately so that accidental unauthenticated
        deployments are caught at construction time rather than silently passing frames."""
        ledger = SovereignLedger(":memory:")
        with pytest.raises(SovereignConfigurationError, match="allow_unauthenticated=True"):
            EdgePipeline(
                ledger=ledger,
                signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
                buffer_path=str(tmp_path / ".edge_buffer.jsonl"),
            )
        ledger.close()

    def test_pipeline_raises_configuration_error_with_empty_string_secret(self, tmp_path: Path) -> None:
        """Passing sensor_secret="" is equivalent to omitting it — SovereignConfigurationError
        must be raised unless allow_unauthenticated=True is also passed."""
        ledger = SovereignLedger(":memory:")
        with pytest.raises(SovereignConfigurationError):
            EdgePipeline(
                ledger=ledger,
                signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
                buffer_path=str(tmp_path / ".edge_buffer.jsonl"),
                sensor_secret="",
            )
        ledger.close()

    def test_pipeline_construction_succeeds_with_allow_unauthenticated(self, tmp_path: Path) -> None:
        """Passing allow_unauthenticated=True must suppress SovereignConfigurationError and
        allow the pipeline to initialize without a secret — the explicit opt-out must work."""
        ledger = SovereignLedger(":memory:")
        pipeline = EdgePipeline(
            ledger=ledger,
            signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
            buffer_path=str(tmp_path / ".edge_buffer.jsonl"),
            allow_unauthenticated=True,
        )
        pipeline.close()
        ledger.close()

    def test_sovereign_configuration_error_is_value_error_subclass(self, tmp_path: Path) -> None:
        """SovereignConfigurationError must be catchable as ValueError so existing callers
        that catch ValueError at construction time handle the new exception without change."""
        ledger = SovereignLedger(":memory:")
        with pytest.raises(ValueError):
            EdgePipeline(
                ledger=ledger,
                signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
                buffer_path=str(tmp_path / ".edge_buffer.jsonl"),
            )
        ledger.close()

    def test_configuration_error_does_not_create_buffer_lock_file(self, tmp_path: Path) -> None:
        """SovereignConfigurationError must fire before OffGridBuffer is constructed so
        no .lock file is written when the exception propagates.  Without the constructor
        ordering fix, the buffer is created before the secret is validated; the lock file
        is left on disk and permanently blocks every subsequent construction attempt that
        shares the same buffer_path for the lifetime of the process.

        :param tmp_path: Pytest-provided isolated temporary directory.
        :type tmp_path: Path
        """
        ledger: SovereignLedger = SovereignLedger(":memory:")
        buffer_path: Path = tmp_path / ".edge_buffer.jsonl"
        lock_path: Path = Path(str(buffer_path) + ".lock")
        with pytest.raises(SovereignConfigurationError):
            EdgePipeline(
                ledger=ledger,
                signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
                buffer_path=str(buffer_path),
            )
        assert not lock_path.exists(), (
            ".lock file must not exist after SovereignConfigurationError from __init__; "
            "buffer construction must follow, not precede, secret validation so that a "
            "misconfigured constructor does not orphan a lock on every failed attempt"
        )
        ledger.close()

    def test_init_failure_after_buffer_creation_closes_worker_thread(self, tmp_path: Path) -> None:
        """A post-buffer construction failure must close the OffGridBuffer so the
        background writer thread is stopped and the .lock file is not orphaned.

        Without the try/except wrapper around the post-buffer init steps, an exception
        raised by SovereignKeyManager (or any other step after OffGridBuffer is
        constructed) leaves the daemon thread running and the .lock file on disk.
        A subsequent construction attempt on the same buffer_path then raises
        RuntimeError("already held by process …") even though the original pipeline
        never completed initialization, blocking the recovery path indefinitely.

        :param tmp_path: Pytest-provided isolated temporary directory.
        :type tmp_path: Path
        """
        ledger: SovereignLedger = SovereignLedger(":memory:")
        buffer_path: Path = tmp_path / ".edge_buffer.jsonl"
        lock_path: Path = Path(str(buffer_path) + ".lock")
        with patch(
            "sovereign_edge.pipeline.SovereignKeyManager",
            side_effect=RuntimeError("key manager init failed"),
        ):
            with pytest.raises(RuntimeError, match="key manager init failed"):
                EdgePipeline(
                    ledger=ledger,
                    signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
                    buffer_path=str(buffer_path),
                    allow_unauthenticated=True,
                )
        assert not lock_path.exists(), (
            ".lock file must be released after a post-buffer construction failure; "
            "EdgePipeline.__init__ must call self._buffer.close() in its except handler "
            "so the background writer thread is joined and the lock is unlinked before "
            "the exception propagates — a stranded thread and orphaned lock on the same "
            "path permanently block every subsequent construction attempt"
        )
        ledger.close()
