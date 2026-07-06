"""TDD test suite for sovereign_airlock.policy — write before implementation."""

from pathlib import Path

import pytest
import yaml

from sovereign_airlock.exception import AirlockConfigurationError
from sovereign_airlock.payload import NormalizedPayload, normalize_raw
from sovereign_airlock.policy import PolicyEngine, PolicyRule, PolicyVerdict
from sovereign_airlock.telemetry import AirlockTelemetry
from sovereign_sieve import sieve_with_metrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_policy(tmp_path: Path, config: dict) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.dump(config), encoding="utf-8")
    return path


def _make_telemetry(raw_tokens: int = 100, sieved_tokens: int = 80) -> AirlockTelemetry:
    import hashlib
    return AirlockTelemetry(
        raw_tokens=raw_tokens,
        sieved_tokens=sieved_tokens,
        tax_savings_percentage=round((raw_tokens - sieved_tokens) / raw_tokens * 100.0, 4) if raw_tokens else 0.0,
        payload_hash=hashlib.sha256(b"test").hexdigest(),
    )


# ---------------------------------------------------------------------------
# TestPolicyLoading
# ---------------------------------------------------------------------------

class TestPolicyLoading:
    def test_loads_valid_yaml(self, policy_path: Path) -> None:
        """PolicyEngine initialises cleanly from a well-formed YAML config."""
        engine = PolicyEngine(policy_path)
        assert engine is not None

    def test_raises_on_missing_file(self, tmp_path: Path) -> None:
        """Missing policy file raises AirlockConfigurationError."""
        with pytest.raises(AirlockConfigurationError, match="not found"):
            PolicyEngine(tmp_path / "nonexistent.yaml")

    def test_raises_on_malformed_yaml(self, tmp_path: Path) -> None:
        """Malformed YAML raises AirlockConfigurationError."""
        bad_path = tmp_path / "bad.yaml"
        bad_path.write_bytes(b":\tinvalid:\n  - yaml: [unclosed")
        with pytest.raises(AirlockConfigurationError, match="Malformed"):
            PolicyEngine(bad_path)

    def test_raises_on_invalid_scope(self, tmp_path: Path) -> None:
        """An unrecognised rule scope raises AirlockConfigurationError."""
        config = {
            "version": "1.0",
            "global": {},
            "rules": [{"name": "bad_scope", "scope": "network", "action": "deny", "pattern": "x"}],
        }
        with pytest.raises(AirlockConfigurationError):
            PolicyEngine(_write_policy(tmp_path, config))

    def test_raises_on_invalid_action(self, tmp_path: Path) -> None:
        """An unrecognised rule action raises AirlockConfigurationError."""
        config = {
            "version": "1.0",
            "global": {},
            "rules": [{"name": "bad_action", "scope": "raw", "action": "quarantine", "pattern": "x"}],
        }
        with pytest.raises(AirlockConfigurationError):
            PolicyEngine(_write_policy(tmp_path, config))

    def test_accepts_empty_rules_list(self, tmp_path: Path) -> None:
        """A policy with an empty rules list is valid and permits everything."""
        config = {"version": "1.0", "global": {}, "rules": []}
        engine = PolicyEngine(_write_policy(tmp_path, config))
        payload = normalize_raw("Hello world")
        verdict = engine.evaluate(payload)
        assert verdict.allowed is True
        assert verdict.violations == []
        assert verdict.warnings == []

    def test_raises_on_non_mapping_yaml(self, tmp_path: Path) -> None:
        """A YAML file whose root is not a mapping raises AirlockConfigurationError."""
        path = tmp_path / "list.yaml"
        path.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(AirlockConfigurationError, match="mapping"):
            PolicyEngine(path)

    def test_raises_on_malformed_regex_pattern(self, tmp_path: Path) -> None:
        """A rule with an invalid regex pattern raises AirlockConfigurationError at init time."""
        config = {
            "version": "1.0",
            "global": {},
            "rules": [
                {"name": "bad_regex", "scope": "raw", "action": "deny", "pattern": "[invalid("}
            ],
        }
        with pytest.raises(AirlockConfigurationError, match="bad_regex"):
            PolicyEngine(_write_policy(tmp_path, config))


# ---------------------------------------------------------------------------
# TestRawScopeEvaluation
# ---------------------------------------------------------------------------

class TestRawScopeEvaluation:
    def test_deny_on_private_key_pattern_match(self, policy_path: Path) -> None:
        """Raw deny rule blocks payloads containing a private key marker."""
        engine = PolicyEngine(policy_path)
        payload = normalize_raw("context: -----BEGIN PRIVATE KEY----- abc123")
        verdict = engine.evaluate(payload)
        assert verdict.allowed is False
        assert len(verdict.violations) == 1
        assert "block_private_keys" in verdict.violations[0]

    def test_allow_when_no_raw_pattern_matches(self, policy_path: Path) -> None:
        """Clean content passes the raw scope without violations."""
        engine = PolicyEngine(policy_path)
        payload = normalize_raw("Please analyse the temperature dataset.")
        verdict = engine.evaluate(payload)
        assert verdict.allowed is True
        assert verdict.violations == []

    def test_warn_action_populates_warnings_and_allows(self, tmp_path: Path) -> None:
        """Warn action adds to warnings without setting allowed=False."""
        config = {
            "version": "1.0",
            "global": {},
            "rules": [
                {"name": "warn_on_todo", "scope": "raw", "pattern": "TODO", "action": "warn"}
            ],
        }
        engine = PolicyEngine(_write_policy(tmp_path, config))
        payload = normalize_raw("TODO: refactor this section")
        verdict = engine.evaluate(payload)
        assert verdict.allowed is True
        assert len(verdict.warnings) == 1
        assert "warn_on_todo" in verdict.warnings[0]

    def test_multiple_raw_rules_all_evaluated(self, tmp_path: Path) -> None:
        """All matching deny rules are collected; allowed becomes False on first deny."""
        config = {
            "version": "1.0",
            "global": {},
            "rules": [
                {"name": "rule_a", "scope": "raw", "pattern": "FORBIDDEN_A", "action": "deny"},
                {"name": "rule_b", "scope": "raw", "pattern": "FORBIDDEN_B", "action": "deny"},
            ],
        }
        engine = PolicyEngine(_write_policy(tmp_path, config))
        payload = normalize_raw("FORBIDDEN_A and FORBIDDEN_B in payload")
        verdict = engine.evaluate(payload)
        assert verdict.allowed is False
        assert len(verdict.violations) == 2

    def test_allow_action_is_explicit_no_op(self, tmp_path: Path) -> None:
        """An explicit allow-scope rule matching the payload leaves verdict clean."""
        config = {
            "version": "1.0",
            "global": {},
            "rules": [
                {"name": "explicit_allow", "scope": "raw", "pattern": "safe", "action": "allow"}
            ],
        }
        engine = PolicyEngine(_write_policy(tmp_path, config))
        verdict = engine.evaluate(normalize_raw("this is safe content"))
        assert verdict.allowed is True
        assert verdict.violations == []
        assert verdict.warnings == []


# ---------------------------------------------------------------------------
# TestFieldsScopeEvaluation
# ---------------------------------------------------------------------------

class TestFieldsScopeEvaluation:
    def test_match_in_messages_content_emits_warning(self, policy_path: Path) -> None:
        """Fields rule matches internal namespace reference in payload content."""
        engine = PolicyEngine(policy_path)
        payload = NormalizedPayload(
            source="openai",
            content=["connect to internal.sovereign.local for config"],
            metadata={},
            tools=[],
            token_estimate=50,
        )
        verdict = engine.evaluate(payload)
        assert verdict.allowed is True
        assert any("guard_internal_namespaces" in w for w in verdict.warnings)

    def test_no_match_in_fields_clears_verdict(self, policy_path: Path) -> None:
        """Content without the guarded pattern produces no field warnings."""
        engine = PolicyEngine(policy_path)
        payload = NormalizedPayload(
            source="openai",
            content=["Analyse the public dataset at api.example.com"],
            metadata={},
            tools=[],
            token_estimate=50,
        )
        verdict = engine.evaluate(payload)
        assert not any("guard_internal_namespaces" in w for w in verdict.warnings)

    def test_match_in_tools_description(self, tmp_path: Path) -> None:
        """Fields rule matches a pattern inside a tool description."""
        config = {
            "version": "1.0",
            "global": {},
            "rules": [
                {
                    "name": "no_internal_tools",
                    "scope": "fields",
                    "fields": ["tools.description"],
                    "pattern": "internal_service",
                    "action": "deny",
                }
            ],
        }
        engine = PolicyEngine(_write_policy(tmp_path, config))
        payload = NormalizedPayload(
            source="openai",
            content=["run the tool"],
            metadata={},
            tools=[{"name": "secret_tool", "description": "calls internal_service endpoint"}],
            token_estimate=20,
        )
        verdict = engine.evaluate(payload)
        assert verdict.allowed is False
        assert "no_internal_tools" in verdict.violations[0]


# ---------------------------------------------------------------------------
# TestTelemetryScopeEvaluation
# ---------------------------------------------------------------------------

class TestTelemetryScopeEvaluation:
    def test_warn_when_raw_tokens_exceed_threshold(self, policy_path: Path) -> None:
        """Telemetry rule emits warning when raw_tokens exceed configured threshold."""
        engine = PolicyEngine(policy_path)
        payload = normalize_raw("x")
        telemetry = _make_telemetry(raw_tokens=35000, sieved_tokens=30000)
        verdict = engine.evaluate(payload, telemetry=telemetry)
        assert any("excessive_context" in w for w in verdict.warnings)

    def test_no_telemetry_warning_below_threshold(self, policy_path: Path) -> None:
        """Telemetry rule does not fire when metric is within threshold."""
        engine = PolicyEngine(policy_path)
        payload = normalize_raw("x")
        telemetry = _make_telemetry(raw_tokens=5000, sieved_tokens=4000)
        verdict = engine.evaluate(payload, telemetry=telemetry)
        assert not any("excessive_context" in w for w in verdict.warnings)

    def test_sieved_tokens_rule_skipped_when_telemetry_absent(self, tmp_path: Path) -> None:
        """With telemetry=None, a sieved_tokens rule is silently skipped, not proxied."""
        config = {
            "version": "1.0",
            "global": {},
            "rules": [
                {
                    "name": "tight_sieved_limit",
                    "scope": "telemetry",
                    "metric": "sieved_tokens",
                    "threshold": 10,
                    "action": "warn",
                }
            ],
        }
        engine = PolicyEngine(_write_policy(tmp_path, config))
        payload = NormalizedPayload(
            source="raw",
            content=["payload with many tokens"],
            metadata={},
            tools=[],
            token_estimate=5000,
        )
        verdict = engine.evaluate(payload, telemetry=None)
        assert not any("tight_sieved_limit" in w for w in verdict.warnings)

    def test_tax_savings_rule_skipped_when_telemetry_absent(self, tmp_path: Path) -> None:
        """With telemetry=None, a tax_savings_percentage rule is silently skipped."""
        config = {
            "version": "1.0",
            "global": {},
            "rules": [
                {
                    "name": "low_savings",
                    "scope": "telemetry",
                    "metric": "tax_savings_percentage",
                    "threshold": 5.0,
                    "action": "warn",
                }
            ],
        }
        engine = PolicyEngine(_write_policy(tmp_path, config))
        payload = NormalizedPayload(
            source="raw",
            content=["content"],
            metadata={},
            tools=[],
            token_estimate=500,
        )
        verdict = engine.evaluate(payload, telemetry=None)
        assert not any("low_savings" in w for w in verdict.warnings)

    def test_evaluate_post_sieve_fires_sieved_tokens_warn_rule(self, tmp_path: Path) -> None:
        """evaluate_post_sieve() fires a sieved_tokens rule against actual post-sieve telemetry."""
        config = {
            "version": "1.0",
            "global": {},
            "rules": [
                {
                    "name": "post_sieve_cap",
                    "scope": "telemetry",
                    "metric": "sieved_tokens",
                    "threshold": 50,
                    "action": "warn",
                }
            ],
        }
        engine = PolicyEngine(_write_policy(tmp_path, config))
        telemetry = _make_telemetry(raw_tokens=200, sieved_tokens=100)
        verdict = engine.evaluate_post_sieve(telemetry)
        assert any("post_sieve_cap" in w for w in verdict.warnings)

    def test_evaluate_post_sieve_deny_rule_returns_violation(self, tmp_path: Path) -> None:
        """evaluate_post_sieve() deny rule sets allowed=False when threshold is exceeded."""
        config = {
            "version": "1.0",
            "global": {},
            "rules": [
                {
                    "name": "sieved_hard_cap",
                    "scope": "telemetry",
                    "metric": "sieved_tokens",
                    "threshold": 50,
                    "action": "deny",
                }
            ],
        }
        engine = PolicyEngine(_write_policy(tmp_path, config))
        telemetry = _make_telemetry(raw_tokens=200, sieved_tokens=100)
        verdict = engine.evaluate_post_sieve(telemetry)
        assert verdict.allowed is False
        assert any("sieved_hard_cap" in v for v in verdict.violations)

    def test_falls_back_to_token_estimate_when_telemetry_absent(self, tmp_path: Path) -> None:
        """With telemetry=None, raw scope falls back to payload.token_estimate."""
        config = {
            "version": "1.0",
            "global": {},
            "rules": [
                {
                    "name": "high_estimate",
                    "scope": "telemetry",
                    "metric": "raw_tokens",
                    "threshold": 100,
                    "action": "warn",
                }
            ],
        }
        engine = PolicyEngine(_write_policy(tmp_path, config))
        payload = NormalizedPayload(
            source="raw",
            content=["small payload"],
            metadata={},
            tools=[],
            token_estimate=500,
        )
        verdict = engine.evaluate(payload, telemetry=None)
        assert any("high_estimate" in w for w in verdict.warnings)


# ---------------------------------------------------------------------------
# TestGlobalCeiling
# ---------------------------------------------------------------------------

class TestGlobalCeiling:
    def test_deny_when_token_estimate_exceeds_ceiling(self, tmp_path: Path) -> None:
        """Global max_token_ceiling triggers a deny verdict when breached."""
        config = {"version": "1.0", "global": {"max_token_ceiling": 100}, "rules": []}
        engine = PolicyEngine(_write_policy(tmp_path, config))
        payload = NormalizedPayload(
            source="raw",
            content=["payload"],
            metadata={},
            tools=[],
            token_estimate=200,
        )
        verdict = engine.evaluate(payload)
        assert verdict.allowed is False
        assert len(verdict.violations) == 1

    def test_allow_when_token_estimate_within_ceiling(self, tmp_path: Path) -> None:
        """Global ceiling does not fire when token_estimate is within bounds."""
        config = {"version": "1.0", "global": {"max_token_ceiling": 1000}, "rules": []}
        engine = PolicyEngine(_write_policy(tmp_path, config))
        payload = NormalizedPayload(
            source="raw",
            content=["payload"],
            metadata={},
            tools=[],
            token_estimate=200,
        )
        verdict = engine.evaluate(payload)
        assert verdict.allowed is True

    def test_zero_ceiling_skips_global_check(self, tmp_path: Path) -> None:
        """max_token_ceiling of 0 is treated as disabled (no ceiling enforced)."""
        config = {"version": "1.0", "global": {"max_token_ceiling": 0}, "rules": []}
        engine = PolicyEngine(_write_policy(tmp_path, config))
        payload = NormalizedPayload(
            source="raw",
            content=["payload"],
            metadata={},
            tools=[],
            token_estimate=999999,
        )
        verdict = engine.evaluate(payload)
        assert verdict.allowed is True

    def test_policy_rule_fields_is_immutable_tuple(self, tmp_path: Path) -> None:
        """PolicyRule.fields is stored as an immutable tuple, not a mutable list."""
        config = {
            "version": "1.0",
            "global": {},
            "rules": [
                {
                    "name": "field_rule",
                    "scope": "fields",
                    "fields": ["messages.content", "tools.description"],
                    "pattern": "secret",
                    "action": "deny",
                }
            ],
        }
        engine = PolicyEngine(_write_policy(tmp_path, config))
        rule = engine._rules[0]
        assert isinstance(rule.fields, tuple)
        with pytest.raises(AttributeError):
            rule.fields.append("mutation")  # type: ignore[attr-defined]
