import asyncio
import json
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import AsyncClient, ASGITransport

from sovereign_fastapi.middleware import SovereignMiddleware


# ---------------------------------------------------------------------------
# Test application factory
# ---------------------------------------------------------------------------

def _build_app(tmp_path: Any, payload_field: str = "text") -> FastAPI:
    """Return a minimal FastAPI app wrapped in SovereignMiddleware."""
    app = FastAPI()
    app.add_middleware(
        SovereignMiddleware,
        signing_key=str(tmp_path / "keys" / "sovereign_identity.pem"),
        payload_field=payload_field,
    )

    @app.post("/ingest")
    async def ingest(request: Request) -> JSONResponse:
        body_bytes = await request.body()
        try:
            data = json.loads(body_bytes)
            received = data.get(payload_field, "")
        except (json.JSONDecodeError, AttributeError):
            received = body_bytes.decode("utf-8", errors="replace")
        return JSONResponse({"received_text": received})

    return app


def _build_strict_app(tmp_path: Any) -> FastAPI:
    """Return a strict-mode app that returns 422 on middleware errors."""
    app = FastAPI()
    app.add_middleware(
        SovereignMiddleware,
        signing_key=str(tmp_path / "strict_keys" / "sovereign_identity.pem"),
        payload_field="text",
        strict_mode=True,
    )

    @app.post("/ingest")
    async def ingest(request: Request) -> JSONResponse:  # pragma: no cover
        body_bytes = await request.body()
        return JSONResponse({"received_text": json.loads(body_bytes).get("text", "")})

    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> FastAPI:
    """FastAPI test app with SovereignMiddleware and an isolated key directory."""
    monkeypatch.setenv("SOVEREIGN_NODE_SECRET", "pytest-middleware-secret-v010")
    return _build_app(tmp_path)


@pytest.fixture
def strict_app_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> FastAPI:
    """Strict-mode variant of the test app."""
    monkeypatch.setenv("SOVEREIGN_NODE_SECRET", "pytest-middleware-strict-secret")
    return _build_strict_app(tmp_path)


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------

class TestSovereignMiddleware:
    """Functional and concurrency tests for SovereignMiddleware."""

    async def test_middleware_happy_path(self, app_env: FastAPI) -> None:
        """Inbound filler payload is sieved; route handler receives minimized text;
        outbound response carries the X-Sovereign-* cryptographic headers.

        Specifically verifies:
        - Route handler sees 'help me now' instead of the raw filler-laden input.
        - X-Sovereign-Receipt-Signature is present and non-empty.
        - X-Sovereign-Tokens-Saved is present and a non-negative integer.
        """
        async with AsyncClient(
            transport=ASGITransport(app=app_env), base_url="http://test"
        ) as client:
            response = await client.post(
                "/ingest", json={"text": "hi please just help me now"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["received_text"] == "help me now", (
            f"Route handler must see the sieved payload; got: {data['received_text']!r}"
        )

        headers = response.headers
        assert "x-sovereign-receipt-signature" in headers, (
            "Response must carry X-Sovereign-Receipt-Signature header"
        )
        assert headers["x-sovereign-receipt-signature"], (
            "X-Sovereign-Receipt-Signature must not be empty"
        )
        assert "x-sovereign-tokens-saved" in headers, (
            "Response must carry X-Sovereign-Tokens-Saved header"
        )
        tokens_saved = int(headers["x-sovereign-tokens-saved"])
        assert tokens_saved >= 0, (
            f"X-Sovereign-Tokens-Saved must be a non-negative integer; got {tokens_saved}"
        )

    async def test_middleware_passthrough_for_non_json(self, app_env: FastAPI) -> None:
        """Non-JSON requests bypass sieve processing and reach the route handler unchanged."""
        async with AsyncClient(
            transport=ASGITransport(app=app_env), base_url="http://test"
        ) as client:
            response = await client.post(
                "/ingest",
                content=b"raw bytes",
                headers={"content-type": "text/plain"},
            )

        assert response.status_code == 200
        assert "x-sovereign-receipt-signature" not in response.headers, (
            "Non-JSON requests must not produce a receipt header"
        )

    async def test_middleware_strict_mode_missing_field(
        self, strict_app_env: FastAPI
    ) -> None:
        """In strict_mode, a missing payload_field causes a 422 Unprocessable Entity."""
        async with AsyncClient(
            transport=ASGITransport(app=strict_app_env), base_url="http://test"
        ) as client:
            response = await client.post("/ingest", json={"message": "wrong field name"})

        assert response.status_code == 422, (
            f"strict_mode must return 422 for missing payload_field; got {response.status_code}"
        )

    async def test_middleware_concurrency_isolation(self, app_env: FastAPI) -> None:
        """20 concurrent requests never bleed per-request sieve metrics across contexts.

        Fires 10 filler payloads (each reduces to 'help me now') and 10 clean
        payloads (unchanged, no filler tokens) simultaneously via asyncio.gather.

        Asserts:
        - Each response body reflects the correct minimized form of its *own*
          payload, proving the coroutine-local OptimizationReceipt capture in
          sieve_and_sign() prevents cross-request metric contamination.
        - Every response carries a non-empty X-Sovereign-Receipt-Signature,
          confirming all 20 concurrent signing operations succeeded.
        """
        filler = "hi please just help me now"    # → "help me now" after sieve
        clean = "execute the analysis pipeline"  # → unchanged (no filler tokens)

        payloads = [filler if i % 2 == 0 else clean for i in range(20)]

        async with AsyncClient(
            transport=ASGITransport(app=app_env), base_url="http://test"
        ) as client:
            tasks = [client.post("/ingest", json={"text": p}) for p in payloads]
            responses = await asyncio.gather(*tasks)

        for i, response in enumerate(responses):
            assert response.status_code == 200, (
                f"Request {i}: expected HTTP 200, got {response.status_code}"
            )
            data = response.json()
            expected = "help me now" if i % 2 == 0 else clean
            assert data["received_text"] == expected, (
                f"Request {i}: metric bleed detected — "
                f"expected {expected!r}, got {data['received_text']!r}"
            )
            assert response.headers.get("x-sovereign-receipt-signature"), (
                f"Request {i}: missing or empty X-Sovereign-Receipt-Signature header"
            )

    async def test_middleware_corrects_content_length_header(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """After a sieve pass, Content-Length exactly matches the sieved body's byte length.

        Builds a route handler that echoes back both the Content-Length header it
        observes on the inbound request and the actual byte count of the body it
        reads.  A filler-dense payload (multiple stripped tokens) ensures the body
        shrinks measurably so that a stale header would produce a clearly visible
        mismatch.

        Specifically verifies:
        - The Content-Length header seen inside the route handler equals the string
          representation of len(await request.body()), confirming that
          request.scope["headers"] was patched in-place by the middleware.
        - This guarantees downstream ASGI handlers, routers, and reverse proxies
          never hang waiting for bytes that no longer exist.
        """
        monkeypatch.setenv("SOVEREIGN_NODE_SECRET", "pytest-middleware-cl-header-secret")

        app = FastAPI()
        app.add_middleware(
            SovereignMiddleware,
            signing_key=str(tmp_path / "cl_keys" / "sovereign_identity.pem"),
            payload_field="text",
        )

        @app.post("/measure")
        async def measure(request: Request) -> JSONResponse:
            body_bytes = await request.body()
            return JSONResponse({
                "content_length_header": request.headers.get("content-length"),
                "body_byte_len": len(body_bytes),
            })

        # Multiple filler tokens ensure the body shrinks after sieving.
        # "hi please just certainly of course help me now" → "help me now"
        raw_text = "hi please just certainly of course help me now"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/measure", json={"text": raw_text})

        assert response.status_code == 200
        data = response.json()
        assert data["content_length_header"] == str(data["body_byte_len"]), (
            f"Content-Length header ({data['content_length_header']!r}) must equal "
            f"the true body byte length ({data['body_byte_len']}) after the sieve "
            f"rewrite; a stale value would cause downstream consumers to hang on read"
        )
