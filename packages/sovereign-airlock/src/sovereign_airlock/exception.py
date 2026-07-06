# packages/sovereign-airlock/src/sovereign_airlock/exception.py


class AirlockPolicyViolation(RuntimeError):
    """Raised when an outbound payload triggers a ``deny`` action in the policy engine.

    Carrying the concatenated violation messages from all matching deny rules,
    this exception signals that the payload has been blocked at the sovereign
    perimeter and must not be transmitted to the external system.

    :param message: Human-readable description of the policy rules that were violated.
    :type message: str
    :param warnings: Non-fatal ``warn``-action messages accumulated prior to the deny,
        preserved here for caller diagnostics.  Empty list when none were collected.
    :type warnings: list[str] | None
    """

    warnings: list[str]

    def __init__(self, message: str, warnings: list[str] | None = None) -> None:
        """
        Initialise the violation exception with violation message and optional prior warnings.

        :param message: Human-readable description of the policy rules that were violated.
        :type message: str
        :param warnings: Non-fatal ``warn``-action messages accumulated before the deny verdict.
        :type warnings: list[str] | None
        """
        super().__init__(message)
        self.warnings = list(warnings) if warnings is not None else []


class AirlockConfigurationError(ValueError):
    """Raised when a policy YAML configuration file is missing, malformed, or structurally invalid.

    Extends :exc:`ValueError` to signal that the error is a configuration
    invariant violation rather than a runtime data-quality issue.  Callers
    should treat this as a fatal initialisation failure requiring operator
    intervention rather than a recoverable runtime exception.

    :param message: Human-readable description of the configuration defect.
    :type message: str
    """
