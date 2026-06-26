# packages/sovereign-edge/src/sovereign_edge/models.py
"""Typed data models for the sovereign-edge ingestion pipeline."""
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class SensorFrame:
    """Deserialized representation of a sovereign-sensor wire envelope.

    Corresponds one-to-one with the seven-key minified JSON frame produced by
    :meth:`~sovereign_sensor.SovereignEnvelope.seal`.  The ``d`` field carries
    the raw sensor observation dict; all other fields are protocol metadata.

    :param v: Protocol version integer (``1`` for the current wire format).
    :type v: int
    :param n: Originating node identifier string.
    :type n: str
    :param t: ISO-8601 observation timestamp string.
    :type t: str
    :param q: Monotonic sequence counter binding the frame to its emission
        position in the node's custody timeline.
    :type q: int
    :param alg: Canonical signing algorithm identifier (e.g. ``"hmac-sha256"``).
    :type alg: str
    :param d: Structured sensor observation payload, wrapped in a
        :class:`~types.MappingProxyType` that enforces shallow read-only protection on
        the top-level envelope dictionary keys; nested mutable values are not frozen.
        The reference itself is also immutable due to ``frozen=True``.
    :type d: MappingProxyType[str, Any]
    :param s: Hex-encoded signature string produced by the sensor's HAL driver.
    :type s: str
    """

    v: int
    n: str
    t: str
    q: int
    alg: str
    d: MappingProxyType[str, Any]
    s: str

    @classmethod
    def from_bytes(cls, raw: bytes) -> "SensorFrame":
        """Deserialize a wire frame from UTF-8 JSON bytes into a typed :class:`SensorFrame`.

        :param raw: Ultra-minified JSON bytes as produced by
            :meth:`~sovereign_sensor.SovereignEnvelope.seal`.
        :type raw: bytes
        :return: A fully populated :class:`SensorFrame` instance.
        :rtype: SensorFrame
        :raises json.JSONDecodeError: If ``raw`` is not valid JSON.
        :raises KeyError: If any mandatory wire frame key is absent from the
            decoded object.
        :raises UnicodeDecodeError: If ``raw`` is not valid UTF-8.
        :raises TypeError: If any field carries a value whose runtime type is
            incompatible with its wire format contract (e.g., a string where an
            integer is required, or a boolean masquerading as an int).
        :raises ValueError: If the ``v`` field is not ``1``; future or unknown
            wire format versions are rejected immediately to prevent silent
            misinterpretation of structurally incompatible envelopes.
        """
        frame: dict[str, Any] = json.loads(raw.decode("utf-8"))
        for _field, _expected_type in (
            ("n", str), ("t", str), ("alg", str), ("s", str), ("d", dict),
        ):
            _val: Any = frame[_field]
            if not isinstance(_val, _expected_type):
                raise TypeError(
                    f"SensorFrame field '{_field}' must be {_expected_type.__name__}, "
                    f"got {type(_val).__name__!r}"
                )
        for _int_field in ("v", "q"):
            _val = frame[_int_field]
            if not isinstance(_val, int) or isinstance(_val, bool):
                raise TypeError(
                    f"SensorFrame field '{_int_field}' must be int, "
                    f"got {type(_val).__name__!r}"
                )
        if frame["v"] != 1:
            raise ValueError(
                f"Unsupported wire format version {frame['v']!r}: "
                "sovereign-edge requires protocol version 1"
            )
        return cls(
            v=frame["v"],
            n=frame["n"],
            t=frame["t"],
            q=frame["q"],
            alg=frame["alg"],
            d=MappingProxyType(frame["d"]),
            s=frame["s"],
        )

    def text_content(self) -> str:
        """Return the canonical text representation of the sensor payload.

        Serializes the ``d`` observation mapping to deterministic, compact
        (``separators=(",", ":")``, no inter-token whitespace), sort-keyed,
        non-ASCII-escaped JSON.  This canonical form is used for two purposes:
        (1) as the sieve-layer input to :func:`~sovereign_sieve.sieve_with_metrics`,
        and (2) as the ``d``-segment of the HMAC-SHA256 preimage during inbound
        signature verification in :meth:`EdgePipeline.process`.  Using the same
        compact separators for both guarantees 100 % string-canonicalization
        alignment with the sensor-side :func:`json.dumps` call that produced the
        original HMAC digest.

        Float values are serialized exactly as received — ``1.0`` is emitted as
        ``1.0``, not coerced to the integer ``1`` — so the edge-side preimage is
        byte-identical to the string the sensor passed to its HMAC-SHA256 digest
        function.  ``dict(self.d)`` converts the :class:`~types.MappingProxyType`
        back to a plain dict before passing to :func:`json.dumps`, whose C encoder
        only serializes native :class:`dict` instances.

        :return: Compact minified JSON string representation of the observation payload.
        :rtype: str
        """
        return json.dumps(
            dict(self.d),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )


@dataclass
class EdgeResult:
    """Structured result produced by a successful :class:`~sovereign_edge.EdgePipeline` processing pass.

    Carries the ledger receipt identifier, the full signed ForensicReceipt
    envelope, the sieve-minimized content string, Prose Tax token telemetry,
    and a flag indicating whether the receipt was queued to the off-grid buffer
    rather than committed to the ledger directly.

    :param payload_hash: The ``payload_hash`` field of the minted
        :class:`~sovereign_core.crypto.ForensicReceipt`, usable as an opaque
        receipt identifier for downstream ledger lookups.
    :type payload_hash: str
    :param receipt: The fully minted :class:`~sovereign_core.crypto.ForensicReceipt`
        dict envelope whose ``signature`` covers ``timestamp``, ``payload_hash``,
        and ``metadata`` atomically.
    :type receipt: dict[str, Any]
    :param sieved_content: The Prose-Tax-minimized string produced from the
        sensor observation payload.
    :type sieved_content: str
    :param raw_token_count: Estimated token count of the original payload before
        sieve minimization.
    :type raw_token_count: int
    :param optimized_token_count: Estimated token count after sieve minimization.
    :type optimized_token_count: int
    :param tax_savings_percentage: Percentage token reduction relative to the
        raw baseline, in the range ``[0.0, 100.0]``.
    :type tax_savings_percentage: float
    :param buffered: ``True`` if the receipt was written to the off-grid JSONL
        buffer because the ledger was unreachable; ``False`` if the receipt was
        committed to the ledger directly.
    :type buffered: bool
    """

    payload_hash: str
    receipt: dict[str, Any]
    sieved_content: str
    raw_token_count: int
    optimized_token_count: int
    tax_savings_percentage: float
    buffered: bool
