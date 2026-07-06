# packages/sovereign-airlock/src/sovereign_airlock/payload.py
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NormalizedPayload:
    """Provider-neutral inspection object for outbound governance evaluation.

    A :class:`NormalizedPayload` is not a transport object.  It is an inspection
    object: a stable governance surface derived from any originating protocol,
    provider, or runtime.  All policy evaluation, telemetry generation, context
    minimisation, and receipt creation operate exclusively against this structure.

    :param source: Transport or provider identifier (e.g. ``"openai"``, ``"anthropic"``, ``"raw"``).
    :type source: str
    :param content: Ordered list of text strings extracted from the payload (system prompt,
        user messages, raw text, etc.).  Joined with a single space for flat-string
        evaluation in ``raw`` and ``fields`` scope rules.
    :type content: list[str]
    :param metadata: Transport-specific fields that are not part of the primary content
        surface (e.g. ``model``, ``temperature``, request headers).
    :type metadata: dict[str, Any]
    :param tools: List of tool/function definitions attached to the request, each expressed
        as a provider-neutral mapping with at minimum ``name`` and ``description`` keys.
    :type tools: list[dict[str, Any]]
    :param token_estimate: Heuristic token count of the combined content, computed via the
        UTF-8 byte-density heuristic (÷ 4).  Used for pre-sieve policy telemetry evaluation.
    :type token_estimate: int
    """

    source: str
    content: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
    tools: list[dict[str, Any]] = field(default_factory=list)
    token_estimate: int = 0


def _estimate_tokens(text: str) -> int:
    """Approximates token count using the UTF-8 byte-density heuristic (÷ 4)."""
    return max(0, len(text.encode("utf-8")) // 4)


def normalize_openai(request: dict[str, Any]) -> NormalizedPayload:
    """Normalise an OpenAI-compatible chat completion request into a :class:`NormalizedPayload`.

    Extracts content from ``messages[].content`` (both plain string and multi-part
    content-block formats), and tool definitions from the top-level ``tools`` key.

    :param request: A dict representing an OpenAI chat completion request body.
    :type request: dict[str, Any]
    :return: A provider-neutral :class:`NormalizedPayload` suitable for governance evaluation.
    :rtype: NormalizedPayload
    """
    content: list[str] = []
    for msg in request.get("messages") or []:
        msg_content = msg.get("content", "")
        if isinstance(msg_content, str):
            content.append(msg_content)
        elif isinstance(msg_content, list):
            for part in msg_content:
                if isinstance(part, dict) and part.get("type") == "text":
                    content.append(part.get("text", ""))

    tools: list[dict[str, Any]] = list(request.get("tools") or [])
    metadata: dict[str, Any] = {
        k: v for k, v in request.items() if k not in ("messages", "tools")
    }
    combined = " ".join(content)
    return NormalizedPayload(
        source="openai",
        content=content,
        metadata=metadata,
        tools=tools,
        token_estimate=_estimate_tokens(combined),
    )


def normalize_anthropic(request: dict[str, Any]) -> NormalizedPayload:
    """Normalise an Anthropic-compatible messages API request into a :class:`NormalizedPayload`.

    Handles both plain-string and content-block ``messages[].content`` formats, and
    the top-level ``system`` field (plain string or content-block list).

    :param request: A dict representing an Anthropic messages API request body.
    :type request: dict[str, Any]
    :return: A provider-neutral :class:`NormalizedPayload` suitable for governance evaluation.
    :rtype: NormalizedPayload
    """
    content: list[str] = []

    system = request.get("system")
    if isinstance(system, str):
        content.append(system)
    elif isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                content.append(block.get("text", ""))

    for msg in request.get("messages") or []:
        msg_content = msg.get("content", "")
        if isinstance(msg_content, str):
            content.append(msg_content)
        elif isinstance(msg_content, list):
            for block in msg_content:
                if isinstance(block, dict) and block.get("type") == "text":
                    content.append(block.get("text", ""))

    tools: list[dict[str, Any]] = list(request.get("tools") or [])
    metadata: dict[str, Any] = {
        k: v for k, v in request.items() if k not in ("messages", "tools", "system")
    }
    combined = " ".join(content)
    return NormalizedPayload(
        source="anthropic",
        content=content,
        metadata=metadata,
        tools=tools,
        token_estimate=_estimate_tokens(combined),
    )


def normalize_raw(
    text: str,
    source: str = "raw",
    metadata: dict[str, Any] | None = None,
) -> NormalizedPayload:
    """Normalise a plain string into a :class:`NormalizedPayload`.

    :param text: The raw string payload to wrap.
    :type text: str
    :param source: Transport identifier label.  Defaults to ``"raw"``.
    :type source: str
    :param metadata: Optional annotation mapping attached to the payload.
    :type metadata: dict[str, Any] | None
    :return: A :class:`NormalizedPayload` wrapping the raw string.
    :rtype: NormalizedPayload
    """
    return NormalizedPayload(
        source=source,
        content=[text],
        metadata=metadata or {},
        tools=[],
        token_estimate=_estimate_tokens(text),
    )
