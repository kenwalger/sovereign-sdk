# packages/sovereign-core/src/sovereign_core/gateway.py
import asyncio
import re
from typing import Any, Dict, List, Union
from pydantic import BaseModel, Field


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
    (re.compile(r"(#{1,6} [^\n]+)\n\1", re.IGNORECASE), r"\1"),
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
