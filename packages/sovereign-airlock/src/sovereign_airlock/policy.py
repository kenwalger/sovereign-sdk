# packages/sovereign-airlock/src/sovereign_airlock/policy.py
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .exception import AirlockConfigurationError
from .payload import NormalizedPayload
from .telemetry import AirlockTelemetry

_VALID_SCOPES: frozenset[str] = frozenset({"raw", "fields", "telemetry"})
_VALID_ACTIONS: frozenset[str] = frozenset({"allow", "warn", "deny"})
_VALID_METRICS: frozenset[str] = frozenset({"raw_tokens", "sieved_tokens", "tax_savings_percentage"})
_POST_SIEVE_METRICS: frozenset[str] = frozenset({"sieved_tokens", "tax_savings_percentage"})


@dataclass(frozen=True)
class PolicyRule:
    """Immutable descriptor for a single machine-local governance rule.

    :param name: Human-readable rule identifier used in violation and warning messages.
    :type name: str
    :param scope: Evaluation domain.  One of ``"raw"`` (flat content string),
        ``"fields"`` (structured field extraction), or ``"telemetry"`` (numeric metrics).
    :type scope: str
    :param action: Outcome on match.  One of ``"allow"``, ``"warn"``, or ``"deny"``.
    :type action: str
    :param pattern: Pre-compiled regex applied during ``raw`` and ``fields`` scope evaluation.
        ``None`` when unused (i.e. for ``telemetry`` scope rules).  Compiled at
        :class:`PolicyEngine` initialisation time; a malformed pattern raises
        :exc:`~sovereign_airlock.exception.AirlockConfigurationError` before any rule is stored.
    :type pattern: re.Pattern[str] | None
    :param fields: Dot-notation field paths evaluated during ``fields`` scope matching
        (e.g. ``"messages.content"``, ``"tools.description"``).
    :type fields: tuple[str, ...]
    :param metric: Token-economy metric name evaluated during ``telemetry`` scope matching
        (e.g. ``"raw_tokens"``, ``"sieved_tokens"``).
    :type metric: str | None
    :param threshold: Numeric ceiling beyond which the telemetry rule triggers.
        Coerced to ``float`` at parse time for ``telemetry``-scope rules; non-numeric
        values raise :exc:`~sovereign_airlock.exception.AirlockConfigurationError`.
    :type threshold: float | None
    """

    name: str
    scope: str
    action: str
    pattern: re.Pattern[str] | None = None
    fields: tuple[str, ...] = field(default_factory=tuple)
    metric: str | None = None
    threshold: int | float | None = None


@dataclass
class PolicyVerdict:
    """Mutable result of a policy evaluation pass over a single :class:`NormalizedPayload`.

    :param allowed: ``True`` if the payload is permitted to continue; ``False`` if any
        ``deny`` rule matched or the global token ceiling was breached.
    :type allowed: bool
    :param violations: Human-readable messages for each triggered ``deny`` rule.
    :type violations: list[str]
    :param warnings: Human-readable messages for each triggered ``warn`` rule.
    :type warnings: list[str]
    """

    allowed: bool = True
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class PolicyEngine:
    """Machine-local policy evaluator that loads governance rules from a YAML configuration file.

    Evaluation is deterministic and fully offline: no network calls are issued,
    no model inference is invoked, and no mutable global state is touched.  All regex
    patterns are compiled at initialisation time; a malformed pattern causes an immediate
    :exc:`~sovereign_airlock.exception.AirlockConfigurationError` rather than a deferred
    ``re.error`` at evaluation time.

    :param config_path: Filesystem path to the YAML policy configuration file.
    :type config_path: str | Path
    :raises AirlockConfigurationError: If the file is absent, cannot be parsed as
        valid YAML, contains structurally invalid rule definitions, or any rule carries
        a malformed regex pattern.
    """

    _rules: list[PolicyRule]
    _max_token_ceiling: int
    _prose_tax_warning_threshold: float

    def __init__(self, config_path: str | Path) -> None:
        """Load and validate the YAML policy configuration.

        :param config_path: Path to the YAML policy file.
        :type config_path: str | Path
        :raises AirlockConfigurationError: On missing file, malformed YAML, invalid rule
            definitions, malformed regex patterns, out-of-range ``prose_tax_warning_threshold``,
            or unrecognised telemetry metric names.
        """
        path = Path(config_path)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw: Any = yaml.safe_load(fh)
        except FileNotFoundError:
            raise AirlockConfigurationError(
                f"Policy configuration not found: {path}"
            )
        except yaml.YAMLError as exc:
            raise AirlockConfigurationError(
                f"Malformed policy YAML at {path}: {exc}"
            ) from exc

        if not isinstance(raw, dict):
            raise AirlockConfigurationError(
                "Policy configuration must be a YAML mapping at the root level."
            )

        global_cfg: dict[str, Any] = raw.get("global") or {}
        self._max_token_ceiling = int(global_cfg.get("max_token_ceiling", 0))
        self._prose_tax_warning_threshold = float(
            global_cfg.get("prose_tax_warning_threshold", 0.0)
        )
        if not (0.0 <= self._prose_tax_warning_threshold <= 1.0):
            raise AirlockConfigurationError(
                f"prose_tax_warning_threshold must be in [0.0, 1.0]; "
                f"got {self._prose_tax_warning_threshold}."
            )

        self._rules = []
        for rule_def in raw.get("rules") or []:
            try:
                self._rules.append(self._parse_rule(rule_def))
            except AirlockConfigurationError:
                raise
            except (KeyError, TypeError, ValueError) as exc:
                raise AirlockConfigurationError(
                    f"Invalid rule definition {rule_def!r}: {exc}"
                ) from exc

    # ------------------------------------------------------------------
    # Internal parsing
    # ------------------------------------------------------------------

    def _parse_rule(self, rule_def: dict[str, Any]) -> PolicyRule:
        """Parse and validate a single rule mapping from the YAML configuration.

        Compiles any ``pattern`` string via :func:`re.compile` at parse time so that
        malformed patterns raise :exc:`~sovereign_airlock.exception.AirlockConfigurationError`
        during :meth:`__init__` rather than a deferred ``re.error`` at evaluation time.

        :param rule_def: Raw rule dictionary extracted from the YAML ``rules`` list.
        :type rule_def: dict[str, Any]
        :return: A validated, immutable :class:`PolicyRule` with a pre-compiled pattern.
        :rtype: PolicyRule
        :raises AirlockConfigurationError: If a scope-required field is absent (``pattern``
            for ``raw``, ``metric`` for ``telemetry``, non-empty ``fields`` for ``fields``),
            if ``pattern`` is present but not a valid regex, or if a ``telemetry``-scope
            rule specifies a ``metric`` not in ``_VALID_METRICS``.
        :raises ValueError: If ``scope`` or ``action`` carry an unrecognised value.
        :raises KeyError: If ``name``, ``scope``, or ``action`` keys are absent.
        """
        name = str(rule_def["name"])
        scope = str(rule_def["scope"])
        action = str(rule_def["action"])

        if scope not in _VALID_SCOPES:
            raise ValueError(
                f"Unknown scope {scope!r}; expected one of {sorted(_VALID_SCOPES)}."
            )
        if action not in _VALID_ACTIONS:
            raise ValueError(
                f"Unknown action {action!r}; expected one of {sorted(_VALID_ACTIONS)}."
            )

        raw_pattern = rule_def.get("pattern")
        compiled: re.Pattern[str] | None = None
        if raw_pattern is not None:
            try:
                compiled = re.compile(str(raw_pattern))
            except re.error as exc:
                raise AirlockConfigurationError(
                    f"Invalid regex pattern in rule '{name}': {exc}"
                ) from exc
        if scope == "raw" and raw_pattern is None:
            raise AirlockConfigurationError(
                f"Rule '{name}' has scope 'raw' but is missing required 'pattern' key."
            )

        raw_metric: str | None = rule_def.get("metric")
        raw_threshold = rule_def.get("threshold")
        if scope == "telemetry":
            if raw_metric is None:
                raise AirlockConfigurationError(
                    f"Rule '{name}' has scope 'telemetry' but is missing required 'metric' key."
                )
            if raw_metric not in _VALID_METRICS:
                raise AirlockConfigurationError(
                    f"Unknown telemetry metric '{raw_metric}' in rule '{name}'; "
                    f"expected one of {sorted(_VALID_METRICS)}."
                )
            if raw_threshold is None:
                raise AirlockConfigurationError(
                    f"Rule '{name}' has scope 'telemetry' but is missing required 'threshold' key."
                )
            try:
                raw_threshold = float(raw_threshold)
            except (ValueError, TypeError) as exc:
                raise AirlockConfigurationError(
                    f"Rule '{name}': threshold value {raw_threshold!r} cannot be coerced to float: {exc}"
                ) from exc

        raw_fields: list[str] = list(rule_def.get("fields") or [])
        if scope == "fields" and not raw_fields:
            raise AirlockConfigurationError(
                f"Rule '{name}' has scope 'fields' but is missing or has empty 'fields' key."
            )
        if scope == "fields" and raw_pattern is None:
            raise AirlockConfigurationError(
                f"Rule '{name}' has scope 'fields' but is missing required 'pattern' key."
            )

        return PolicyRule(
            name=name,
            scope=scope,
            action=action,
            pattern=compiled,
            fields=tuple(raw_fields),
            metric=raw_metric,
            threshold=raw_threshold,
        )

    # ------------------------------------------------------------------
    # Public evaluation interface
    # ------------------------------------------------------------------

    def evaluate(
        self,
        payload: NormalizedPayload,
        telemetry: AirlockTelemetry | None = None,
    ) -> PolicyVerdict:
        """Evaluate all configured rules against ``payload`` and return a :class:`PolicyVerdict`.

        Evaluation order:
        1. Global ``max_token_ceiling`` check against ``payload.token_estimate``.
        2. Each rule in declaration order; all rules are evaluated regardless of
           prior denials so that the full set of violations is surfaced.

        :param payload: The normalised outbound payload under inspection.
        :type payload: NormalizedPayload
        :param telemetry: Optional post-sieve telemetry.  When ``None``, telemetry-scope
            rules fall back to ``payload.token_estimate`` for metric resolution.
        :type telemetry: AirlockTelemetry | None
        :return: A :class:`PolicyVerdict` recording allow/deny state and all messages.
        :rtype: PolicyVerdict
        """
        verdict = PolicyVerdict()

        if (
            self._max_token_ceiling > 0
            and payload.token_estimate > self._max_token_ceiling
        ):
            verdict.allowed = False
            verdict.violations.append(
                f"Global ceiling breach: token_estimate {payload.token_estimate} "
                f"exceeds max_token_ceiling {self._max_token_ceiling}."
            )

        for rule in self._rules:
            if rule.scope == "raw":
                self._evaluate_raw(rule, payload, verdict)
            elif rule.scope == "fields":
                self._evaluate_fields(rule, payload, verdict)
            elif rule.scope == "telemetry":
                self._evaluate_telemetry(rule, payload, telemetry, verdict)

        return verdict

    def evaluate_post_sieve(self, telemetry: AirlockTelemetry) -> PolicyVerdict:
        """Evaluate telemetry-scope rules that require actual post-sieve data.

        Only rules with metrics in ``_POST_SIEVE_METRICS`` (``sieved_tokens``,
        ``tax_savings_percentage``) are evaluated here.  ``raw_tokens`` rules and
        all ``raw``/``fields`` rules were already assessed during the pre-sieve
        :meth:`evaluate` call.  Also applies the ``prose_tax_warning_threshold`` check.

        :param telemetry: Post-sieve metrics from
            :meth:`~sovereign_airlock.telemetry.AirlockTelemetry.from_sieve_output`.
        :type telemetry: AirlockTelemetry
        :return: A :class:`PolicyVerdict` recording any post-sieve deny/warn outcomes.
        :rtype: PolicyVerdict
        """
        verdict = PolicyVerdict()

        for rule in self._rules:
            if rule.scope != "telemetry" or rule.metric not in _POST_SIEVE_METRICS or rule.threshold is None:
                continue

            if rule.metric == "sieved_tokens":
                metric_value: int | float = telemetry.sieved_tokens
            elif rule.metric == "tax_savings_percentage":
                metric_value = telemetry.tax_savings_percentage
            else:
                continue

            if metric_value > rule.threshold:
                self._apply_action(
                    rule,
                    f"Rule '{rule.name}': metric '{rule.metric}' value {metric_value} "
                    f"exceeds threshold {rule.threshold}.",
                    verdict,
                )

        if self._prose_tax_warning_threshold > 0.0:
            threshold_pct = self._prose_tax_warning_threshold * 100.0
            if telemetry.tax_savings_percentage < threshold_pct:
                verdict.warnings.append(
                    f"Prose Tax savings {telemetry.tax_savings_percentage:.4f}% is below "
                    f"the configured warning threshold of {threshold_pct:.1f}%."
                )

        return verdict

    # ------------------------------------------------------------------
    # Scope evaluators
    # ------------------------------------------------------------------

    def _evaluate_raw(
        self,
        rule: PolicyRule,
        payload: NormalizedPayload,
        verdict: PolicyVerdict,
    ) -> None:
        if not rule.pattern:
            return
        flat = " ".join(payload.content)
        if rule.pattern.search(flat):
            self._apply_action(rule, f"Rule '{rule.name}' matched in raw content.", verdict)

    def _evaluate_fields(
        self,
        rule: PolicyRule,
        payload: NormalizedPayload,
        verdict: PolicyVerdict,
    ) -> None:
        if not rule.pattern or not rule.fields:
            return
        for field_path in rule.fields:
            extracted = self._extract_field(field_path, payload)
            if extracted and rule.pattern.search(extracted):
                self._apply_action(
                    rule,
                    f"Rule '{rule.name}' matched in field '{field_path}'.",
                    verdict,
                )
                return

    def _evaluate_telemetry(
        self,
        rule: PolicyRule,
        payload: NormalizedPayload,
        telemetry: AirlockTelemetry | None,
        verdict: PolicyVerdict,
    ) -> None:
        if rule.metric is None or rule.threshold is None:
            return

        metric_value: int | float
        if telemetry is None:
            if rule.metric in _POST_SIEVE_METRICS:
                return
            metric_value = payload.token_estimate
        elif rule.metric == "raw_tokens":
            metric_value = telemetry.raw_tokens
        elif rule.metric == "sieved_tokens":
            metric_value = telemetry.sieved_tokens
        elif rule.metric == "tax_savings_percentage":
            metric_value = telemetry.tax_savings_percentage
        else:
            return

        if metric_value > rule.threshold:
            self._apply_action(
                rule,
                f"Rule '{rule.name}': metric '{rule.metric}' value {metric_value} "
                f"exceeds threshold {rule.threshold}.",
                verdict,
            )

    # ------------------------------------------------------------------
    # Action dispatcher
    # ------------------------------------------------------------------

    def _apply_action(
        self,
        rule: PolicyRule,
        message: str,
        verdict: PolicyVerdict,
    ) -> None:
        if rule.action == "deny":
            verdict.allowed = False
            verdict.violations.append(message)
        elif rule.action == "warn":
            verdict.warnings.append(message)
        # "allow" action is an explicit no-op: matching payload passes unconditionally.

    # ------------------------------------------------------------------
    # Field extraction
    # ------------------------------------------------------------------

    def _extract_field(self, field_path: str, payload: NormalizedPayload) -> str:
        """Extract a dot-notation field value from a :class:`NormalizedPayload`.

        Supported paths and their mapped sources:

        - ``messages.content``, ``input``, ``prompt`` → ``" ".join(payload.content)``
        - ``messages.<other>`` (e.g. ``messages.role``, ``messages.tool_calls``) → ``""``
          These sub-fields have no proxy in the provider-neutral surface; returning
          ``""`` prevents unintended content leakage from a broad ``messages.*`` pattern.
        - ``tools.<sub_field>`` → concatenated tool ``sub_field`` values
        - Anything else → string representation of ``payload.metadata[path]``

        :param field_path: Dot-separated path string (e.g. ``"messages.content"``).
        :type field_path: str
        :param payload: The normalised payload from which to extract.
        :type payload: NormalizedPayload
        :return: Extracted string value, or ``""`` when the path resolves to nothing.
        :rtype: str
        """
        parts = field_path.split(".", 1)
        top = parts[0]

        if top in ("input", "prompt"):
            return " ".join(payload.content)

        if top == "messages":
            sub = parts[1] if len(parts) > 1 else "content"
            if sub == "content":
                return " ".join(payload.content)
            return ""

        if top == "tools" and len(parts) > 1:
            sub_field = parts[1]
            return " ".join(
                str(tool.get(sub_field, ""))
                for tool in payload.tools
                if tool.get(sub_field)
            )

        val = payload.metadata.get(top)
        return str(val) if val is not None else ""
