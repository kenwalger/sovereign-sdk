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
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

os.environ.setdefault("SOVEREIGN_NODE_SECRET", "sovereign-edge-test-passphrase-v1")

from sovereign_core.crypto import SovereignKeyManager
from sovereign_ledger import SovereignLedger, SovereignStorageError
from sovereign_sensor import bootstrap_sensor_node
from sovereign_sensor.drivers.software_fallback import SoftwareFallbackDriver

from sovereign_edge import EdgePipeline, EdgeResult, OffGridBuffer, SensorFrame

_NODE_ID: str = "edge-test-node-001"
_TIMESTAMP: str = "2026-06-19T00:00:00Z"
_PAYLOAD: dict[str, Any] = {"sensor": "temperature", "unit": "C", "value": 23}
_KEY_PATH: str = SoftwareFallbackDriver.MOCK_KEY_SENTINEL


def _seal_frame(tmp_path: Path, payload: dict[str, Any] | None = None) -> bytes:
    """Seal a sensor wire frame using the software fallback driver and mock key."""
    envelope = bootstrap_sensor_node(
        _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
    )
    return envelope.seal(_TIMESTAMP, payload or _PAYLOAD)


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
        assert buf.size == 0

    def test_drain_returns_empty_list_when_buffer_absent(self, tmp_path: Path) -> None:
        """drain() must return an empty list when the buffer file does not exist."""
        buf = OffGridBuffer(str(tmp_path / "nonexistent.jsonl"))
        assert buf.drain() == []

    def test_push_increments_size(self, tmp_path: Path) -> None:
        """size must reflect the number of entries written via push()."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        buf.push(self._make_receipt("A"), "content A")
        assert buf.size == 1
        buf.push(self._make_receipt("B"), "content B")
        assert buf.size == 2

    def test_drain_returns_all_entries(self, tmp_path: Path) -> None:
        """drain() must return every (receipt, sieved_content) pair previously pushed."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        buf.push(self._make_receipt("X"), "payload X")
        buf.push(self._make_receipt("Y"), "payload Y")
        entries = buf.drain()
        assert len(entries) == 2

    def test_drain_returns_entries_in_fifo_order(self, tmp_path: Path) -> None:
        """drain() must preserve push() insertion order."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        buf.push(self._make_receipt("first"), "first content")
        buf.push(self._make_receipt("second"), "second content")
        entries = buf.drain()
        assert entries[0][0]["payload_hash"] == "hash_first"
        assert entries[1][0]["payload_hash"] == "hash_second"

    def test_drain_clears_buffer_after_read(self, tmp_path: Path) -> None:
        """size must be 0 after drain() empties the buffer."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        buf.push(self._make_receipt("Z"), "content Z")
        buf.drain()
        assert buf.size == 0

    def test_drain_preserves_sieved_content_verbatim(self, tmp_path: Path) -> None:
        """drain() must return the exact sieved_content string supplied to push()."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        content = "temperature reading: 42°C — no filler here"
        buf.push(self._make_receipt("V"), content)
        entries = buf.drain()
        assert entries[0][1] == content

    def test_push_after_drain_works(self, tmp_path: Path) -> None:
        """push() must succeed after drain() has cleared the buffer."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        buf.push(self._make_receipt("A"), "content A")
        buf.drain()
        buf.push(self._make_receipt("B"), "content B")
        assert buf.size == 1
        entries = buf.drain()
        assert entries[0][0]["payload_hash"] == "hash_B"

    def test_drain_skips_malformed_json_lines(self, tmp_path: Path) -> None:
        """drain() must quarantine corrupt lines in dead_letter and return only valid entries."""
        buf_path = tmp_path / "buf.jsonl"
        buf = OffGridBuffer(str(buf_path))
        buf.push(self._make_receipt("good"), "good content")
        buf.flush()  # ensure background writer closes its file handle before we append
        with open(buf_path, "a", encoding="utf-8") as fh:
            fh.write("{corrupt-json\n")
        entries = buf.drain()
        assert len(entries) == 1
        assert entries[0][0]["payload_hash"] == "hash_good"

    def test_drain_size_zero_after_committed_line_corrupted_on_disk(
        self, tmp_path: Path
    ) -> None:
        """size must reach exactly 0 after drain() when a fsync'd line is subsequently
        corrupted on disk.  The _committed decrement must cover total disk line count
        (valid + dead-letter), not only successfully parsed lines; otherwise _committed
        stays at 1 for an empty file, producing permanent counter drift."""
        buf_path = tmp_path / "buf.jsonl"
        buf = OffGridBuffer(str(buf_path))
        buf.push(self._make_receipt("good"), "good content")
        buf.flush()
        assert buf.size == 1  # _committed == 1 after successful fsync
        buf_path.write_text("{corrupted-line\n", encoding="utf-8")
        entries = buf.drain()
        assert entries == []
        assert buf.size == 0
        assert buf.dead_letter_count == 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_ledger() -> SovereignLedger:
    """In-memory ledger — fast, side-effect free."""
    ledger = SovereignLedger(":memory:")
    yield ledger
    if not ledger._closed:
        ledger.close()


@pytest.fixture
def edge_pipeline(mem_ledger: SovereignLedger, tmp_path: Path) -> EdgePipeline:
    """EdgePipeline wired to an in-memory ledger with isolated key and buffer paths."""
    return EdgePipeline(
        ledger=mem_ledger,
        signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
        buffer_path=str(tmp_path / ".edge_buffer.jsonl"),
    )


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
        edge_pipeline.process(_seal_frame(tmp_path))
        cur = mem_ledger._conn.execute("SELECT COUNT(*) FROM forensic_ledger")
        assert cur.fetchone()[0] == 1

    def test_process_ledger_row_matches_result_payload_hash(
        self, edge_pipeline: EdgePipeline, mem_ledger: SovereignLedger, tmp_path: Path
    ) -> None:
        """The payload_hash stored in the ledger must match EdgeResult.payload_hash."""
        result = edge_pipeline.process(_seal_frame(tmp_path))
        cur = mem_ledger._conn.execute(
            "SELECT payload_hash FROM forensic_ledger WHERE payload_hash = ?",
            (result.payload_hash,),
        )
        assert cur.fetchone() is not None

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
        )
        ledger.close()

        result = pipeline.process(_seal_frame(tmp_path))
        assert result.buffered is True

    def test_process_increments_buffer_depth_on_ledger_error(self, tmp_path: Path) -> None:
        """buffer_depth must reflect each receipt queued after a ledger failure."""
        ledger = SovereignLedger(":memory:")
        pipeline = EdgePipeline(
            ledger=ledger,
            signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
            buffer_path=str(tmp_path / ".edge_buffer.jsonl"),
        )
        ledger.close()

        pipeline.process(_seal_frame(tmp_path))
        assert pipeline.buffer_depth == 1

    def test_process_returns_payload_hash_even_when_buffered(self, tmp_path: Path) -> None:
        """payload_hash must be the ForensicReceipt hash even when the receipt is buffered."""
        ledger = SovereignLedger(":memory:")
        pipeline = EdgePipeline(
            ledger=ledger,
            signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
            buffer_path=str(tmp_path / ".edge_buffer.jsonl"),
        )
        ledger.close()

        result = pipeline.process(_seal_frame(tmp_path))
        assert len(result.payload_hash) == 64

    def test_buffer_depth_zero_before_any_error(
        self, edge_pipeline: EdgePipeline
    ) -> None:
        """buffer_depth must be 0 before any processing errors occur."""
        assert edge_pipeline.buffer_depth == 0

    def test_close_propagates_buffer_write_error_as_runtime_error(
        self, tmp_path: Path
    ) -> None:
        """EdgePipeline.close() must propagate RuntimeError from OffGridBuffer.close()
        when un-journaled write errors survive the internal drain_buffer() pass.

        Setup: close the ledger to force buffering, pre-seal both frames outside the
        patch context so sequence-file writes succeed, then patch builtins.open with
        ENOSPC to make the background writer place the receipt in _write_errors instead
        of on disk.  A second identical patch during close() ensures the drain_buffer()
        re-queue also fails, keeping _write_errors non-empty when buffer.close()
        inspects it and raises."""
        ledger = SovereignLedger(":memory:")
        pipeline = EdgePipeline(
            ledger=ledger,
            signing_key=str(tmp_path / ".keys" / "edge_identity.pem"),
            buffer_path=str(tmp_path / ".edge_buffer.jsonl"),
        )
        frame_a: bytes = _seal_frame(tmp_path)
        pipeline.process(frame_a)  # warm up key manager before any open() patch
        ledger.close()

        frame_b: bytes = _seal_frame(tmp_path)
        with patch("builtins.open", side_effect=OSError("ENOSPC: no space left on device")):
            pipeline.process(frame_b)
            pipeline._buffer.flush()

        assert pipeline._buffer.write_error_count == 1

        with patch("builtins.open", side_effect=OSError("ENOSPC: no space left on device")):
            with pytest.raises(RuntimeError, match="un-journaled"):
                pipeline.close()


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
        )
        closed_ledger.close()

        result = pipeline_a.process(_seal_frame(tmp_path))
        assert result.buffered is True
        buffered_hash = result.payload_hash

        # Phase 2: drain via a fresh pipeline wired to a new open ledger.
        open_ledger = SovereignLedger(str(tmp_path / "recovery.db"))
        pipeline_b = EdgePipeline(
            ledger=open_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
        )

        committed = pipeline_b.drain_buffer()
        assert buffered_hash in committed
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
        )
        closed_ledger.close()
        pipeline_a.process(_seal_frame(tmp_path))

        open_ledger = SovereignLedger(str(tmp_path / "recovery2.db"))
        pipeline_b = EdgePipeline(
            ledger=open_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
        )
        pipeline_b.drain_buffer()

        assert pipeline_b.buffer_depth == 0
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
        )
        closed_ledger_a.close()
        pipeline_a.process(_seal_frame(tmp_path))
        assert pipeline_a.buffer_depth == 1

        # Attempt drain with another closed ledger — must re-queue.
        closed_ledger_b = SovereignLedger(":memory:")
        pipeline_b = EdgePipeline(
            ledger=closed_ledger_b,
            signing_key=key_path,
            buffer_path=buffer_path,
        )
        closed_ledger_b.close()

        committed = pipeline_b.drain_buffer()
        assert committed == []
        assert pipeline_b.buffer_depth == 1

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
        )
        closed_ledger.close()

        # Buffer two receipts.
        pipeline_a.process(_seal_frame(tmp_path))
        pipeline_a.process(_seal_frame(tmp_path))

        recovery_ledger = SovereignLedger(str(tmp_path / "integrity_recovery.db"))
        pipeline_b = EdgePipeline(
            ledger=recovery_ledger,
            signing_key=key_path,
            buffer_path=buffer_path,
        )
        committed = pipeline_b.drain_buffer()
        assert len(committed) == 2
        assert recovery_ledger.verify_ledger_integrity() is True
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
        buf.push(self._make_receipt("A"), "content A")
        assert buf.size >= 1

    def test_flush_ensures_entry_is_on_disk(self, tmp_path: Path) -> None:
        """flush() must block until the background writer commits the entry to disk."""
        buf_path = tmp_path / "buf.jsonl"
        buf = OffGridBuffer(str(buf_path))
        buf.push(self._make_receipt("A"), "content A")
        buf.flush()
        assert buf_path.exists()
        non_empty = [ln for ln in buf_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(non_empty) == 1

    def test_drain_sorts_entries_ascending_by_sequence(self, tmp_path: Path) -> None:
        """drain() must return entries in ascending sequence order regardless of push order."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        r_late = self._make_receipt("late", sequence=5)
        r_early = self._make_receipt("early", sequence=2)
        buf.push(r_late, "late content")
        buf.push(r_early, "early content")
        entries = buf.drain()
        assert len(entries) == 2
        assert entries[0][0]["payload_hash"] == "hash_early"
        assert entries[1][0]["payload_hash"] == "hash_late"

    def test_drain_stable_sort_preserves_fifo_for_equal_sequence(
        self, tmp_path: Path
    ) -> None:
        """drain() must preserve FIFO push order when entries share the same sequence key."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        buf.push(self._make_receipt("first"), "first content")
        buf.push(self._make_receipt("second"), "second content")
        entries = buf.drain()
        assert entries[0][0]["payload_hash"] == "hash_first"
        assert entries[1][0]["payload_hash"] == "hash_second"

    def test_drain_tolerates_non_integer_sequence_value(self, tmp_path: Path) -> None:
        """drain() must not raise when a sequence value cannot be cast to int; entry
        falls back to sort key 0 rather than aborting the entire drain pass."""
        buf_path = tmp_path / "buf.jsonl"
        buf = OffGridBuffer(str(buf_path))
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

    def test_size_correct_after_drain_preserves_concurrent_committed_count(
        self, tmp_path: Path
    ) -> None:
        """_committed is decremented by the drained count, not zeroed, so entries
        committed after flush() but before the file swap are not lost from the counter."""
        buf = OffGridBuffer(str(tmp_path / "buf.jsonl"))
        buf.push(self._make_receipt("A"), "content A")
        buf.drain()
        buf.push(self._make_receipt("B"), "content B")
        assert buf.size == 1


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
        cur = mem_ledger._conn.execute("SELECT COUNT(*) FROM forensic_ledger")
        assert cur.fetchone()[0] == 1


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
        buf_dir.rmdir()  # remove the directory __init__ just created to trigger write failure
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
