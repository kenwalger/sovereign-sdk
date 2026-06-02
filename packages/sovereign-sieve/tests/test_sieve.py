# packages/sovereign-sieve/tests/test_sieve.py
"""Comprehensive test suite for sovereign-sieve.

Invariants verified across every test class:
- pure_sieve() is deterministic: identical inputs produce identical outputs.
- pure_sieve() and sieve_with_metrics() never raise on well-formed str inputs.
- Malformed / edge-case inputs are handled gracefully without exceptions.
- sieve_with_metrics() token arithmetic is internally consistent.
"""
import pytest

from sovereign_sieve import SieveOutput, pure_sieve, sieve_with_metrics


# ---------------------------------------------------------------------------
# TestPureSieveGreetingStripping
# ---------------------------------------------------------------------------
class TestPureSieveGreetingStripping:
    def test_strips_hi(self):
        assert pure_sieve("Hi! help me now") == "! help me now"

    def test_strips_hello(self):
        assert pure_sieve("Hello, can you assist?") == ", can you assist?"

    def test_strips_hey(self):
        assert pure_sieve("Hey there, let's start.") == "there, let's start."

    def test_strips_greetings(self):
        assert pure_sieve("Greetings! Here is the data.") == "! Here is the data."

    def test_strips_case_insensitive(self):
        assert pure_sieve("HELLO world") == "world"

    def test_preserves_hi_fi(self):
        assert "hi-fi" in pure_sieve("The hi-fi system requires calibration.")

    def test_preserves_hello_world(self):
        assert "hello-world" in pure_sieve("hello-world is a classic example.")

    def test_preserves_hi_res(self):
        assert "hi-res" in pure_sieve("Use hi-res images for the banner.")


# ---------------------------------------------------------------------------
# TestPureSieveHedgingAdverbs
# ---------------------------------------------------------------------------
class TestPureSieveHedgingAdverbs:
    def test_strips_please(self):
        result = pure_sieve("Please send me the report.")
        assert "please" not in result.lower()
        assert "send me the report" in result.lower()

    def test_strips_just(self):
        result = pure_sieve("I just need the output.")
        assert "just" not in result.lower()
        assert "need the output" in result.lower()

    def test_strips_simply(self):
        result = pure_sieve("Simply run the command.")
        assert "simply" not in result.lower()

    def test_strips_actually(self):
        result = pure_sieve("Actually, the data is correct.")
        assert "actually" not in result.lower()

    def test_strips_basically(self):
        result = pure_sieve("Basically, this means the request succeeded.")
        assert "basically" not in result.lower()

    def test_strips_probably(self):
        result = pure_sieve("Probably the cache is stale.")
        assert "probably" not in result.lower()

    def test_strips_kindly(self):
        result = pure_sieve("Kindly review the document.")
        assert "kindly" not in result.lower()

    def test_preserves_just_in_time(self):
        assert "just-in-time" in pure_sieve("Enable just-in-time compilation.")

    def test_preserves_simply_typed(self):
        assert "simply-typed" in pure_sieve("This is a simply-typed lambda calculus.")


# ---------------------------------------------------------------------------
# TestPureSieveAffirmationFiller
# ---------------------------------------------------------------------------
class TestPureSieveAffirmationFiller:
    def test_strips_of_course(self):
        result = pure_sieve("Of course, we can proceed.")
        assert "of course" not in result.lower()

    def test_strips_certainly(self):
        result = pure_sieve("Certainly, the answer is yes.")
        assert "certainly" not in result.lower()

    def test_strips_absolutely(self):
        result = pure_sieve("Absolutely, let's continue.")
        assert "absolutely" not in result.lower()

    def test_strips_sure_standalone(self):
        result = pure_sieve("Sure, I can do that.")
        assert result.strip().lower().startswith("i can do that") or "sure," not in result.lower()

    def test_preserves_make_sure(self):
        payload = "Make sure to flush the cache first."
        assert "sure" in pure_sieve(payload)

    def test_preserves_be_sure(self):
        payload = "Be sure to commit before pushing."
        assert "sure" in pure_sieve(payload)

    def test_preserves_certainly_not_compound(self):
        payload = "certainly-not a valid use case"
        assert "certainly-not" in pure_sieve(payload)

    def test_preserves_absolutely_not_compound(self):
        payload = "This is absolutely-not recommended."
        assert "absolutely-not" in pure_sieve(payload)


# ---------------------------------------------------------------------------
# TestPureSievePreamblePhrases
# ---------------------------------------------------------------------------
class TestPureSievePreamblePhrases:
    def test_strips_i_hope_this(self):
        result = pure_sieve("I hope this helps you.")
        assert "i hope this" not in result.lower()
        assert "helps you" in result.lower()

    def test_strips_i_hope_that(self):
        result = pure_sieve("I hope that the data arrives safely.")
        assert "i hope that" not in result.lower()

    def test_strips_i_hope_you(self):
        result = pure_sieve("I hope you are doing well.")
        assert "i hope you" not in result.lower()

    def test_preserves_trailing_content(self):
        result = pure_sieve("I hope this clarifies the situation.")
        assert "clarifies the situation" in result


# ---------------------------------------------------------------------------
# TestPureSieveMarkdownCleaning
# ---------------------------------------------------------------------------
class TestPureSieveMarkdownCleaning:
    def test_removes_four_asterisks(self):
        assert "****" not in pure_sieve("Here is ****some text.")

    def test_removes_five_underscores(self):
        assert "_____" not in pure_sieve("Separator: _____ end.")

    def test_preserves_bold(self):
        assert "**bold**" in pure_sieve("This is **bold** text.")

    def test_preserves_italic(self):
        assert "*italic*" in pure_sieve("This is *italic* text.")

    def test_preserves_bold_italic(self):
        assert "***bold-italic***" in pure_sieve("This is ***bold-italic*** text.")

    def test_deduplicates_headings(self):
        result = pure_sieve("## Setup\n## Setup")
        assert result.count("## Setup") == 1

    def test_preserves_distinct_case_headings(self):
        result = pure_sieve("## Setup\n## setup")
        assert "## Setup" in result
        assert "## setup" in result


# ---------------------------------------------------------------------------
# TestPureSieveWhitespaceNormalization
# ---------------------------------------------------------------------------
class TestPureSieveWhitespaceNormalization:
    def test_collapses_multiple_spaces(self):
        result = pure_sieve("hello   world")
        assert "  " not in result

    def test_removes_space_before_comma(self):
        result = pure_sieve("yes , this works")
        assert " ," not in result

    def test_removes_space_before_period(self):
        result = pure_sieve("done .")
        assert " ." not in result

    def test_strips_leading_trailing_whitespace(self):
        assert pure_sieve("  hello world  ") == pure_sieve("hello world")

    def test_filler_removal_no_double_space_artifact(self):
        result = pure_sieve("hi please help me now")
        assert "  " not in result


# ---------------------------------------------------------------------------
# TestPureSieveDeterminism
# ---------------------------------------------------------------------------
class TestPureSieveDeterminism:
    def test_same_output_on_repeated_calls(self):
        payload = "Hello! Please just help me. Of course, I hope this is clear."
        first = pure_sieve(payload)
        second = pure_sieve(payload)
        third = pure_sieve(payload)
        assert first == second == third

    def test_idempotent(self):
        payload = "Hi there, please just send the data."
        once = pure_sieve(payload)
        twice = pure_sieve(once)
        assert once == twice

    def test_empty_string_is_stable(self):
        assert pure_sieve("") == ""

    def test_whitespace_only_is_stable(self):
        assert pure_sieve("   ") == ""


# ---------------------------------------------------------------------------
# TestPureSieveMalformedInputs
# ---------------------------------------------------------------------------
class TestPureSieveMalformedInputs:
    def test_raises_on_none(self):
        with pytest.raises(TypeError):
            pure_sieve(None)  # type: ignore[arg-type]

    def test_raises_on_integer(self):
        with pytest.raises(TypeError):
            pure_sieve(42)  # type: ignore[arg-type]

    def test_raises_on_list(self):
        with pytest.raises(TypeError):
            pure_sieve(["hello"])  # type: ignore[arg-type]

    def test_raises_on_dict(self):
        with pytest.raises(TypeError):
            pure_sieve({"text": "hello"})  # type: ignore[arg-type]

    def test_handles_only_filler(self):
        result = pure_sieve("hi hello hey please just simply")
        assert isinstance(result, str)

    def test_handles_unicode(self):
        result = pure_sieve("Hello! こんにちは。Please help.")
        assert isinstance(result, str)
        assert "こんにちは" in result

    def test_handles_very_long_string(self):
        payload = ("hi please just " * 1000).strip()
        result = pure_sieve(payload)
        assert isinstance(result, str)

    def test_handles_newlines_and_tabs(self):
        payload = "Hello\tplease\njust help me."
        result = pure_sieve(payload)
        assert isinstance(result, str)

    def test_handles_only_punctuation(self):
        result = pure_sieve("!!! ??? ...")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# TestSieveWithMetrics
# ---------------------------------------------------------------------------
class TestSieveWithMetrics:
    def test_returns_sieve_output(self):
        result = sieve_with_metrics("hi please help me now")
        assert isinstance(result, SieveOutput)

    def test_text_field_matches_pure_sieve(self):
        payload = "Hello! Please just send the data."
        assert sieve_with_metrics(payload).text == pure_sieve(payload)

    def test_raw_count_non_negative(self):
        result = sieve_with_metrics("hi please help me now")
        assert result.raw_token_count >= 0

    def test_optimized_count_lte_raw(self):
        result = sieve_with_metrics("hi please just certainly of course help me now")
        assert result.optimized_token_count <= result.raw_token_count

    def test_savings_zero_on_clean_input(self):
        result = sieve_with_metrics("help me now")
        assert result.tax_savings_percentage == 0.0

    def test_savings_positive_on_filler_input(self):
        result = sieve_with_metrics("hi please just certainly of course help me now")
        assert result.tax_savings_percentage > 0.0

    def test_savings_lte_100(self):
        result = sieve_with_metrics("hi please just certainly of course help me now")
        assert result.tax_savings_percentage <= 100.0

    def test_savings_percentage_arithmetic(self):
        result = sieve_with_metrics("hi please just certainly of course help me now")
        if result.raw_token_count > 0:
            expected = (result.raw_token_count - result.optimized_token_count) / result.raw_token_count * 100.0
            assert abs(result.tax_savings_percentage - round(expected, 4)) < 1e-9

    def test_empty_string_metrics(self):
        result = sieve_with_metrics("")
        assert result.raw_token_count == 0
        assert result.optimized_token_count == 0
        assert result.tax_savings_percentage == 0.0
        assert result.text == ""

    def test_raises_on_non_string(self):
        with pytest.raises(TypeError):
            sieve_with_metrics(42)  # type: ignore[arg-type]

    def test_raises_on_none(self):
        with pytest.raises(TypeError):
            sieve_with_metrics(None)  # type: ignore[arg-type]

    def test_deterministic_metrics(self):
        payload = "Hello! Please just help me. Of course, I hope this is clear."
        first = sieve_with_metrics(payload)
        second = sieve_with_metrics(payload)
        assert first.text == second.text
        assert first.raw_token_count == second.raw_token_count
        assert first.optimized_token_count == second.optimized_token_count
        assert first.tax_savings_percentage == second.tax_savings_percentage

    def test_unicode_payload_metrics(self):
        payload = "Hello! こんにちは。Please help."
        result = sieve_with_metrics(payload)
        assert isinstance(result, SieveOutput)
        assert result.raw_token_count >= result.optimized_token_count
