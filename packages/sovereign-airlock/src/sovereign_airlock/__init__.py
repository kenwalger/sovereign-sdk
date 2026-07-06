# packages/sovereign-airlock/src/sovereign_airlock/__init__.py
"""sovereign-sdk-airlock — Model-neutral outbound governance boundary.

Inspects, evaluates, minimises, and records structured payloads before they cross
a sovereign perimeter and enter an external computational system.  Implements the
four-component Airlock lifecycle: boundary interception, machine-local policy
enforcement, deterministic context minimisation, and immutable evidence generation.
"""

from .boundary import AirlockBoundary, AirlockResult
from .exception import AirlockConfigurationError, AirlockPolicyViolation
from .payload import NormalizedPayload, normalize_anthropic, normalize_openai, normalize_raw
from .policy import PolicyEngine, PolicyRule, PolicyVerdict
from .receipt import ReceiptBuilder
from .telemetry import AirlockTelemetry

__version__ = "1.4.0"

__all__ = [
    "AirlockBoundary",
    "AirlockConfigurationError",
    "AirlockPolicyViolation",
    "AirlockResult",
    "AirlockTelemetry",
    "NormalizedPayload",
    "PolicyEngine",
    "PolicyRule",
    "PolicyVerdict",
    "ReceiptBuilder",
    "normalize_anthropic",
    "normalize_openai",
    "normalize_raw",
]
