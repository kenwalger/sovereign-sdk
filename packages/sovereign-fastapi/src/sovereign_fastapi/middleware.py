from __future__ import annotations

import json
import logging
from typing import Any, Callable, Awaitable, Optional

logger = logging.getLogger("sovereign_fastapi")

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from sovereign_core.gateway import SovereignGateway


class SovereignMiddleware(BaseHTTPMiddleware):
    """Drop-in Starlette/FastAPI ASGI middleware that wraps the Sovereign sieve-and-sign
    pipeline around every inbound JSON request.

    On each request that carries a JSON body the middleware:

    1. Extracts the target text (either the value of ``payload_field`` or the
       entire serialised body when no field is specified).
    2. Calls :meth:`~sovereign_core.gateway.SovereignGateway.sieve_and_sign`
       to strip Prose Tax filler, normalise whitespace, and produce a signed
       :class:`~sovereign_core.crypto.ForensicReceipt`.
    3. Overwrites ``request._body`` with the optimised payload so that every
       downstream route handler transparently receives the cleaned text without
       any code changes on its side.
    4. Stores the sealed receipt at ``request.state.sovereign_receipt`` for
       in-route auditability.
    5. Injects two headers into the outbound response:
       - ``X-Sovereign-Receipt-Signature`` — the base64-encoded Ed25519 signature.
       - ``X-Sovereign-Tokens-Saved`` — the cumulative FinOps savings counter.

    The underlying :meth:`sieve_and_sign` macro captures its
    :class:`~sovereign_core.gateway.OptimizationReceipt` in coroutine-local
    scope, so concurrent requests sharing the same middleware instance never
    bleed per-request metrics into each other's receipts.

    Args:
        app: The downstream ASGI application to wrap.
        signing_key: Path to the Ed25519 private key PEM file.  Forwarded
            directly to :class:`~sovereign_core.gateway.SovereignGateway`.
            Defaults to ``.keys/sovereign_identity.pem``.
        strict_mode: When ``True``, any exception raised during body
            interception (JSON decode error, missing field, key
            initialisation failure) causes the middleware to abort and
            return an HTTP 422 Unprocessable Entity response instead of
            falling through to the downstream handler.  Defaults to
            ``False``.
        payload_field: Optional key name to extract from the JSON body as
            the text to sieve.  When ``None``, the entire body is
            serialised with :func:`json.dumps` and treated as the input
            string.  Defaults to ``None``.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        signing_key: str = ".keys/sovereign_identity.pem",
        strict_mode: bool = False,
        payload_field: Optional[str] = None,
    ) -> None:
        super().__init__(app)
        self.gateway = SovereignGateway(signing_key=signing_key)
        self.strict_mode = strict_mode
        self.payload_field = payload_field

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        content_type = request.headers.get("content-type", "")
        receipt: Any = None

        if "application/json" in content_type:
            # Buffer the body.  _CachedRequest.wrapped_receive (Starlette's
            # internal mechanism for replaying the body to downstream handlers)
            # returns self._body verbatim when it is set.  Overwriting _body
            # after this call is therefore the correct way to transparently
            # inject a modified payload for every consumer downstream.
            body_bytes = await request.body()
            new_body = body_bytes  # default: pass through unchanged on error

            try:
                body_json = json.loads(body_bytes)

                if self.payload_field is not None:
                    target_text = str(body_json[self.payload_field])
                else:
                    target_text = json.dumps(body_json, ensure_ascii=False)

                result = await self.gateway.sieve_and_sign(target_text)
                receipt = result.receipt
                request.state.sovereign_receipt = receipt

                if self.payload_field is not None:
                    body_json[self.payload_field] = result.content
                    new_body = json.dumps(body_json, ensure_ascii=False).encode("utf-8")
                else:
                    new_body = result.content.encode("utf-8")

            except Exception as exc:
                if self.strict_mode:
                    return JSONResponse(
                        {"detail": f"Sovereign middleware validation error: {exc}"},
                        status_code=422,
                    )
                logger.error(
                    "Sovereign boundary processing failed. "
                    "Bypassing defensively to ensure system availability.",
                    exc_info=True,
                )

            # Overwrite the _CachedRequest body cache.  Every downstream
            # receive() call issued by Starlette's routing layer will replay
            # this value instead of the original stream.
            request._body = new_body  # type: ignore[attr-defined]

            # Realign Content-Length with the (possibly sieved) body so that
            # downstream ASGI handlers, routers, and reverse proxies never
            # encounter a stale-length mismatch against the overwritten body
            # bytes or a hanging read timeout waiting for bytes that no longer
            # exist.
            new_content_length = str(len(new_body)).encode("utf-8")
            scope_headers = request.scope["headers"]
            patched = [
                (n, new_content_length if n == b"content-length" else v)
                for n, v in scope_headers
            ]
            if not any(n == b"content-length" for n, _ in scope_headers):
                patched.append((b"content-length", new_content_length))
            request.scope["headers"] = patched

        response = await call_next(request)

        if receipt is not None:
            response.headers["X-Sovereign-Receipt-Signature"] = receipt["signature"]
            tokens_saved = (
                receipt["metadata"]
                .get("prose_tax_summary", {})
                .get("total_tokens_saved", 0)
            )
            response.headers["X-Sovereign-Tokens-Saved"] = str(tokens_saved)

        return response
