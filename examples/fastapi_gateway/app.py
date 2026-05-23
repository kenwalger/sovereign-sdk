"""Runnable example: FastAPI application with SovereignMiddleware.

Start the server:
    SOVEREIGN_NODE_SECRET=<your-secret> uv run uvicorn examples.fastapi_gateway.app:app --reload

Then exercise it with client.py in a second terminal.
"""
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from sovereign_fastapi.middleware import SovereignMiddleware

app = FastAPI(
    title="Sovereign Gateway Example",
    description="Demonstrates transparent Prose Tax sieve-and-sign via SovereignMiddleware.",
)

app.add_middleware(
    SovereignMiddleware,
    signing_key=".keys/example_identity.pem",
    payload_field="text",
)


@app.post("/api/v1/sanitize")
async def sanitize(request: Request) -> JSONResponse:
    """Echo the sieved text back to the caller.

    The middleware strips Prose Tax filler from the inbound 'text' field and
    rewrites request._body before this handler fires, so the value read here
    is already the minimized, cryptographically sealed payload.
    """
    body_bytes = await request.body()
    try:
        data = json.loads(body_bytes)
        sanitized_text = data.get("text", "")
    except (json.JSONDecodeError, AttributeError):
        sanitized_text = body_bytes.decode("utf-8", errors="replace")

    receipt = getattr(request.state, "sovereign_receipt", None)
    return JSONResponse({
        "sanitized": sanitized_text,
        "sovereign_verified": receipt is not None,
        "payload_hash": receipt["payload_hash"] if receipt else None,
    })
