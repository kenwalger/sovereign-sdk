# packages/sovereign-sensor/src/sovereign_sensor/envelope.py
"""Tamper-evident transmission envelope for sovereign sensor telemetry.

Canonicalizes, signs, and packs sensor payloads into a versioned, minified
JSON wire format with monotonic sequence-counter replay protection.  The
driver instance carries all key-material state; the envelope instance owns
the per-node sequence monotonic counter.
"""
import binascii
import json

from .interface import SovereignCryptoDriver


class SovereignEnvelope:
    """Constructs and seals authenticated sensor transmission envelopes.

    Each instance maintains an independent monotonic sequence counter that
    is incremented on every ``seal()`` call, binding observations to a
    strict ordering that prevents replay attacks.

    :param node_id: Immutable identifier for the originating sensor node.
    :type node_id: str
    :param driver: Initialized HAL driver supplying the signing primitive.
    :type driver: SovereignCryptoDriver
    """

    def __init__(self, node_id: str, driver: SovereignCryptoDriver) -> None:
        self._node_id: str = node_id
        self._driver: SovereignCryptoDriver = driver
        self._sequence: int = 0

    def seal(self, timestamp: str, payload: dict) -> bytes:
        """Canonicalize, sign, and serialize a sensor observation into a wire envelope.

        Sealing proceeds in seven deterministic steps:

        1. Internal sequence counter is incremented monotonically, binding
           this observation to a unique position in the node's emission history
           and preventing replayed frames from being accepted as fresh.
        2. Active algorithm identifier is queried from the driver, embedding
           the signing primitive into the authenticated preimage for protocol agility.
        3. Payload is canonicalized to minified JSON with no inter-token whitespace.
        4. A versioned, hardened preimage string is constructed as
           ``1|node_id|timestamp|sequence|algorithm|canonical_payload``,
           binding the protocol version, identity, time, ordering, and algorithm
           into a single signed surface.
        5. Preimage bytes traverse the driver's signing boundary, returning
           raw binary output from the underlying cryptographic primitive.
        6. Raw signature bytes are hex-encoded via ``binascii.hexlify``,
           guaranteeing all byte values ``0x00–0xFF`` map safely to the
           alphanumeric characters ``0–9``, ``a–f`` without ``UnicodeDecodeError``
           on constrained MicroPython runtimes.
        7. All fields are packed into a versioned transmission dict and
           serialized to ultra-minified UTF-8 JSON bytes.

        :param timestamp: ISO-8601 observation timestamp string.
        :type timestamp: str
        :param payload: Structured sensor observation data.
        :type payload: dict
        :return: Ultra-minified JSON bytes containing keys ``v``, ``n``, ``t``,
                 ``q``, ``alg``, ``d``, ``s``.
        :rtype: bytes
        """
        self._sequence += 1
        algo: str = self._driver.algorithm()
        canonical: str = json.dumps(payload, separators=(",", ":"))
        preimage: bytes = (
            f"1|{self._node_id}|{timestamp}|{self._sequence}|{algo}|{canonical}"
            .encode("utf-8")
        )
        sig_bytes: bytes = self._driver.sign(preimage)
        signature_string: str = binascii.hexlify(sig_bytes).decode("utf-8")
        frame: dict = {
            "v": 1,
            "n": self._node_id,
            "t": timestamp,
            "q": self._sequence,
            "alg": algo,
            "d": payload,
            "s": signature_string,
        }
        return json.dumps(frame, separators=(",", ":")).encode("utf-8")
