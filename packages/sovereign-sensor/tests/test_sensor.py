# packages/sovereign-sensor/tests/test_sensor.py
"""Desktop validation suite for sovereign-sensor Phase 9.

Invariants verified across every test:
- bootstrap_sensor_node auto-detects a non-ESP32 desktop environment and
  binds SoftwareFallbackDriver without any explicit caller configuration.
- seal() produces well-formed, ultra-minified JSON bytes carrying exactly
  seven keys: v (protocol version), n (node_id), t (timestamp), q (sequence
  counter), alg (algorithm identifier), d (payload dict), s (signature string).
- Sequence counter increments monotonically on every seal() call, providing
  replay protection by binding each frame to a unique position in the node's
  emission history.
- Signature is hex-encoded by the envelope layer (not the driver), guaranteeing
  that all bytes 0x00-0xFF map safely to alphanumeric characters without
  UnicodeDecodeError on constrained MicroPython silicon.
"""
import json

from sovereign_sensor import SovereignEnvelope, bootstrap_sensor_node


_NODE_ID = "node-sensor-001"
_KEY_PATH = "/nonexistent/key.pem"
_TIMESTAMP = "2026-06-16T00:00:00Z"
_PAYLOAD: dict = {"sensor": "temperature", "value": 42, "unit": "C"}


class TestBootstrap:
    """Verify platform detection and driver selection logic."""

    def test_returns_sovereign_envelope_instance(self) -> None:
        """bootstrap_sensor_node must return a SovereignEnvelope on any non-ESP32 host."""
        result = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        assert isinstance(result, SovereignEnvelope)

    def test_non_esp32_platform_selects_software_fallback(self) -> None:
        """Desktop CI must bind SoftwareFallbackDriver, verified via the public algorithm() contract.

        The software fallback declares "hmac-sha256" via its algorithm() method,
        which is surfaced in the sealed envelope's "alg" field.  Asserting this
        value confirms driver selection through the public HAL interface without
        piercing private envelope attributes.
        """
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert parsed["alg"] == "hmac-sha256"

    def test_software_driver_is_initialized_after_bootstrap(self) -> None:
        """bootstrap_sensor_node must call initialize_hardware() before returning.

        Verified by confirming that seal() completes without exception on the
        first call: a driver whose initialize_hardware() was never invoked would
        be unable to service sign() requests.
        """
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        result = envelope.seal(_TIMESTAMP, _PAYLOAD)
        assert isinstance(result, bytes)


class TestEnvelopeSeal:
    """Verify structural and semantic correctness of sealed envelopes."""

    def test_seal_returns_bytes(self) -> None:
        """seal() must return raw bytes suitable for wire transmission."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        result = envelope.seal(_TIMESTAMP, _PAYLOAD)
        assert isinstance(result, bytes)

    def test_seal_produces_valid_json(self) -> None:
        """Sealed bytes must parse as valid JSON without error."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        result = envelope.seal(_TIMESTAMP, _PAYLOAD)
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_seal_envelope_contains_exactly_seven_mandatory_keys(self) -> None:
        """Transmission envelope must contain exactly: v, n, t, q, alg, d, s."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert set(parsed.keys()) == {"v", "n", "t", "q", "alg", "d", "s"}

    def test_seal_protocol_version_is_integer_one(self) -> None:
        """'v' field must be the integer 1, not the string '1'."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert parsed["v"] == 1
        assert isinstance(parsed["v"], int)

    def test_seal_node_id_field_matches_constructor(self) -> None:
        """'n' field must be the node_id supplied to bootstrap_sensor_node."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert parsed["n"] == _NODE_ID

    def test_seal_timestamp_field_is_preserved_verbatim(self) -> None:
        """'t' field must be the exact timestamp string passed to seal()."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert parsed["t"] == _TIMESTAMP

    def test_seal_sequence_counter_starts_at_one(self) -> None:
        """First seal() on a fresh envelope must produce q=1."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert parsed["q"] == 1

    def test_seal_sequence_counter_increments_monotonically(self) -> None:
        """Consecutive seal() calls must produce strictly increasing 'q' values."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        q_values = [
            json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))["q"]
            for _ in range(3)
        ]
        assert q_values == [1, 2, 3]

    def test_seal_algorithm_field_matches_software_fallback_driver(self) -> None:
        """'alg' field must carry the driver's declared algorithm identifier."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert parsed["alg"] == "hmac-sha256"

    def test_seal_payload_round_trips_without_mutation(self) -> None:
        """'d' field must equal the original payload dict after JSON round-trip."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert parsed["d"] == _PAYLOAD

    def test_seal_signature_is_non_empty_ascii_string(self) -> None:
        """'s' field must be a non-empty string decodable as ASCII."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert isinstance(parsed["s"], str)
        assert len(parsed["s"]) > 0
        parsed["s"].encode("ascii")  # raises if non-ASCII

    def test_seal_signature_is_sha256_hex_length(self) -> None:
        """Software fallback signature must be a 64-character SHA-256 hex digest."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert len(parsed["s"]) == 64

    def test_seal_output_is_minified_json(self) -> None:
        """Output bytes must not contain whitespace after separators (ultra-minified)."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        result = envelope.seal(_TIMESTAMP, _PAYLOAD)
        assert b": " not in result
        assert b", " not in result

    def test_seal_is_deterministic_across_independent_instances(self) -> None:
        """Two fresh envelope instances must produce byte-identical first seals for identical inputs.

        Determinism holds when the sequence counter, node_id, timestamp, payload,
        and algorithm are all identical.  Each fresh instance starts at q=0, so the
        first seal on both instances emits q=1 and produces the same preimage and
        therefore the same signature.
        """
        envelope_a = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        envelope_b = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        assert envelope_a.seal(_TIMESTAMP, _PAYLOAD) == envelope_b.seal(_TIMESTAMP, _PAYLOAD)

    def test_seal_signature_changes_when_payload_changes(self) -> None:
        """Distinct payloads at the same sequence position must produce distinct signatures.

        Two independent instances are used so that sequence number (q=1 for both)
        and all other preimage fields are held constant, isolating payload as the
        sole independent variable.
        """
        envelope_a = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        envelope_b = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        sig_a = json.loads(envelope_a.seal(_TIMESTAMP, {"v": 1}))["s"]
        sig_b = json.loads(envelope_b.seal(_TIMESTAMP, {"v": 2}))["s"]
        assert sig_a != sig_b

    def test_seal_signature_contains_only_valid_hex_characters(self) -> None:
        """'s' field must consist exclusively of lowercase hex characters (0-9, a-f).

        Validates that envelope.py applies binascii.hexlify() to the raw driver
        bytes rather than a bare .decode() call, guaranteeing that high-entropy
        binary output from any future hardware driver cannot trigger a
        UnicodeDecodeError on MicroPython silicon.
        """
        _HEX_ALPHABET: frozenset = frozenset("0123456789abcdef")
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert all(c in _HEX_ALPHABET for c in parsed["s"])
