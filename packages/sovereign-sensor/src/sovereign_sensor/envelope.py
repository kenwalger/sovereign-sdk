# packages/sovereign-sensor/src/sovereign_sensor/envelope.py
"""Tamper-evident transmission envelope for sovereign sensor telemetry.

Canonicalizes, signs, and packs sensor payloads into a minified JSON wire
format suitable for constrained transport channels.  All operations are
stateless with respect to the payload; the driver instance carries
all key-material state.
"""
import binascii
import json

from .interface import SovereignCryptoDriver


class SovereignEnvelope:
    """Constructs and seals authenticated sensor transmission envelopes.

    :param node_id: Immutable identifier for the originating sensor node.
    :type node_id: str
    :param driver: Initialized HAL driver supplying the signing primitive.
    :type driver: SovereignCryptoDriver
    """

    def __init__(self, node_id: str, driver: SovereignCryptoDriver) -> None:
        self._node_id: str = node_id
        self._driver: SovereignCryptoDriver = driver

    def seal(self, timestamp: str, payload: dict) -> bytes:
        """Canonicalize, sign, and serialize a sensor observation into a wire envelope.

        Sealing proceeds in four deterministic steps:

        1. Payload is canonicalized to minified JSON with no whitespace.
        2. A strict preimage is assembled as ``node_id|timestamp|canonical_payload``.
        3. The preimage traverses the driver's signing boundary, returning raw bytes.
        4. Raw signature bytes are hex-encoded via ``binascii.hexlify`` before
           JSON serialization, guaranteeing all bytes ``0x00–0xFF`` map safely to the
           alphanumeric characters ``0–9``, ``a–f`` without UnicodeDecodeError on
           constrained MicroPython runtimes.
        5. All fields are packed into a compact transmission dict and serialized.

        :param timestamp: ISO-8601 observation timestamp string.
        :type timestamp: str
        :param payload: Structured sensor observation data.
        :type payload: dict
        :return: Ultra-minified JSON bytes containing keys ``n``, ``t``, ``d``, ``s``.
        :rtype: bytes
        """
        canonical: str = json.dumps(payload, separators=(",", ":"))
        preimage: bytes = f"{self._node_id}|{timestamp}|{canonical}".encode("utf-8")
        sig_bytes: bytes = self._driver.sign(preimage)
        signature_string: str = binascii.hexlify(sig_bytes).decode("utf-8")
        envelope: dict = {
            "n": self._node_id,
            "t": timestamp,
            "d": payload,
            "s": signature_string,
        }
        return json.dumps(envelope, separators=(",", ":")).encode("utf-8")
