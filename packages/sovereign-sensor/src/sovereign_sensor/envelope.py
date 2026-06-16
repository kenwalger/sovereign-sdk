# packages/sovereign-sensor/src/sovereign_sensor/envelope.py
"""Tamper-evident transmission envelope for sovereign sensor telemetry.

Canonicalizes, signs, and packs sensor payloads into a versioned, minified
JSON wire format with monotonic sequence-counter replay protection that
survives hardware reboots via VFS-persisted counter state.  The driver
instance carries all key-material state; the envelope instance owns the
per-node sequence counter and its persistence path.
"""
import binascii
import json

from .interface import SovereignCryptoDriver


class SovereignEnvelope:
    """Constructs and seals authenticated sensor transmission envelopes.

    Each instance maintains a monotonic sequence counter that is incremented
    and persisted to the VFS on every ``seal()`` call.  On construction, any
    previously persisted counter is restored from the sequence file, enabling
    the counter to resume monotonically after a hardware reboot rather than
    resetting to zero and opening a replay window.  File-access failures
    (``OSError``) degrade gracefully to a zero counter without raising.
    Corrupt or non-integer file contents — the typical artifact of a mid-write
    power interruption that truncated the flash page before any digits were
    committed — are caught as ``ValueError``, a diagnostic is printed, and the
    counter resets to zero so device initialization completes rather than aborting.

    :param node_id: Immutable identifier for the originating sensor node.
    :type node_id: str
    :param driver: Initialized HAL driver supplying the signing primitive.
    :type driver: SovereignCryptoDriver
    :param sequence_file: VFS path used to persist the monotonic sequence
        counter across reboots.  Defaults to ``".sovereign_sequence"`` in the
        current working directory.
    :type sequence_file: str
    """

    def __init__(
        self,
        node_id: str,
        driver: SovereignCryptoDriver,
        sequence_file: str = ".sovereign_sequence",
    ) -> None:
        self._node_id: str = node_id
        self._driver: SovereignCryptoDriver = driver
        self._sequence_file: str = sequence_file
        self._sequence: int = 0
        try:
            with open(self._sequence_file, "r") as f:
                data: str = f.read()
        except OSError:
            pass
        else:
            try:
                self._sequence = int(data.strip())
            except ValueError:
                # Sequence file is empty or contains non-integer data — most likely a
                # truncated write from a power drop mid-flush.  Reset to zero rather
                # than propagating an exception that would abort device initialization.
                print(
                    f"WARNING: sequence file '{self._sequence_file}' is corrupt or empty; "
                    "resetting counter to 0"
                )
                self._sequence = 0

    def seal(self, timestamp: str, payload: dict) -> bytes:
        """Canonicalize, sign, and serialize a sensor observation into a wire envelope.

        Sealing proceeds in seven deterministic steps:

        1. Internal sequence counter is incremented monotonically and immediately
           persisted to the configured VFS sequence file, binding this observation
           to a unique position in the node's emission history and preventing
           replayed frames from being accepted as fresh — including across hardware
           reboots.  VFS write failures degrade gracefully to RAM-only tracking.
        2. Active algorithm identifier is queried from the driver, embedding
           the signing primitive into the authenticated preimage for protocol agility.
        3. Payload keys are alphabetically sorted and the dict is serialized to
           minified JSON with no inter-token whitespace, guaranteeing an identical
           preimage regardless of key insertion order on any MicroPython target.
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
        # Persist counter to VFS immediately after increment.  The with-block
        # guarantees flush and close before execution continues, satisfying
        # MicroPython's flash-write coherence requirement: a power-cycle at any
        # subsequent point cannot reuse this sequence position.
        try:
            with open(self._sequence_file, "w") as f:
                f.write(str(self._sequence))
        except OSError:
            pass  # Degrade gracefully to RAM-only sequence tracking.
        algo: str = self._driver.algorithm()
        canonical: str = json.dumps(payload, separators=(",", ":"), sort_keys=True)
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
