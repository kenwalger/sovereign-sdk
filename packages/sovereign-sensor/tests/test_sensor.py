# packages/sovereign-sensor/tests/test_sensor.py
"""Desktop validation suite for sovereign-sensor Phase 9.

Invariants verified across every test:
- bootstrap_sensor_node auto-detects a non-ESP32 desktop environment and
  binds SoftwareFallbackDriver without any explicit caller configuration.
- seal() produces well-formed, ultra-minified JSON bytes carrying exactly
  seven keys: v (protocol version), n (node_id), t (timestamp), q (sequence
  counter), alg (algorithm identifier), d (payload dict), s (signature string).
- Sequence counter increments monotonically on every seal() call and is
  persisted to the VFS sequence file, resuming correctly after a reboot
  rather than resetting to zero and opening a replay window.
- HMAC-SHA256 signatures change when the underlying key material changes,
  proving that key bytes participate in the cryptographic signature math.
- Signature is hex-encoded by the envelope layer (not the driver), guaranteeing
  that all bytes 0x00-0xFF map safely to alphanumeric characters without
  UnicodeDecodeError on constrained MicroPython silicon.
- sign() raises RuntimeError if called before initialize_hardware(), preventing
  silent keyless HMAC packets from bad setup sequencing.
- initialize_hardware() raises RuntimeError for any non-sentinel path that cannot
  be opened, eliminating silent key substitution for production key paths.
- bootstrap_sensor_node() falls back to SoftwareFallbackDriver with a printed
  warning when the selected hardware driver raises NotImplementedError, keeping
  the sealing and VFS layers exercisable before hardware acceleration is complete.
- Every test uses an isolated tmp_path sequence file so no test run pollutes
  the workspace VFS state or interferes with concurrent test execution.
"""
import json
from pathlib import Path

import pytest

from sovereign_sensor import SovereignEnvelope, bootstrap_sensor_node
from sovereign_sensor.drivers.software_fallback import SoftwareFallbackDriver


_NODE_ID = "node-sensor-001"
_KEY_PATH = "/mock/test_gateway.key"  # SoftwareFallbackDriver._MOCK_KEY_SENTINEL
_TIMESTAMP = "2026-06-16T00:00:00Z"
_PAYLOAD: dict = {"sensor": "temperature", "value": 42, "unit": "C"}


class TestBootstrap:
    """Verify platform detection and driver selection logic."""

    def test_returns_sovereign_envelope_instance(self, tmp_path: Path) -> None:
        """bootstrap_sensor_node must return a SovereignEnvelope on any non-ESP32 host."""
        result = bootstrap_sensor_node(
            _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
        )
        assert isinstance(result, SovereignEnvelope)

    def test_non_esp32_platform_selects_software_fallback(self, tmp_path: Path) -> None:
        """Desktop CI must bind SoftwareFallbackDriver, verified via the public algorithm() contract.

        The software fallback declares "hmac-sha256" via its algorithm() method,
        which is surfaced in the sealed envelope's "alg" field.  Asserting this
        value confirms driver selection through the public HAL interface without
        piercing private envelope attributes.
        """
        envelope = bootstrap_sensor_node(
            _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
        )
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert parsed["alg"] == "hmac-sha256"

    def test_software_driver_is_initialized_after_bootstrap(self, tmp_path: Path) -> None:
        """bootstrap_sensor_node must call initialize_hardware() before returning.

        Verified by confirming that seal() completes without exception on the
        first call: a driver whose initialize_hardware() was never invoked would
        raise RuntimeError and be unable to service sign() requests.
        """
        envelope = bootstrap_sensor_node(
            _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
        )
        result = envelope.seal(_TIMESTAMP, _PAYLOAD)
        assert isinstance(result, bytes)


    def test_bootstrap_falls_back_to_software_driver_when_hardware_not_implemented(
        self, tmp_path: Path
    ) -> None:
        """bootstrap_sensor_node must fall back to SoftwareFallbackDriver when the
        selected hardware driver raises NotImplementedError from initialize_hardware().

        Patches ``sys.platform`` to ``"esp32"`` so the factory routes to
        ``ESP32HardwareDriver``, whose ``initialize_hardware()`` raises
        ``NotImplementedError``.  The factory must catch that exception, emit a
        warning, and rebind to ``SoftwareFallbackDriver`` so the node can exercise
        the sealing, sequencing, and VFS serialization layers on the workbench
        before register-level crypto acceleration is complete.  The sealed envelope
        ``"alg"`` field confirms the software driver is active.

        :type tmp_path: Path
        """
        import unittest.mock

        with unittest.mock.patch("sys.platform", "esp32"):
            result = bootstrap_sensor_node(
                _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
            )
        assert isinstance(result, SovereignEnvelope)
        parsed = json.loads(result.seal(_TIMESTAMP, _PAYLOAD))
        assert parsed["alg"] == "hmac-sha256"


class TestDriverGuard:
    """Verify that SoftwareFallbackDriver enforces its initialization contract."""

    def test_sign_raises_runtime_error_if_not_initialized(self) -> None:
        """sign() must raise RuntimeError when called before initialize_hardware().

        Verifies the initialization guard that prevents silent, keyless HMAC
        packets arising from bad setup sequencing.  A driver that has never had
        initialize_hardware() invoked must not fall back to an empty key or
        produce any output.
        """
        driver = SoftwareFallbackDriver(_KEY_PATH)
        with pytest.raises(RuntimeError, match="initialize_hardware"):
            driver.sign(b"test-preimage")

    def test_initialize_hardware_raises_on_non_sentinel_missing_path(self) -> None:
        """initialize_hardware() must raise RuntimeError for any non-sentinel path
        that cannot be opened, eliminating the silent _MOCK_KEY substitution defect.

        A ``SoftwareFallbackDriver`` pointed at a real but absent key file must
        fail fast with a descriptive ``RuntimeError`` rather than substituting the
        fixed stub and silently producing signatures under an unknown key.  Only
        the explicit ``_MOCK_KEY_SENTINEL`` path opts into deterministic mock
        signing; every other missing path is a hard boot failure.
        """
        driver = SoftwareFallbackDriver("/nonexistent/production.key")
        with pytest.raises(RuntimeError, match="Key material loading failed"):
            driver.initialize_hardware()


class TestEnvelopeSeal:
    """Verify structural and semantic correctness of sealed envelopes."""

    def test_seal_returns_bytes(self, tmp_path: Path) -> None:
        """seal() must return raw bytes suitable for wire transmission."""
        envelope = bootstrap_sensor_node(
            _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
        )
        result = envelope.seal(_TIMESTAMP, _PAYLOAD)
        assert isinstance(result, bytes)

    def test_seal_produces_valid_json(self, tmp_path: Path) -> None:
        """Sealed bytes must parse as valid JSON without error."""
        envelope = bootstrap_sensor_node(
            _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
        )
        result = envelope.seal(_TIMESTAMP, _PAYLOAD)
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_seal_envelope_contains_exactly_seven_mandatory_keys(self, tmp_path: Path) -> None:
        """Transmission envelope must contain exactly: v, n, t, q, alg, d, s."""
        envelope = bootstrap_sensor_node(
            _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
        )
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert set(parsed.keys()) == {"v", "n", "t", "q", "alg", "d", "s"}

    def test_seal_protocol_version_is_integer_one(self, tmp_path: Path) -> None:
        """'v' field must be the integer 1, not the string '1'."""
        envelope = bootstrap_sensor_node(
            _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
        )
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert parsed["v"] == 1
        assert isinstance(parsed["v"], int)

    def test_seal_node_id_field_matches_constructor(self, tmp_path: Path) -> None:
        """'n' field must be the node_id supplied to bootstrap_sensor_node."""
        envelope = bootstrap_sensor_node(
            _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
        )
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert parsed["n"] == _NODE_ID

    def test_seal_timestamp_field_is_preserved_verbatim(self, tmp_path: Path) -> None:
        """'t' field must be the exact timestamp string passed to seal()."""
        envelope = bootstrap_sensor_node(
            _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
        )
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert parsed["t"] == _TIMESTAMP

    def test_seal_sequence_counter_starts_at_one(self, tmp_path: Path) -> None:
        """First seal() on a fresh envelope with no prior state must produce q=1.

        Uses an isolated tmp_path sequence file to guarantee the counter begins
        at zero regardless of any state left by prior test runs.
        """
        seq_file = str(tmp_path / ".sovereign_sequence")
        driver = SoftwareFallbackDriver(_KEY_PATH)
        driver.initialize_hardware()
        envelope = SovereignEnvelope(_NODE_ID, driver, sequence_file=seq_file)
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert parsed["q"] == 1

    def test_seal_sequence_counter_increments_monotonically(self, tmp_path: Path) -> None:
        """Consecutive seal() calls must produce strictly increasing 'q' values.

        Uses an isolated tmp_path sequence file to guarantee the counter begins
        at zero regardless of any state left by prior test runs.
        """
        seq_file = str(tmp_path / ".sovereign_sequence")
        driver = SoftwareFallbackDriver(_KEY_PATH)
        driver.initialize_hardware()
        envelope = SovereignEnvelope(_NODE_ID, driver, sequence_file=seq_file)
        q_values = [
            json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))["q"]
            for _ in range(3)
        ]
        assert q_values == [1, 2, 3]

    def test_seal_algorithm_field_matches_software_fallback_driver(self, tmp_path: Path) -> None:
        """'alg' field must carry the driver's declared algorithm identifier."""
        envelope = bootstrap_sensor_node(
            _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
        )
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert parsed["alg"] == "hmac-sha256"

    def test_seal_payload_round_trips_without_mutation(self, tmp_path: Path) -> None:
        """'d' field must equal the original payload dict after JSON round-trip."""
        envelope = bootstrap_sensor_node(
            _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
        )
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert parsed["d"] == _PAYLOAD

    def test_seal_signature_is_non_empty_ascii_string(self, tmp_path: Path) -> None:
        """'s' field must be a non-empty string decodable as ASCII."""
        envelope = bootstrap_sensor_node(
            _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
        )
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert isinstance(parsed["s"], str)
        assert len(parsed["s"]) > 0
        parsed["s"].encode("ascii")  # raises if non-ASCII

    def test_seal_signature_is_sha256_hex_length(self, tmp_path: Path) -> None:
        """Software fallback HMAC-SHA256 signature must be a 64-character hex digest."""
        envelope = bootstrap_sensor_node(
            _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
        )
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert len(parsed["s"]) == 64

    def test_seal_output_is_minified_json(self, tmp_path: Path) -> None:
        """Output bytes must not contain whitespace after separators (ultra-minified)."""
        envelope = bootstrap_sensor_node(
            _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
        )
        result = envelope.seal(_TIMESTAMP, _PAYLOAD)
        assert b": " not in result
        assert b", " not in result

    def test_seal_is_deterministic_across_independent_instances(self, tmp_path: Path) -> None:
        """Two envelope instances initialized from the same sequence file state
        must produce byte-identical seals for identical inputs.

        Both instances read the same persisted counter value N at construction
        time, so both increment to N+1 on their first seal() call.  With
        identical node_id, timestamp, payload, key material, and sequence
        position, the HMAC preimages are identical and the outputs match.
        """
        seq_file = str(tmp_path / ".sovereign_sequence")
        envelope_a = bootstrap_sensor_node(_NODE_ID, _KEY_PATH, sequence_file=seq_file)
        envelope_b = bootstrap_sensor_node(_NODE_ID, _KEY_PATH, sequence_file=seq_file)
        assert envelope_a.seal(_TIMESTAMP, _PAYLOAD) == envelope_b.seal(_TIMESTAMP, _PAYLOAD)

    def test_seal_signature_changes_when_payload_changes(self, tmp_path: Path) -> None:
        """Distinct payloads at the same sequence position must produce distinct signatures.

        Two independent instances are used so that both seal at the same counter
        value (both read N at init and increment to N+1 on their respective
        first calls), isolating payload as the sole independent variable.
        """
        seq_file = str(tmp_path / ".sovereign_sequence")
        envelope_a = bootstrap_sensor_node(_NODE_ID, _KEY_PATH, sequence_file=seq_file)
        envelope_b = bootstrap_sensor_node(_NODE_ID, _KEY_PATH, sequence_file=seq_file)
        sig_a = json.loads(envelope_a.seal(_TIMESTAMP, {"v": 1}))["s"]
        sig_b = json.loads(envelope_b.seal(_TIMESTAMP, {"v": 2}))["s"]
        assert sig_a != sig_b

    def test_seal_signature_contains_only_valid_hex_characters(self, tmp_path: Path) -> None:
        """'s' field must consist exclusively of lowercase hex characters (0-9, a-f).

        Validates that envelope.py applies binascii.hexlify() to the raw driver
        bytes rather than a bare .decode() call, guaranteeing that high-entropy
        binary output from any future hardware driver cannot trigger a
        UnicodeDecodeError on MicroPython silicon.
        """
        _HEX_ALPHABET: frozenset = frozenset("0123456789abcdef")
        envelope = bootstrap_sensor_node(
            _NODE_ID, _KEY_PATH, sequence_file=str(tmp_path / ".sovereign_sequence")
        )
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert all(c in _HEX_ALPHABET for c in parsed["s"])

    def test_seal_payload_key_order_does_not_affect_signature(self, tmp_path: Path) -> None:
        """sort_keys=True canonicalization must produce identical signatures for
        semantically equivalent payloads regardless of key insertion order.

        On constrained MicroPython targets, dict insertion order is not
        guaranteed stable across firmware versions or allocation events.
        Two instances sealing payloads with identical key-value pairs but
        inverted construction order must produce byte-identical signature
        fields, proving that alphabetical key sorting makes the HMAC
        preimage invariant to insertion order across all target platforms.
        """
        seq_file = str(tmp_path / ".sovereign_sequence")
        payload_ab: dict = {"a": 1, "b": 2}
        payload_ba: dict = {"b": 2, "a": 1}

        envelope_a = bootstrap_sensor_node(_NODE_ID, _KEY_PATH, sequence_file=seq_file)
        envelope_b = bootstrap_sensor_node(_NODE_ID, _KEY_PATH, sequence_file=seq_file)

        sig_a = json.loads(envelope_a.seal(_TIMESTAMP, payload_ab))["s"]
        sig_b = json.loads(envelope_b.seal(_TIMESTAMP, payload_ba))["s"]
        assert sig_a == sig_b

    def test_software_driver_signature_changes_with_different_key_files(
        self, tmp_path: Path
    ) -> None:
        """HMAC signatures must diverge when the underlying key material differs.

        Two drivers are initialized from distinct key files containing different
        secret bytes.  Both seal the same payload at q=1 (via separate, fresh
        sequence files), so the sequence position and all other preimage fields
        are held constant.  The signature difference proves that key bytes
        participate directly in the HMAC computation rather than being ignored.
        """
        key_file_a = tmp_path / "key_alpha.bin"
        key_file_b = tmp_path / "key_bravo.bin"
        key_file_a.write_bytes(b"sovereign-key-material-alpha-node-identity-v1")
        key_file_b.write_bytes(b"sovereign-key-material-bravo-node-identity-v1")

        driver_a = SoftwareFallbackDriver(str(key_file_a))
        driver_a.initialize_hardware()
        envelope_a = SovereignEnvelope(
            _NODE_ID, driver_a, sequence_file=str(tmp_path / "seq_a")
        )

        driver_b = SoftwareFallbackDriver(str(key_file_b))
        driver_b.initialize_hardware()
        envelope_b = SovereignEnvelope(
            _NODE_ID, driver_b, sequence_file=str(tmp_path / "seq_b")
        )

        sig_a = json.loads(envelope_a.seal(_TIMESTAMP, _PAYLOAD))["s"]
        sig_b = json.loads(envelope_b.seal(_TIMESTAMP, _PAYLOAD))["s"]
        assert sig_a != sig_b

    def test_wire_frame_serialization_is_order_independent(self, tmp_path: Path) -> None:
        """sort_keys=True on the outer frame serialization must produce byte-identical
        wire output for semantically equivalent payloads regardless of insertion order.

        This is distinct from ``test_seal_payload_key_order_does_not_affect_signature``,
        which only verifies the ``"s"`` field.  This test compares the complete raw byte
        arrays emitted by ``seal()``, confirming that the ``"d"`` sub-object is also
        serialized in alphabetical key order in the transmitted frame — not merely in the
        signed preimage — so every byte on the wire is deterministic regardless of how
        the MicroPython allocator ordered the payload dict in memory.

        :type tmp_path: Path
        """
        seq_file = str(tmp_path / ".sovereign_sequence")
        payload_ab: dict = {"a": 1, "b": 2}
        payload_ba: dict = {"b": 2, "a": 1}

        envelope_a = bootstrap_sensor_node(_NODE_ID, _KEY_PATH, sequence_file=seq_file)
        envelope_b = bootstrap_sensor_node(_NODE_ID, _KEY_PATH, sequence_file=seq_file)

        wire_a: bytes = envelope_a.seal(_TIMESTAMP, payload_ab)
        wire_b: bytes = envelope_b.seal(_TIMESTAMP, payload_ba)
        assert wire_a == wire_b

    def test_envelope_recovers_gracefully_from_truncated_empty_sequence_file(
        self, tmp_path: Path
    ) -> None:
        """A zero-byte sequence file left by a mid-write power loss must not crash init.

        Simulates a power interruption that truncated the VFS sequence file to 0 bytes
        before any counter digits were flushed.  The envelope constructor must catch the
        resulting ``ValueError`` from ``int("".strip())``, print a diagnostic, and silently
        reset ``_sequence`` to zero so device initialization continues without raising.

        :type tmp_path: Path
        """
        seq_file = tmp_path / ".sovereign_sequence"
        seq_file.write_bytes(b"")  # 0-byte truncated file from power-loss mid-write

        driver = SoftwareFallbackDriver(_KEY_PATH)
        driver.initialize_hardware()
        envelope = SovereignEnvelope(_NODE_ID, driver, sequence_file=str(seq_file))

        assert envelope._sequence == 0

    def test_sequence_counter_resumes_after_reboot_simulation(
        self, tmp_path: Path
    ) -> None:
        """A fresh envelope bound to an existing sequence file must resume from the
        persisted counter rather than resetting to zero.

        Simulates a device reboot by discarding the pre-reboot envelope instance
        and constructing a new one that shares the same VFS sequence file path.
        After sealing three times pre-reboot (counter reaches 3), the post-reboot
        envelope's first seal must produce q=4, proving that the counter restored
        correctly from flash and that the monotonic replay-protection invariant
        survives a full power cycle.
        """
        seq_file = str(tmp_path / ".sovereign_sequence")

        driver_before = SoftwareFallbackDriver(_KEY_PATH)
        driver_before.initialize_hardware()
        envelope_before = SovereignEnvelope(_NODE_ID, driver_before, sequence_file=seq_file)
        for _ in range(3):
            envelope_before.seal(_TIMESTAMP, _PAYLOAD)

        # Reboot: construct new driver and envelope instances from the persisted state.
        driver_after = SoftwareFallbackDriver(_KEY_PATH)
        driver_after.initialize_hardware()
        envelope_after = SovereignEnvelope(_NODE_ID, driver_after, sequence_file=seq_file)

        parsed = json.loads(envelope_after.seal(_TIMESTAMP, _PAYLOAD))
        assert parsed["q"] == 4
