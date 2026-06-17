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
    A successfully parsed integer value is additionally clamped to zero if negative,
    preventing an adversarially written or filesystem-corrupted negative counter from
    producing ``q < 0`` wire frames that violate the monotonic custody invariant.

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
            else:
                # Clamp adversarially written or filesystem-corrupted negative values
                # to zero so the monotonic q ≥ 1 invariant is preserved on the first seal().
                if self._sequence < 0:
                    self._sequence = 0

    def seal(self, timestamp: str, payload: dict) -> bytes:
        """Canonicalize, sign, and serialize a sensor observation into a wire envelope.

        Sealing proceeds in seven deterministic steps:

        1. Monotonic sequence index is computed transiently as ``self._sequence + 1``
           and bound into the preimage.  The in-memory counter and the VFS sequence
           file are not advanced until step 5 returns successfully, guaranteeing that
           a ``sign()`` failure never consumes a sequence position or introduces a gap
           in the on-disk custody timeline.
        2. Active algorithm identifier is queried from the driver, embedding
           the signing primitive into the authenticated preimage for protocol agility.
        3. Payload keys are alphabetically sorted and the dict is serialized to
           minified JSON with ``ensure_ascii=False`` and no inter-token whitespace.
           Raw UTF-8 string bytes in the canonical form guarantee identical preimage
           bytes across CPython and bare-metal MicroPython for any Unicode payload,
           eliminating the ``\\uXXXX``-vs-raw-UTF-8 split-brain divergence that
           would cause cross-platform signature verification to fail on non-ASCII
           payloads.
        4. ``node_id``, ``timestamp``, and the algorithm identifier are each encoded
           to UTF-8 byte arrays independently.  Each is prefixed with its UTF-8 byte
           count (not its Unicode character count) and the three prefixed strings are
           joined into the versioned preimage:
           ``1|{len(node_bytes)}:{node_id}|{len(time_bytes)}:{timestamp}|sequence|{len(algo_bytes)}:{algorithm}|canonical_payload``.
           Uniform byte-count prefixing across all three variable-length string fields
           closes the preimage delimiter injection surface in its entirety: without
           prefixes, any field value containing ``|`` collapses with adjacent fields
           into an ambiguous pipe-joined byte sequence, enabling cross-identity or
           cross-algorithm signature reuse.  Byte-count prefixes make every field
           boundary unambiguous regardless of field content, including multi-byte UTF-8
           characters and algorithm identifiers that embed separator characters.
        5. Preimage bytes traverse the driver's signing boundary, returning
           raw binary output from the underlying cryptographic primitive.
        6. The in-memory sequence counter is advanced to the transient value from
           step 1 and immediately flushed to the configured VFS sequence file, binding
           this observation to a unique position in the node's emission history.
           Execution reaches this step only when ``sign()`` returns successfully.
           VFS write failures degrade gracefully to RAM-only tracking.
        7. Raw signature bytes are hex-encoded via ``binascii.hexlify``; all fields
           are packed into a versioned dict and serialized to ultra-minified UTF-8
           JSON bytes with ``sort_keys=True`` and ``ensure_ascii=False``, freezing
           the alphabetical key sequence and raw UTF-8 encoding in the wire frame
           independently of MicroPython allocator-driven insertion order.

        :param timestamp: ISO-8601 observation timestamp string.
        :type timestamp: str
        :param payload: Structured sensor observation data.
        :type payload: dict
        :return: Ultra-minified JSON bytes containing keys ``v``, ``n``, ``t``,
                 ``q``, ``alg``, ``d``, ``s``.
        :rtype: bytes
        """
        next_sequence: int = self._sequence + 1
        algo: str = self._driver.algorithm()
        canonical: str = json.dumps(
            payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False
        )
        node_bytes: bytes = self._node_id.encode("utf-8")
        time_bytes: bytes = timestamp.encode("utf-8")
        algo_bytes: bytes = algo.encode("utf-8")
        node_prefix: str = f"{len(node_bytes)}:{self._node_id}"
        time_prefix: str = f"{len(time_bytes)}:{timestamp}"
        algo_prefix: str = f"{len(algo_bytes)}:{algo}"
        preimage: bytes = (
            f"1|{node_prefix}|{time_prefix}|{next_sequence}|{algo_prefix}|{canonical}"
            .encode("utf-8")
        )
        sig_bytes: bytes = self._driver.sign(preimage)
        # Commit sequence state only after sign() returns successfully.  A sign()
        # failure propagates without advancing the counter, so no sequence position
        # is silently consumed and the on-disk custody timeline has no gaps.
        self._sequence = next_sequence
        try:
            with open(self._sequence_file, "w") as f:
                f.write(str(self._sequence))
        except OSError:
            pass  # Degrade gracefully to RAM-only sequence tracking.
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
        return json.dumps(
            frame, separators=(",", ":"), sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
