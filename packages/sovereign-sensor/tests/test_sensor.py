# packages/sovereign-sensor/tests/test_sensor.py
"""Desktop validation suite for sovereign-sensor Phase 9 architectural skeleton.

Invariants verified across every test:
- bootstrap_sensor_node auto-detects a non-ESP32 desktop environment and
  binds SoftwareFallbackDriver without any explicit caller configuration.
- seal() produces well-formed, ultra-minified JSON bytes.
- The transmission envelope contains exactly the four mandatory keys:
  n (node_id), t (timestamp), d (payload dict), s (signature string).
- Payload round-trips without mutation through the canonicalization path.
- Signature is hex-encoded by the envelope layer (not the driver), guaranteeing
  that all bytes 0x00-0xFF map safely to alphanumeric characters without
  UnicodeDecodeError on constrained MicroPython silicon.
"""
import json

import pytest

from sovereign_sensor import SovereignEnvelope, bootstrap_sensor_node
from sovereign_sensor.drivers.software_fallback import SoftwareFallbackDriver


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
        """Desktop CI environment must bind SoftwareFallbackDriver, not a hardware driver."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        assert isinstance(envelope._driver, SoftwareFallbackDriver)

    def test_software_driver_is_initialized_after_bootstrap(self) -> None:
        """bootstrap_sensor_node must call initialize_hardware() before returning."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        assert envelope._driver._initialized is True


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

    def test_seal_envelope_contains_exactly_four_mandatory_keys(self) -> None:
        """Transmission envelope must contain exactly: n, t, d, s."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert set(parsed.keys()) == {"n", "t", "d", "s"}

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

    def test_seal_payload_round_trips_without_mutation(self) -> None:
        """'d' field must equal the original payload dict after JSON round-trip."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert parsed["d"] == _PAYLOAD

    def test_seal_signature_is_non_empty_ascii_string(self) -> None:
        """'s' field must be a non-empty string decodable as ASCII hex."""
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

    def test_seal_is_deterministic_for_identical_inputs(self) -> None:
        """Identical inputs must produce byte-identical sealed envelopes."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        result_a = envelope.seal(_TIMESTAMP, _PAYLOAD)
        result_b = envelope.seal(_TIMESTAMP, _PAYLOAD)
        assert result_a == result_b

    def test_seal_signature_changes_when_payload_changes(self) -> None:
        """Different payloads must produce distinct signatures."""
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        sig_a = json.loads(envelope.seal(_TIMESTAMP, {"v": 1}))["s"]
        sig_b = json.loads(envelope.seal(_TIMESTAMP, {"v": 2}))["s"]
        assert sig_a != sig_b

    def test_seal_signature_contains_only_valid_hex_characters(self) -> None:
        """'s' field must consist exclusively of lowercase hex characters (0-9, a-f).

        Validates that envelope.py uses binascii.hexlify() on the raw driver
        bytes rather than a bare .decode() call, guaranteeing that high-entropy
        binary output from any future hardware driver cannot trigger a
        UnicodeDecodeError on MicroPython silicon.
        """
        _HEX_ALPHABET: frozenset = frozenset("0123456789abcdef")
        envelope = bootstrap_sensor_node(_NODE_ID, _KEY_PATH)
        parsed = json.loads(envelope.seal(_TIMESTAMP, _PAYLOAD))
        assert all(c in _HEX_ALPHABET for c in parsed["s"])
