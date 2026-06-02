# packages/sovereign-sieve/src/sovereign_sieve/sieve.py
import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Filler patterns applied during text minimization.  Each entry is a
# (compiled_pattern, replacement) pair.  Most patterns substitute the empty
# string; the duplicate-heading pattern substitutes \1 to preserve exactly one
# clean copy of the header rather than erasing both.
# ---------------------------------------------------------------------------
_FILLER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Greeting tokens — grouped inside a non-capturing alternation so that the
    # trailing (?![-\w]) lookahead applies uniformly to every option.
    (re.compile(r"\b(?:hi|hello|hey|greetings)\b(?![-\w])", re.IGNORECASE), ""),
    # Hedging adverbs — stripped only when they stand as isolated colloquial
    # filler.  Negative lookahead (?![-\w]) prevents matching when the word is
    # the first component of a hyphenated compound (e.g. "just-in-time").
    (
        re.compile(
            r"\bplease(?![-\w])|\bkindly(?![-\w])|\bjust(?![-\w])|\bsimply(?![-\w])"
            r"|\bactually(?![-\w])|\bbasically(?![-\w])|\bprobably(?![-\w])",
            re.IGNORECASE,
        ),
        "",
    ),
    # Affirmation filler — three sub-patterns with distinct guard structures:
    #   • \bof course\b — phrase-anchored; cannot form a hyphenated compound.
    #   • \b(?:certainly|absolutely)\b(?![-\w]) — grouped so the trailing guard
    #     applies uniformly to both tokens.
    #   • (?<!make )(?<!be )\bsure\b(?![-\w]) — dual lookbehinds protect
    #     instructional directives ("make sure to", "be sure to").
    (re.compile(r"\bof course\b|\b(?:certainly|absolutely)\b(?![-\w])|(?<!make )(?<!be )\bsure\b(?![-\w])", re.IGNORECASE), ""),
    # Preamble phrases — erase only the literal preamble token itself.
    (re.compile(r"\bI hope (?:this|that|you)\b", re.IGNORECASE), ""),
    # Degenerate bold/italic marker runs — four or more consecutive asterisks
    # or underscores with no enclosed text.  Preserves all valid Markdown wrappers.
    (re.compile(r"\*{4,}|_{4,}"), ""),
    # Duplicate consecutive headings — replace the full match (first + newline +
    # second) with \1 so exactly one clean copy of the heading is preserved.
    # Case-sensitive: headings that differ only in capitalisation are distinct.
    (re.compile(r"(#{1,6} [^\n]+)\n\1"), r"\1"),
]


@dataclass
class SieveOutput:
    """Structured result of a sieve pass with FinOps telemetry.

    Attributes:
        text: The minimized string after filler removal and whitespace normalization.
        raw_token_count: Estimated token count of the original payload.
        optimized_token_count: Estimated token count after minimization.
        tax_savings_percentage: Percentage reduction relative to the raw baseline,
            in the range ``[0.0, 100.0]``.
    """

    text: str
    raw_token_count: int
    optimized_token_count: int
    tax_savings_percentage: float


def _approximate_token_count(text: str) -> int:
    """Estimates token count using a UTF-8 byte-density heuristic (÷ 4).

    Matches the average token density of the cl100k_base tokenizer across
    mixed English/code content without invoking any external tokenizer.
    """
    return max(0, len(text.encode("utf-8")) // 4)


def _apply_patterns(text: str) -> str:
    """Applies all filler patterns and normalizes whitespace."""
    for pattern, repl in _FILLER_PATTERNS:
        text = pattern.sub(repl, text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r" ([,.:;!?])", r"\1", text)
    return text.strip()


def pure_sieve(payload: str) -> str:
    """Strip conversational boilerplate, normalize whitespace, and return purified text.

    Applies the full Prose Tax filler-phrase regex library to ``payload``:
    greeting tokens, hedging adverbs, affirmation filler, preamble phrases,
    degenerate markdown runs, and duplicate consecutive headings.  Whitespace
    is normalized post-substitution.

    This function is pure and synchronous — it performs no I/O, holds no
    state, and imports no web framework or ML library.  Safe to call from
    scripts, batch pipelines, and offline data-processing workers.

    Args:
        payload: Raw string to be cleaned.

    Returns:
        Minimized string with conversational filler removed and whitespace
        normalized.

    Raises:
        TypeError: If ``payload`` is not a ``str``.
    """
    if not isinstance(payload, str):
        raise TypeError(
            f"pure_sieve expected str, got {type(payload).__name__!r}."
        )
    return _apply_patterns(payload)


def sieve_with_metrics(payload: str) -> SieveOutput:
    """Strip boilerplate and return purified text with FinOps token metrics.

    Equivalent to :func:`pure_sieve` but returns a :class:`SieveOutput`
    dataclass that also carries the raw token estimate, the optimized token
    estimate, and the immediate Prose Tax savings percentage — all computed
    locally via the UTF-8 byte-density heuristic without a network call.

    This function is pure and synchronous — no I/O, no shared state, no web
    framework dependencies.

    Args:
        payload: Raw string to be cleaned.

    Returns:
        A :class:`SieveOutput` with four fields:

        - ``text`` — the minimized string.
        - ``raw_token_count`` — estimated token count before minimization.
        - ``optimized_token_count`` — estimated token count after minimization.
        - ``tax_savings_percentage`` — percentage reduction, rounded to four
          decimal places.

    Raises:
        TypeError: If ``payload`` is not a ``str``.
    """
    if not isinstance(payload, str):
        raise TypeError(
            f"sieve_with_metrics expected str, got {type(payload).__name__!r}."
        )
    raw_count = _approximate_token_count(payload)
    minimized = _apply_patterns(payload)
    optimized_count = _approximate_token_count(minimized)
    savings_pct = (
        (raw_count - optimized_count) / raw_count * 100.0 if raw_count > 0 else 0.0
    )
    return SieveOutput(
        text=minimized,
        raw_token_count=raw_count,
        optimized_token_count=optimized_count,
        tax_savings_percentage=round(savings_pct, 4),
    )
