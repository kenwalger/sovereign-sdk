"""Regression tests for the Prose Tax Optimization Layer and SovereignGateway interface."""

import base64
from typing import Any

import pytest

from sovereign_core.crypto import SovereignKeyManager
from sovereign_core.gateway import (
    OptimizationReceipt,
    SessionContext,
    SieveAndSignResult,
    SovereignBoundaryResponse,
    SovereignGateway,
    process_prose_tax,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ctx() -> SessionContext:
    """Provide a fresh, isolated SessionContext for each test."""
    return SessionContext(session_id="pytest-gateway-session-001")


# ---------------------------------------------------------------------------
# Filler token stripping
# ---------------------------------------------------------------------------


class TestFillerTokenStripping:
    """Verify that standard conversational filler tokens are cleanly removed."""

    async def test_greeting_hi_stripped(self, ctx: SessionContext) -> None:
        """'hi' greeting token is stripped from a plaintext string payload.

        The token must be removed entirely when it appears as a standalone word,
        leaving the rest of the sentence intact with normalized whitespace.
        """
        result, _ = await process_prose_tax("hi there, welcome", ctx)

        assert isinstance(result, str)
        assert "hi " not in result.lower(), (
            f"Greeting 'hi' must be stripped from the payload; got: {result!r}"
        )

    async def test_greeting_hello_stripped(self, ctx: SessionContext) -> None:
        """'hello' greeting token is stripped when it appears as a standalone word.

        The sentence body following the greeting must be preserved verbatim
        with leading whitespace stripped.
        """
        result, _ = await process_prose_tax("hello this is a message", ctx)

        assert isinstance(result, str)
        assert result.lower().startswith("this"), (
            f"'hello' must be stripped; remaining content must start with 'this'; "
            f"got: {result!r}"
        )

    async def test_please_stripped(self, ctx: SessionContext) -> None:
        """'please' hedging adverb is removed when used as a standalone filler token."""
        result, _ = await process_prose_tax("please do the thing now", ctx)

        assert "please" not in result.lower(), (
            f"'please' filler must be removed; got: {result!r}"
        )

    async def test_certainly_standalone_stripped(self, ctx: SessionContext) -> None:
        """'certainly' affirmation filler is removed when it appears as a standalone token."""
        result, _ = await process_prose_tax("certainly I can help with that", ctx)

        assert "certainly" not in result.lower(), (
            f"'certainly' standalone affirmation must be stripped; got: {result!r}"
        )

    async def test_of_course_stripped(self, ctx: SessionContext) -> None:
        """'of course' affirmation phrase is cleanly removed from the payload."""
        result, _ = await process_prose_tax("of course that is the correct approach", ctx)

        assert "of course" not in result.lower(), (
            f"'of course' affirmation phrase must be stripped; got: {result!r}"
        )

    async def test_i_hope_this_preamble_stripped_content_preserved(
        self, ctx: SessionContext
    ) -> None:
        """'I hope this' preamble token is removed; trailing sentence content is preserved intact.

        The previous greedy '.*' wildcard silently consumed the entire remainder
        of the line.  The pattern must terminate at the word boundary after the
        subject pronoun, stripping only the literal preamble token.
        """
        result, _ = await process_prose_tax(
            "I hope this helps you understand the system", ctx
        )

        assert "i hope this" not in result.lower(), (
            f"Preamble 'I hope this' must be stripped; got: {result!r}"
        )
        assert "helps you understand the system" in result, (
            f"Content following the preamble must be preserved intact; got: {result!r}"
        )

    async def test_just_hedging_adverb_stripped(self, ctx: SessionContext) -> None:
        """'just' hedging adverb is removed as standalone colloquial filler."""
        result, _ = await process_prose_tax("just run the command", ctx)

        assert "just" not in result.lower().split(), (
            f"'just' standalone adverb must be stripped; got: {result!r}"
        )

    async def test_return_is_tuple_of_str_and_receipt(self, ctx: SessionContext) -> None:
        """process_prose_tax always returns (str, OptimizationReceipt) for string input."""
        result, receipt = await process_prose_tax("please do it", ctx)

        assert isinstance(result, str), "String input must return a string output"
        assert isinstance(receipt, OptimizationReceipt), "Second element must be OptimizationReceipt"


# ---------------------------------------------------------------------------
# High-integrity phrase passthrough
# ---------------------------------------------------------------------------


class TestHighIntegrityPhrasePassthrough:
    """Verify that guarded high-integrity phrases survive the optimization pass unmarred."""

    async def test_make_sure_to_preserved(self, ctx: SessionContext) -> None:
        """The instructional directive 'make sure to' passes through completely unmarred.

        The (?<!make ) lookbehind guard on the 'sure' sub-pattern protects
        this phrase.  Without it, 'sure' would be stripped, leaving 'make to'.
        """
        payload = "make sure to validate the input before submitting"
        result, _ = await process_prose_tax(payload, ctx)

        assert "make sure to" in result, (
            f"Instructional directive 'make sure to' must not be stripped; got: {result!r}"
        )

    async def test_hello_world_hyphenated_preserved(self, ctx: SessionContext) -> None:
        """The hyphenated compound 'hello-world' must not be mangled to '-world'.

        The (?![-\\w]) negative lookahead on the greeting alternation group
        prevents the match when 'hello' is immediately followed by a hyphen.
        """
        payload = "run the hello-world example to verify the setup"
        result, _ = await process_prose_tax(payload, ctx)

        assert "hello-world" in result, (
            f"Hyphenated compound 'hello-world' must survive intact; got: {result!r}"
        )

    async def test_hi_fi_hyphenated_preserved(self, ctx: SessionContext) -> None:
        """The hyphenated compound 'hi-fi' must not be mangled to '-fi'.

        The (?![-\\w]) lookahead guard on the greeting alternation group
        prevents 'hi' from matching when it is the first component of a
        hyphenated technical term.
        """
        payload = "the hi-fi audio system delivered clean output"
        result, _ = await process_prose_tax(payload, ctx)

        assert "hi-fi" in result, (
            f"Hyphenated compound 'hi-fi' must survive intact; got: {result!r}"
        )

    async def test_certainly_not_hyphenated_preserved(self, ctx: SessionContext) -> None:
        """The hyphenated compound 'certainly-not' must not be mangled to '-not'.

        The (?![-\\w]) lookahead unified across the certainly/absolutely
        alternation group prevents the match when the token is the first
        component of a hyphenated compound.
        """
        payload = "a certainly-not pattern confirms the invariant holds"
        result, _ = await process_prose_tax(payload, ctx)

        assert "certainly-not" in result, (
            f"Hyphenated compound 'certainly-not' must survive intact; got: {result!r}"
        )

    async def test_be_sure_to_preserved(self, ctx: SessionContext) -> None:
        """The instructional directive 'be sure to' is protected by the (?<!be ) lookbehind guard.

        Without this guard, 'sure' in 'be sure to flush' would be stripped,
        producing the grammatically broken 'be to flush'.
        """
        payload = "be sure to flush the buffer before closing"
        result, _ = await process_prose_tax(payload, ctx)

        assert "be sure to" in result, (
            f"Instructional directive 'be sure to' must not be stripped; got: {result!r}"
        )

    async def test_absolutely_not_hyphenated_preserved(self, ctx: SessionContext) -> None:
        """The hyphenated compound 'absolutely-not' must not be mangled to '-not'.

        'absolutely' is covered by the same non-capturing group and shared
        (?![-\\w]) lookahead as 'certainly'.
        """
        payload = "an absolutely-not check prevents invalid state transitions"
        result, _ = await process_prose_tax(payload, ctx)

        assert "absolutely-not" in result, (
            f"Hyphenated compound 'absolutely-not' must survive intact; got: {result!r}"
        )


# ---------------------------------------------------------------------------
# Case-sensitive heading preservation
# ---------------------------------------------------------------------------


class TestCaseSensitiveHeadingPreservation:
    """Verify that case-distinct headings are preserved and never silently collapsed."""

    async def test_identical_duplicate_headings_collapsed_to_one(
        self, ctx: SessionContext
    ) -> None:
        """Exactly duplicate headings (same text, same case, consecutive) are collapsed to one.

        The deduplication regex must replace the pair with a single clean copy
        via the \\1 backreference, not erase both headings entirely.
        """
        payload = "# Setup\n# Setup"
        result, _ = await process_prose_tax(payload, ctx)

        assert result.count("# Setup") == 1, (
            f"Identical consecutive headings must be collapsed to exactly one copy; "
            f"got: {result!r}"
        )

    async def test_different_case_headings_both_preserved(self, ctx: SessionContext) -> None:
        """Headings that differ only in capitalisation are distinct and must not be collapsed.

        The deduplication regex is compiled WITHOUT re.IGNORECASE.  '# Setup'
        and '# SETUP' are semantically different section anchors and must
        both survive in the output.
        """
        payload = "# Setup\n# SETUP"
        result, _ = await process_prose_tax(payload, ctx)

        assert "# Setup" in result, (
            f"'# Setup' (title-case) heading must survive; got: {result!r}"
        )
        assert "# SETUP" in result, (
            f"'# SETUP' (upper-case) heading must survive; got: {result!r}"
        )

    async def test_different_heading_levels_both_preserved(self, ctx: SessionContext) -> None:
        """Headings at different markdown levels (## vs ###) are distinct and must both survive."""
        payload = "## Config\n### Config"
        result, _ = await process_prose_tax(payload, ctx)

        assert "## Config" in result, "Level-2 heading must survive"
        assert "### Config" in result, "Level-3 heading must survive"

    async def test_non_consecutive_duplicate_headings_both_preserved(
        self, ctx: SessionContext
    ) -> None:
        """Non-consecutive duplicate headings (separated by content) must not be collapsed.

        The regex matches only the pattern (heading \\n identical-heading) and
        cannot fire when content separates two identical headings.
        """
        payload = "# Intro\nSome content here.\n# Intro"
        result, _ = await process_prose_tax(payload, ctx)

        assert result.count("# Intro") == 2, (
            f"Non-consecutive duplicate headings must both survive; got: {result!r}"
        )


# ---------------------------------------------------------------------------
# Whitespace and punctuation normalization
# ---------------------------------------------------------------------------


class TestWhitespaceNormalization:
    """Verify that multiple spaces and orphaned punctuation artifacts are normalized."""

    async def test_multiple_spaces_collapsed_to_one(self, ctx: SessionContext) -> None:
        """Multiple consecutive spaces are normalized to a single space throughout the string."""
        result, _ = await process_prose_tax("word   word    word", ctx)

        assert "  " not in result, (
            f"Double (or more) consecutive spaces must be collapsed; got: {result!r}"
        )
        assert result == "word word word", (
            f"Three words with multi-spaces must normalize to single-spaced output; "
            f"got: {result!r}"
        )

    async def test_orphaned_space_before_comma_removed(self, ctx: SessionContext) -> None:
        """A space immediately before a comma is absorbed into the comma."""
        result, _ = await process_prose_tax("this , is a test", ctx)

        assert " ," not in result, (
            f"Space before comma must be collapsed; got: {result!r}"
        )
        assert "this," in result, (
            f"Comma must immediately follow its preceding word; got: {result!r}"
        )

    async def test_orphaned_space_before_period_removed(self, ctx: SessionContext) -> None:
        """A space immediately before a period is absorbed into the period."""
        result, _ = await process_prose_tax("end of sentence .", ctx)

        assert " ." not in result, (
            f"Space before period must be collapsed; got: {result!r}"
        )

    async def test_filler_removal_leaves_no_double_space(self, ctx: SessionContext) -> None:
        """Stripping a filler word must not leave a double-space artifact in the output.

        When 'please' is removed from 'please do the thing', the space that
        preceded it and the space that followed it collapse via the
        post-substitution re.sub(r' {2,}', ' ', ...) normalization pass.
        """
        result, _ = await process_prose_tax("please do the thing now", ctx)

        assert "  " not in result, (
            f"Filler removal must not leave a double-space artifact; got: {result!r}"
        )

    async def test_combined_filler_removal_and_normalization(self, ctx: SessionContext) -> None:
        """Removing multiple filler words from a sentence produces clean single-spaced output."""
        result, _ = await process_prose_tax("hi please just help me now", ctx)

        assert "  " not in result, (
            f"No double spaces after multi-filler removal; got: {result!r}"
        )
        assert result == "help me now", (
            f"'hi', 'please', and 'just' must all be stripped, leaving 'help me now'; "
            f"got: {result!r}"
        )


# ---------------------------------------------------------------------------
# Dict-based message list structural preservation
# ---------------------------------------------------------------------------


class TestDictMessageListStructure:
    """Verify that dict-based message list payloads preserve their exact structural shape."""

    async def test_list_input_returns_list(self, ctx: SessionContext) -> None:
        """A list-of-dicts payload returns a list, not a collapsed flat string.

        The return type must mirror the structural type of the input for
        downstream consumers that unpack the result into a message pipeline.
        """
        payload: list[dict[str, Any]] = [
            {"role": "user", "content": "hi please help"},
        ]
        result, _ = await process_prose_tax(payload, ctx)

        assert isinstance(result, list), (
            f"List input must return a list; got {type(result).__name__!r}"
        )

    async def test_all_non_content_keys_preserved(self, ctx: SessionContext) -> None:
        """All non-content keys in each message dict are forwarded to the output unmodified.

        Shallow-copying each dict via {**msg, 'content': minimized} must not
        drop any keys that are not named 'content'.
        """
        payload: list[dict[str, Any]] = [
            {"role": "user", "content": "hello there", "id": "msg-001", "turn": 1},
        ]
        result, _ = await process_prose_tax(payload, ctx)

        output_msg = result[0]
        assert output_msg["role"] == "user", "'role' key must be preserved"
        assert output_msg["id"] == "msg-001", "'id' key must be preserved"
        assert output_msg["turn"] == 1, "'turn' key must be preserved"

    async def test_content_field_is_optimized(self, ctx: SessionContext) -> None:
        """The 'content' field of each message dict is processed through prose tax optimization.

        Filler words present in the content string must be stripped in the
        output, proving the minimization pass ran over the dict payload.
        """
        payload: list[dict[str, Any]] = [
            {"role": "user", "content": "hi please just help me now"},
        ]
        result, _ = await process_prose_tax(payload, ctx)

        content: str = result[0]["content"]
        assert "please" not in content.lower(), (
            f"'please' must be stripped from message content; got: {content!r}"
        )
        assert content == "help me now", (
            f"Full filler stripping on 'hi please just help me now' must yield "
            f"'help me now'; got: {content!r}"
        )

    async def test_message_without_string_content_forwarded_unmodified(
        self, ctx: SessionContext
    ) -> None:
        """Messages that lack a string 'content' field are forwarded to the output unmodified.

        A system-role dict with no 'content' key, or with a non-string value
        under 'content', must pass through the list branch unchanged.
        """
        system_msg: dict[str, Any] = {"role": "system", "tokens": 42}
        payload: list[dict[str, Any]] = [
            system_msg,
            {"role": "user", "content": "hello there"},
        ]
        result, _ = await process_prose_tax(payload, ctx)

        assert result[0] == system_msg, (
            f"System message without 'content' must be forwarded unmodified; "
            f"got: {result[0]!r}"
        )

    async def test_output_list_length_equals_input_length(self, ctx: SessionContext) -> None:
        """The returned list contains exactly as many messages as the input list.

        No messages must be silently dropped during the minimization pass,
        even if their content field becomes empty after filler stripping.
        """
        payload: list[dict[str, Any]] = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "of course certainly"},
            {"role": "user", "content": "please continue"},
        ]
        result, _ = await process_prose_tax(payload, ctx)

        assert len(result) == 3, (
            f"Output list must have the same length as input; expected 3, got {len(result)}"
        )

    async def test_optimization_receipt_reflects_list_token_counts(
        self, ctx: SessionContext
    ) -> None:
        """The OptimizationReceipt from a list payload carries non-negative token counts.

        Token estimation runs over the joined content strings; counts must be
        non-negative integers and tax_savings_percentage must be in [0.0, 100.0].
        """
        payload: list[dict[str, Any]] = [
            {"role": "user", "content": "hi please certainly help"},
        ]
        _, receipt = await process_prose_tax(payload, ctx)

        assert receipt.raw_token_count >= 0
        assert receipt.optimized_token_count >= 0
        assert 0.0 <= receipt.tax_savings_percentage <= 100.0

    async def test_invalid_payload_type_raises_type_error(self, ctx: SessionContext) -> None:
        """Passing a non-string, non-list payload raises TypeError with a descriptive message."""
        with pytest.raises(TypeError):
            await process_prose_tax(12345, ctx)  # type: ignore[arg-type]

    async def test_session_context_receives_receipt_after_list_pass(
        self, ctx: SessionContext
    ) -> None:
        """After a list-payload pass, the SessionContext carries the prose_tax_receipt entry.

        Downstream ForensicReceipt envelopes rely on this key being populated
        in the context variable store to carry a complete optimization audit trail.
        """
        payload: list[dict[str, Any]] = [
            {"role": "user", "content": "please just do it"},
        ]
        await process_prose_tax(payload, ctx)

        assert ctx.get("prose_tax_receipt") is not None, (
            "prose_tax_receipt must be written into the SessionContext after optimization"
        )


# ---------------------------------------------------------------------------
# SovereignGateway high-level interface
# ---------------------------------------------------------------------------


@pytest.fixture
def gateway_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> SovereignGateway:
    """Provide a SovereignGateway with a temp key directory and known node secret."""
    monkeypatch.setenv("SOVEREIGN_NODE_SECRET", "pytest-gateway-secret-v072")
    return SovereignGateway(signing_key=str(tmp_path / "keys" / "sovereign_identity.pem"))


class TestSovereignGateway:
    """Functional tests for the SovereignGateway high-level developer interface."""

    async def test_sieve_returns_string(self, tmp_path: Any) -> None:
        """sieve() always returns a plain string regardless of input content."""
        gw = SovereignGateway(signing_key=str(tmp_path / ".keys" / "sovereign_identity.pem"))
        result = await gw.sieve("hello world")

        assert isinstance(result, str), (
            f"sieve() must return a str; got {type(result).__name__!r}"
        )

    async def test_sieve_strips_filler_tokens(self, tmp_path: Any) -> None:
        """sieve() removes greeting, hedging, and affirmation filler tokens."""
        gw = SovereignGateway(signing_key=str(tmp_path / ".keys" / "sovereign_identity.pem"))
        result = await gw.sieve("hi please just help me now")

        assert result == "help me now", (
            f"sieve() must strip 'hi', 'please', and 'just', leaving 'help me now'; "
            f"got: {result!r}"
        )

    async def test_sieve_normalizes_whitespace(self, tmp_path: Any) -> None:
        """sieve() collapses multiple consecutive spaces to a single space."""
        gw = SovereignGateway(signing_key=str(tmp_path / ".keys" / "sovereign_identity.pem"))
        result = await gw.sieve("word   word    word")

        assert result == "word word word", (
            f"sieve() must normalize multi-space runs; got: {result!r}"
        )

    async def test_sieve_preserves_guarded_phrases(self, tmp_path: Any) -> None:
        """sieve() must not mangle guarded instructional phrases like 'make sure to'."""
        gw = SovereignGateway(signing_key=str(tmp_path / ".keys" / "sovereign_identity.pem"))
        result = await gw.sieve("make sure to validate the input before submitting")

        assert "make sure to" in result, (
            f"sieve() must preserve 'make sure to' intact; got: {result!r}"
        )

    def test_sign_returns_forensic_receipt_fields(self, gateway_env: SovereignGateway) -> None:
        """sign() returns a ForensicReceipt dict containing all five required envelope fields."""
        receipt = gateway_env.sign("clean context for signing")

        for field in ("timestamp", "payload_hash", "public_key", "signature", "metadata"):
            assert field in receipt, (
                f"ForensicReceipt must contain '{field}' field; got keys: {list(receipt.keys())}"
            )

    def test_sign_receipt_is_cryptographically_valid(
        self, gateway_env: SovereignGateway
    ) -> None:
        """sign() produces a receipt that passes full SovereignKeyManager.verify_receipt verification.

        This confirms that the payload dict used internally by sign() matches
        the one re-derived by verify_receipt, and that the Ed25519 signature
        over the canonical manifest is intact.
        """
        clean_context = "sovereign verified payload for audit ledger"
        receipt = gateway_env.sign(clean_context)

        assert SovereignKeyManager.verify_receipt(
            receipt, {"content": clean_context}
        ), (
            "The ForensicReceipt produced by sign() must pass full Ed25519 "
            "signature verification against the original payload"
        )

    def test_sign_receipt_fails_on_tampered_payload(
        self, gateway_env: SovereignGateway
    ) -> None:
        """verify_receipt returns False when the payload passed to it differs from the signed one."""
        receipt = gateway_env.sign("original payload content")

        assert not SovereignKeyManager.verify_receipt(
            receipt, {"content": "tampered payload content"}
        ), (
            "A receipt verified against a mutated payload must return False"
        )

    def test_sign_receipt_metadata_contains_source_tag(
        self, gateway_env: SovereignGateway
    ) -> None:
        """The ForensicReceipt metadata field carries the 'SovereignGateway' source tag."""
        receipt = gateway_env.sign("tagged payload")

        assert receipt["metadata"].get("source") == "SovereignGateway", (
            f"Receipt metadata must carry source='SovereignGateway'; "
            f"got: {receipt['metadata']!r}"
        )

    async def test_gateway_sieve_and_sign_atomic(
        self, gateway_env: SovereignGateway
    ) -> None:
        """sieve_and_sign() returns a SovereignBoundaryResponse with minimized content and a valid receipt.

        Verifies the one-shot macro contract via attribute access on the
        SovereignBoundaryResponse Pydantic model:
          - result.content equals the fully stripped output of sieve()
          - result.receipt carries all five ForensicReceipt envelope fields
          - the receipt passes full Ed25519 verify_receipt against {"content": ...}
          - prose_tax_summary is present and internally consistent because sieve()
            ran before sign() inside the same macro call
        """
        raw_payload = "hi please just help me now"
        result = await gateway_env.sieve_and_sign(raw_payload)

        assert isinstance(result, SovereignBoundaryResponse), (
            f"sieve_and_sign() must return a SovereignBoundaryResponse; "
            f"got {type(result).__name__!r}"
        )

        assert result.content == "help me now", (
            f"content must be the fully minimized payload; got: {result.content!r}"
        )

        receipt = result.receipt
        for field in ("timestamp", "payload_hash", "public_key", "signature", "metadata"):
            assert field in receipt, (
                f"ForensicReceipt must contain '{field}'; got keys: {list(receipt.keys())!r}"
            )

        assert SovereignKeyManager.verify_receipt(
            receipt, {"content": result.content}
        ), (
            "Receipt from sieve_and_sign() must pass full Ed25519 "
            "verify_receipt check against its own content"
        )

        assert "prose_tax_summary" in receipt["metadata"], (
            "prose_tax_summary must be fused into metadata by the atomic macro call"
        )
        summary = receipt["metadata"]["prose_tax_summary"]
        assert summary["tokens_eliminated"] == (
            summary["raw_token_count"] - summary["optimized_token_count"]
        ), "tokens_eliminated arithmetic must be self-consistent inside sieve_and_sign result"

    def test_gateway_export_public_key(self, gateway_env: SovereignGateway) -> None:
        """export_public_key() returns the base64-encoded Ed25519 public verification key.

        Verifies three invariants against the ReceiptSchema criteria:
          1. The return value is a plain string.
          2. It base64-decodes to exactly 32 raw bytes (Ed25519 public key size).
          3. It matches receipt["public_key"] in ForensicReceipt envelopes produced
             by the same gateway instance, confirming key-pin consistency.
        """
        pub_key = gateway_env.export_public_key()

        assert isinstance(pub_key, str), (
            f"export_public_key() must return a str; got {type(pub_key).__name__!r}"
        )

        decoded = base64.b64decode(pub_key)
        assert len(decoded) == 32, (
            f"Decoded public key must be 32 bytes (Ed25519); got {len(decoded)} bytes"
        )

        receipt = gateway_env.sign("verification key pin check")
        assert pub_key == receipt["public_key"], (
            "export_public_key() must return the same key pinned in "
            "ForensicReceipt envelopes from this gateway instance"
        )

    async def test_gateway_fuses_prose_tax_metadata(
        self, gateway_env: SovereignGateway
    ) -> None:
        """sieve() then sign() fuses Prose Tax telemetry into the signed ForensicReceipt metadata.

        Verifies that the prose_tax_summary sub-dict is present in the receipt
        metadata, contains all five required keys, carries arithmetically
        consistent and exact values for the known input payload, and that the
        enriched envelope still passes full Ed25519 cryptographic verification —
        confirming that data-provenance and FinOps optimization telemetry are
        sealed together in a single tamper-evident manifest.

        Token approximation uses the byte-density heuristic (UTF-8 bytes ÷ 4):
          - 'hi please just help me now' → 26 bytes → raw_token_count = 6
          - 'help me now'                → 11 bytes → optimized_token_count = 2
          - tokens_eliminated            → 4
        """
        raw_payload = "hi please just help me now"
        clean_context = await gateway_env.sieve(raw_payload)
        receipt = gateway_env.sign(clean_context)

        assert "prose_tax_summary" in receipt["metadata"], (
            f"sign() after sieve() must inject prose_tax_summary into metadata; "
            f"got keys: {list(receipt['metadata'].keys())!r}"
        )
        summary = receipt["metadata"]["prose_tax_summary"]

        for key in (
            "raw_token_count",
            "optimized_token_count",
            "tokens_eliminated",
            "tax_savings_percentage",
            "total_tokens_saved",
        ):
            assert key in summary, (
                f"prose_tax_summary must contain '{key}'; got keys: {list(summary.keys())!r}"
            )

        assert summary["raw_token_count"] == 6, (
            f"raw_token_count for 'hi please just help me now' (26 UTF-8 bytes) "
            f"must be 6; got: {summary['raw_token_count']}"
        )
        assert summary["optimized_token_count"] == 2, (
            f"optimized_token_count for 'help me now' (11 UTF-8 bytes) "
            f"must be 2; got: {summary['optimized_token_count']}"
        )
        assert summary["tokens_eliminated"] == 4, (
            f"tokens_eliminated must be 4; got: {summary['tokens_eliminated']}"
        )
        assert summary["tokens_eliminated"] == (
            summary["raw_token_count"] - summary["optimized_token_count"]
        ), "tokens_eliminated must equal raw_token_count - optimized_token_count"
        assert 0.0 <= summary["tax_savings_percentage"] <= 100.0, (
            f"tax_savings_percentage must be in [0.0, 100.0]; "
            f"got: {summary['tax_savings_percentage']}"
        )
        assert summary["total_tokens_saved"] >= 0, (
            "total_tokens_saved must be non-negative"
        )

        assert SovereignKeyManager.verify_receipt(
            receipt, {"content": clean_context}
        ), (
            "ForensicReceipt enriched with prose_tax_summary must still pass "
            "full Ed25519 signature verification"
        )
