# packages/sovereign-core/src/sovereign_core/gateway.py
import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict, Union
from pydantic import BaseModel, Field

from .crypto import ForensicReceipt, PublicKeyBundle, SovereignKeyManager


class SessionContext(BaseModel):
    """Ephemeral, stateful execution context shared across sequential tool invocations.

    Maintains a mutable variable store and a monotonically increasing
    ``execution_depth`` counter that acts as a tamper-evident ledger index
    for :class:`~sovereign_core.crypto.ForensicReceipt` sequencing.

    Attributes:
        session_id: Unique string identifier for this execution session.
        variables: Key/value store for inter-tool data dependencies persisted
            across dispatch calls within the same session.
        execution_depth: Running count of dispatch attempts within this
            session.  Incremented unconditionally before each tool call to
            prevent ledger index collisions during retry loops.
    """

    session_id: str
    variables: Dict[str, Any] = Field(default_factory=dict)
    execution_depth: int = 0

    def set(self, key: str, value: Any) -> None:
        """Stores a value under ``key`` in the session variable store.

        Args:
            key: String identifier for the stored value.
            value: Arbitrary value to associate with ``key``.  Overwrites any
                existing value for the same key.
        """
        self.variables[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieves a value from the session variable store.

        Args:
            key: String identifier to look up.
            default: Value returned when ``key`` is absent.  Defaults to
                ``None``.

        Returns:
            The stored value for ``key``, or ``default`` if the key does not
            exist in the variable store.
        """
        return self.variables.get(key, default)

    def increment_depth(self) -> None:
        """Increments the execution depth counter by one.

        Called unconditionally at the start of every
        :meth:`~sovereign_runtime.router.LocalRuntimeRouter.dispatch` call so
        that each attempt — including failed or retried ones — receives a unique
        ledger index, preventing forensic receipt collisions.
        """
        self.execution_depth += 1


class OptimizationReceipt(BaseModel):
    """Immutable record of local prose compression metrics for a single optimization pass.

    Captures the before/after token estimates and the derived savings ratio
    produced by :func:`process_prose_tax`.  Written into the active
    :class:`SessionContext` variable store so that downstream
    :class:`~sovereign_core.crypto.ForensicReceipt` envelopes carry a complete
    provenance trail of every optimization event.

    Attributes:
        raw_token_count: Estimated token count of the original, unprocessed
            payload before any minimization is applied.
        optimized_token_count: Estimated token count of the payload after text
            minimization and filler-phrase stripping.
        tax_savings_percentage: Percentage reduction in token count relative to
            the raw baseline, expressed as a value in the range ``[0.0, 100.0]``.
            Computed as
            ``(raw_token_count - optimized_token_count) / raw_token_count * 100``
            when ``raw_token_count`` is non-zero, otherwise ``0.0``.
    """

    raw_token_count: int
    optimized_token_count: int
    tax_savings_percentage: float


# ---------------------------------------------------------------------------
# Filler patterns applied during text minimization.  Each entry is a
# (compiled_pattern, replacement) pair.  Most patterns substitute the empty
# string; the duplicate-heading pattern substitutes \1 to preserve exactly one
# clean copy of the header rather than erasing both.
# ---------------------------------------------------------------------------
_FILLER_PATTERNS: List[tuple[re.Pattern[str], str]] = [
    # Greeting tokens — grouped inside a non-capturing alternation so that the
    # trailing (?![-\w]) lookahead applies uniformly to every option.  The
    # previous per-token form guarded only "hi", leaving "hello", "hey", and
    # "greetings" unguarded and vulnerable to mangling hyphenated compounds
    # (e.g. "hello-world" → "-world").
    (re.compile(r"\b(?:hi|hello|hey|greetings)\b(?![-\w])", re.IGNORECASE), ""),
    # Hedging adverbs — stripped only when they stand as isolated colloquial
    # filler.  Negative lookahead (?![-\w]) prevents matching when the word is
    # the first component of a hyphenated compound (e.g. "just-in-time",
    # "simply-typed", "basically-correct").  The leading \b guards against
    # embedded substrings (e.g. "adjust", "justified", "unkindly", "probably-not"
    # compound forms) from the other direction.
    (
        re.compile(
            r"\bplease(?![-\w])|\bkindly(?![-\w])|\bjust(?![-\w])|\bsimply(?![-\w])"
            r"|\bactually(?![-\w])|\bbasically(?![-\w])|\bprobably(?![-\w])",
            re.IGNORECASE,
        ),
        "",
    ),
    # Affirmation filler — three sub-patterns with distinct guard structures:
    #   • \bof course\b — phrase-anchored; cannot form a hyphenated compound,
    #     so no lookahead guard is required.
    #   • \b(?:certainly|absolutely)\b(?![-\w]) — grouped inside a non-capturing
    #     alternation so the trailing (?![-\w]) applies uniformly to both tokens.
    #     Prevents "certainly-not" or "absolutely-not" from being mangled into
    #     "-not".
    #   • (?<!make )(?<!be )\bsure\b(?![-\w]) — dual guards: paired fixed-width
    #     lookbehinds protect instructional directives ("make sure to", "be sure
    #     to"); the trailing lookahead protects hyphenated compounds ("sure-fire").
    (re.compile(r"\bof course\b|\b(?:certainly|absolutely)\b(?![-\w])|(?<!make )(?<!be )\bsure\b(?![-\w])", re.IGNORECASE), ""),
    # Preamble phrases — erase only the literal preamble token itself.  The
    # previous trailing .* greedily consumed the entire remainder of the line,
    # silently deleting any sentence content that followed the phrase.  The
    # pattern now terminates at the word boundary after the subject pronoun so
    # that trailing content on the same line is fully preserved.
    (re.compile(r"\bI hope (?:this|that|you)\b", re.IGNORECASE), ""),
    # Degenerate bold/italic marker runs — four or more consecutive asterisks
    # or underscores with no enclosed text (e.g. "****", "_____").
    # The threshold of 4+ preserves all valid Markdown wrappers:
    #   *italic*  **bold**  ***bold-italic***  (1–3 markers each side)
    # The previous (\*{1,3})(\s*\1)+ pattern was overbroad: it matched the
    # closing ** of one span and the opening ** of the next when only
    # whitespace separated them, collapsing "**foo** **bar**" to "**foo**bar**".
    (re.compile(r"\*{4,}|_{4,}"), ""),
    # Duplicate consecutive headings — replace the full match (first + newline +
    # second) with \1 so exactly one clean copy of the heading is preserved.
    # Case-sensitive (no re.IGNORECASE): headings that differ only in
    # capitalisation are distinct and must not be silently collapsed.
    (re.compile(r"(#{1,6} [^\n]+)\n\1"), r"\1"),
]


def _approximate_token_count(text: str) -> int:
    """Estimates the token count of ``text`` using a byte-density heuristic.

    Applies a conservative approximation of one token per four UTF-8 bytes,
    which matches the average token density of the cl100k_base tokenizer across
    mixed English/code content.  This is a local estimate only; it does not
    invoke any external tokenizer or network service.

    Args:
        text: The string whose token count is to be approximated.

    Returns:
        Non-negative integer estimate of the token count.  Returns ``0`` for
        an empty string.
    """
    return max(0, len(text.encode("utf-8")) // 4)


async def process_prose_tax(
    payload: Union[str, List[Dict[str, Any]]],
    context: SessionContext,
) -> tuple[Union[str, List[Dict[str, Any]]], OptimizationReceipt]:
    """Applies the Prose Tax Optimization pass to a raw payload and records the result.

    Executes three sequential phases against ``payload``:

    1. **Text Minimization** — strips conversational filler phrases, greeting
       tokens, hedging preambles, and redundant markdown decoration using a
       compiled pattern library.  When ``payload`` is a message list rather than
       a bare string, minimization is applied to the ``"content"`` field of each
       message dict that carries one.
    2. **Local Token Approximation** — estimates the raw and optimized token
       counts using a byte-density heuristic (UTF-8 bytes ÷ 4) without invoking
       any external tokenizer or network service.
    3. **Ledger Accumulation** — writes the :class:`OptimizationReceipt` metrics
       into ``context.variables`` under the key ``"prose_tax_receipt"`` and
       accumulates the running total token savings under
       ``"prose_tax_total_savings"``.  Both keys are visible to downstream
       :class:`~sovereign_core.crypto.ForensicReceipt` envelopes.

    Args:
        payload: Either a raw text string or a list of message dicts, each
            optionally containing a ``"content"`` key whose string value is
            subject to minimization.
        context: Active :class:`SessionContext` into which optimization metrics
            are written.  Modified in place via :meth:`SessionContext.set`.

    Returns:
        A 2-tuple of ``(minimized_payload, receipt)`` where ``minimized_payload``
        mirrors the structural type of the input: a cleansed ``str`` when
        ``payload`` is a string, or a ``List[Dict[str, Any]]`` with each
        ``"content"`` field individually minimized when ``payload`` is a message
        list (all other dict keys and messages without a string ``"content"``
        are forwarded unmodified).  ``receipt`` is an
        :class:`OptimizationReceipt` capturing ``raw_token_count``,
        ``optimized_token_count``, and ``tax_savings_percentage`` for this
        optimization pass.

    Raises:
        TypeError: If ``payload`` is neither a ``str`` nor a ``list``.
    """
    if not isinstance(payload, (str, list)):
        raise TypeError(
            f"process_prose_tax expected str or list, got {type(payload).__name__!r}."
        )

    await asyncio.sleep(0)  # yield to event loop before CPU-bound pass

    # ------------------------------------------------------------------
    # Phase 1: Text Minimization
    # Strip conversational filler, greetings, hedging preambles, and
    # redundant markdown syntax from the payload.
    # ------------------------------------------------------------------
    if isinstance(payload, str):
        raw_text: str = payload
        minimized_text: str = raw_text
        for pattern, repl in _FILLER_PATTERNS:
            minimized_text = pattern.sub(repl, minimized_text)
        minimized_text = re.sub(r" {2,}", " ", minimized_text)
        minimized_text = re.sub(r" ([,.:;!?])", r"\1", minimized_text)
        minimized_text = minimized_text.strip()
    else:
        raw_parts: List[str] = [
            msg["content"] for msg in payload if isinstance(msg.get("content"), str)
        ]
        raw_text = " ".join(raw_parts)
        minimized_messages: List[Dict[str, Any]] = []
        minimized_parts: List[str] = []
        for msg in payload:
            if isinstance(msg.get("content"), str):
                minimized = msg["content"]
                for pattern, repl in _FILLER_PATTERNS:
                    minimized = pattern.sub(repl, minimized)
                minimized = re.sub(r" {2,}", " ", minimized)
                minimized = re.sub(r" ([,.:;!?])", r"\1", minimized)
                minimized = minimized.strip()
                minimized_parts.append(minimized)
                minimized_messages.append({**msg, "content": minimized})
            else:
                minimized_messages.append(msg)
        minimized_text = " ".join(minimized_parts)

    # ------------------------------------------------------------------
    # Phase 2: Local Token Approximation
    # Estimate before/after token density using the byte-density heuristic.
    # No external tokenizer or network call is made.
    # ------------------------------------------------------------------
    raw_count: int = _approximate_token_count(raw_text)
    optimized_count: int = _approximate_token_count(minimized_text)
    savings_pct: float = (
        (raw_count - optimized_count) / raw_count * 100.0 if raw_count > 0 else 0.0
    )

    receipt = OptimizationReceipt(
        raw_token_count=raw_count,
        optimized_token_count=optimized_count,
        tax_savings_percentage=round(savings_pct, 4),
    )

    # ------------------------------------------------------------------
    # Phase 3: Ledger Accumulation
    # Write the receipt and running savings total into the session context
    # so downstream ForensicReceipt envelopes carry a complete audit trail.
    # ------------------------------------------------------------------
    context.set("prose_tax_receipt", receipt.model_dump())
    prior_savings: int = context.get("prose_tax_total_savings", 0)
    context.set("prose_tax_total_savings", prior_savings + (raw_count - optimized_count))

    return (minimized_messages if isinstance(payload, list) else minimized_text), receipt


class SieveAndSignResult(TypedDict):
    """Structured result returned by :meth:`SovereignGateway.sieve_and_sign`.

    Attributes:
        content: The purified string produced by the Prose Tax sieve pass.
        receipt: The cryptographically sealed :class:`~sovereign_core.crypto.ForensicReceipt`
            envelope covering ``content`` and any accumulated Prose Tax telemetry.
    """

    content: str
    receipt: ForensicReceipt


class SovereignBoundaryResponse(BaseModel):
    """Structured Pydantic model returned by :meth:`SovereignGateway.sieve_and_sign`.

    Provides attribute-style access to both the purified content string and the
    cryptographically sealed receipt in a single, validated Python object, avoiding
    the error-prone key-lookup patterns inherent in raw dict returns.

    Attributes:
        content: The minimized string produced by the Prose Tax sieve pass —
            conversational filler stripped, whitespace normalized.
        receipt: The Ed25519-signed :class:`~sovereign_core.crypto.ForensicReceipt`
            envelope covering ``content`` and any accumulated Prose Tax telemetry.
            Access individual fields with standard dict syntax, e.g.
            ``response.receipt["payload_hash"]``.
    """

    content: str
    receipt: ForensicReceipt


class SovereignGateway:
    """High-level developer interface for the Sovereign Systems SDK.

    Wraps the Prose Tax sieve and cryptographic signing primitives behind a
    clean four-method API so application code never touches the lower-level
    ``process_prose_tax`` or ``SovereignKeyManager`` APIs directly:
    :meth:`sieve`, :meth:`sign`, :meth:`sieve_and_sign`, and
    :meth:`export_public_key`.

    Args:
        signing_key: Path to the private key PEM file used for signing.  The
            parent directory is passed to :class:`~sovereign_core.crypto.SovereignKeyManager`
            as the ``key_dir``.  Defaults to ``.keys/sovereign_identity.pem``.
    """

    def __init__(self, signing_key: str = ".keys/sovereign_identity.pem") -> None:
        key_path = Path(signing_key).resolve()
        os.makedirs(key_path.parent, exist_ok=True)
        self._key_manager = SovereignKeyManager(key_dir=key_path.parent)
        # Override the key manager's default filename so a custom signing_key
        # path (e.g. "vault/node-alpha.pem") is preserved exactly rather than
        # silently replaced with the default "sovereign_identity.pem".
        self._key_manager.private_key_path = key_path
        self._key_manager.public_key_path = key_path.with_suffix(".pub")
        self._session = SessionContext(session_id="sovereign-gateway")

    async def sieve(self, text_payload: str) -> str:
        """Strip conversational boilerplate, normalize whitespace, and return purified text.

        Delegates to :func:`process_prose_tax` for filler-phrase removal and
        whitespace normalization.  The underlying optimization metrics are
        accumulated in the gateway's internal :class:`SessionContext`.

        Args:
            text_payload: Raw string to be cleaned.

        Returns:
            Minimized string with conversational filler removed and whitespace
            normalized.
        """
        clean, _ = await process_prose_tax(text_payload, self._session)
        return clean if isinstance(clean, str) else str(clean)

    def sign(
        self,
        clean_context: str,
        *,
        prose_tax_receipt: Optional["OptimizationReceipt"] = None,
        total_tokens_saved: Optional[int] = None,
    ) -> "ForensicReceipt":
        """Cryptographically seal a clean payload and return a ForensicReceipt envelope.

        Wraps :meth:`~sovereign_core.crypto.SovereignKeyManager.generate_receipt`
        to produce a fully minted, Ed25519-signed :class:`~sovereign_core.crypto.ForensicReceipt`
        that can be independently verified with
        :meth:`~sovereign_core.crypto.SovereignKeyManager.verify_receipt`.

        Two metric-injection paths are supported:

        * **Explicit receipt (concurrent-safe)**: when ``prose_tax_receipt`` is
          supplied, its values are used directly and no shared session state is
          read.  This is the path taken by :meth:`sieve_and_sign` to prevent
          concurrent requests on the same gateway instance from clobbering each
          other's metrics.
        * **Session fallback (sequential two-step workflow)**: when
          ``prose_tax_receipt`` is ``None``, the Prose Tax metrics written to
          the internal :class:`SessionContext` by a prior :meth:`sieve` call are
          used.  Appropriate only when a single request issues ``sieve()`` and
          ``sign()`` sequentially without interleaved concurrent calls.

        Args:
            clean_context: Purified string payload (typically the output of
                :meth:`sieve`) to be hashed and sealed.
            prose_tax_receipt: Optional :class:`OptimizationReceipt` captured
                from a local :func:`process_prose_tax` call.  When provided,
                its fields are embedded directly in the receipt metadata without
                touching shared session state.
            total_tokens_saved: Optional pre-computed cumulative savings total
                to embed as ``"total_tokens_saved"`` in the receipt metadata.
                Passed by :meth:`sieve_and_sign` after reading the updated
                session accumulator so that the one-shot macro and the two-step
                sequential workflow expose identical cumulative semantics.
                Defaults to the per-call delta (``raw - opt``) when ``None``
                and ``prose_tax_receipt`` is supplied.

        Returns:
            A :class:`~sovereign_core.crypto.ForensicReceipt` TypedDict whose
            ``signature`` covers ``timestamp``, ``payload_hash``, and
            ``metadata`` atomically.  The ``metadata`` dict always contains
            ``"source": "SovereignGateway"`` and, when Prose Tax metrics are
            available, a ``"prose_tax_summary"`` sub-dict with
            ``raw_token_count``, ``optimized_token_count``,
            ``tokens_eliminated``, ``tax_savings_percentage``, and
            ``total_tokens_saved``.
        """
        payload: Dict[str, Any] = {"content": clean_context}
        metadata: Dict[str, Any] = {"source": "SovereignGateway"}

        if prose_tax_receipt is not None:
            # Concurrent-safe path: use only the locally captured receipt and
            # the explicitly passed cumulative total — no shared session reads.
            raw: int = prose_tax_receipt.raw_token_count
            opt: int = prose_tax_receipt.optimized_token_count
            metadata["prose_tax_summary"] = {
                "raw_token_count": raw,
                "optimized_token_count": opt,
                "tokens_eliminated": raw - opt,
                "tax_savings_percentage": prose_tax_receipt.tax_savings_percentage,
                "total_tokens_saved": total_tokens_saved if total_tokens_saved is not None else raw - opt,
            }
        else:
            # Sequential two-step path: read from shared session state.
            tax_receipt = self._session.get("prose_tax_receipt")
            if tax_receipt is not None:
                raw = tax_receipt.get("raw_token_count", 0)
                opt = tax_receipt.get("optimized_token_count", 0)
                metadata["prose_tax_summary"] = {
                    "raw_token_count": raw,
                    "optimized_token_count": opt,
                    "tokens_eliminated": raw - opt,
                    "tax_savings_percentage": tax_receipt.get("tax_savings_percentage", 0.0),
                    "total_tokens_saved": self._session.get("prose_tax_total_savings", 0),
                }

        return self._key_manager.generate_receipt(payload, metadata=metadata)

    async def sieve_and_sign(self, text_payload: str) -> SovereignBoundaryResponse:
        """One-shot macro: sieve the payload and cryptographically seal it in a single call.

        Equivalent to calling ``await self.sieve(text_payload)`` followed
        immediately by ``self.sign(clean_context)``, but returns both the
        purified content string and the sealed :class:`~sovereign_core.crypto.ForensicReceipt`
        together in a validated :class:`SovereignBoundaryResponse` model.  The
        Prose Tax telemetry accumulated during the sieve pass is automatically
        fused into the receipt metadata before signing (see :meth:`sign` for the
        full telemetry contract).

        Args:
            text_payload: Raw string to be cleaned and sealed.

        Returns:
            A :class:`SovereignBoundaryResponse` Pydantic model with two attributes:

            - ``.content``: the minimized string after filler removal and
              whitespace normalization.
            - ``.receipt``: the Ed25519-signed :class:`~sovereign_core.crypto.ForensicReceipt`
              dict covering ``content`` and the Prose Tax telemetry summary.
        """
        # Call process_prose_tax directly so the OptimizationReceipt is captured
        # in this coroutine's local scope.  After it returns, the session
        # accumulator already holds the updated cumulative total (prior savings
        # + this call's delta); read it once here and forward both values
        # explicitly to sign() so the one-shot macro and the two-step sequential
        # workflow expose identical total_tokens_saved semantics without any
        # further shared-state reads inside sign().
        clean, tax_receipt = await process_prose_tax(text_payload, self._session)
        content = clean if isinstance(clean, str) else str(clean)
        cumulative_saved: int = self._session.get("prose_tax_total_savings", 0)
        receipt = self.sign(
            content,
            prose_tax_receipt=tax_receipt,
            total_tokens_saved=cumulative_saved,
        )
        return SovereignBoundaryResponse(content=content, receipt=receipt)

    def export_public_key_bundle(self, node_id: str | None = None) -> PublicKeyBundle:
        """Return a structured :class:`~sovereign_core.crypto.PublicKeyBundle` for this gateway identity.

        The bundle carries the base64-encoded public key, a self-signed
        :class:`~sovereign_core.crypto.ForensicReceipt` attestation (signed by
        the node's private key over a payload that includes the public key
        itself, proving private-key ownership), a UTC issuance timestamp, and an
        optional human-readable node label.

        Args:
            node_id: Optional string label for the issuing node, e.g.
                ``"node-alpha"`` or a UUID.  Defaults to ``None``.

        Returns:
            A :class:`~sovereign_core.crypto.PublicKeyBundle` Pydantic model
            that can be serialised to JSON via :meth:`model_dump_json` and
            shared with downstream verifiers.

        Raises:
            RuntimeError: If ``SOVEREIGN_NODE_SECRET`` is not set in the
                environment and no keypair has been pre-loaded.
        """
        if not self._key_manager.has_identity:
            self._key_manager.load_or_generate_keypair()
        pub_key = self._key_manager.public_key
        # issued_at is captured before generate_receipt() so it is included in
        # the signed metadata manifest and fully covered by the Ed25519 signature.
        issued_at = datetime.now(timezone.utc).isoformat()
        attestation_payload: Dict[str, Any] = {
            "public_key": pub_key,
            "type": "key_attestation",
        }
        attestation = self._key_manager.generate_receipt(
            payload=attestation_payload,
            metadata={
                "issued_at": issued_at,
                "purpose": "key_attestation",
                "source": "PublicKeyBundle",
            },
        )
        return PublicKeyBundle(
            public_key=pub_key,
            attestation=dict(attestation),
            issued_at=issued_at,
            node_id=node_id,
        )

    def save_public_key_bundle(self, path: str, node_id: str | None = None) -> None:
        """Serialise and write the public key bundle to disk as a JSON file.

        Calls :meth:`export_public_key_bundle` and writes the Pydantic v2
        ``model_dump_json()`` output to ``path``.  The file is encoded as
        UTF-8.  The parent directory must already exist; no ``makedirs`` is
        performed.

        Args:
            path: Destination file path for the JSON bundle.
            node_id: Optional node label forwarded to
                :meth:`export_public_key_bundle`.  Defaults to ``None``.

        Raises:
            RuntimeError: If ``SOVEREIGN_NODE_SECRET`` is not set.
            OSError: If ``path`` cannot be written (e.g. parent directory does
                not exist or insufficient permissions).
        """
        bundle = self.export_public_key_bundle(node_id=node_id)
        Path(path).write_text(bundle.model_dump_json(), encoding="utf-8")

    def export_public_key(self) -> str:
        """Return the base64-encoded Ed25519 public verification key for this gateway identity.

        Triggers keypair loading or generation if the underlying
        :class:`~sovereign_core.crypto.SovereignKeyManager` has not yet been
        initialised (i.e. on the first call before any :meth:`sign` or
        :meth:`sieve_and_sign` invocation).  The returned string is the canonical
        public key string that appears in the ``"public_key"`` field of every
        :class:`~sovereign_core.crypto.ForensicReceipt` produced by this gateway
        instance, enabling out-of-band receipt verification by third parties who
        hold only the public key.

        Returns:
            Base64-encoded raw 32-byte Ed25519 public key string, identical to
            ``receipt["public_key"]`` in all receipts minted by this instance.

        Raises:
            RuntimeError: If ``SOVEREIGN_NODE_SECRET`` is not set in the
                environment and no keypair has been pre-loaded.
        """
        if not self._key_manager.has_identity:
            self._key_manager.load_or_generate_keypair()
        return self._key_manager.public_key
