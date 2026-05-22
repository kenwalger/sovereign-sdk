"""Example client for the Sovereign Gateway FastAPI application.

Usage (with the app server already running):
    uv run python examples/fastapi_gateway/client.py
"""
import json
import sys

import httpx

ENDPOINT = "http://127.0.0.1:8000/api/v1/sanitize"

RAW_PAYLOAD = (
    "hi please just certainly help me execute this analysis pipeline of course, "
    "basically I just wanted to ask you to please run the model training workflow "
    "and simply confirm the output looks good"
)


def main() -> None:
    print("Sovereign Gateway — Example Client")
    print("=" * 50)
    print()
    print("Raw inbound payload (Prose Tax present):")
    print(f"  {RAW_PAYLOAD!r}")
    print()

    try:
        response = httpx.post(ENDPOINT, json={"text": RAW_PAYLOAD}, timeout=10.0)
        response.raise_for_status()
    except httpx.ConnectError:
        print(
            "Connection refused. Is the example server running?\n"
            "Start it with:\n"
            "  SOVEREIGN_NODE_SECRET=<secret> uv run uvicorn "
            "examples.fastapi_gateway.app:app --reload",
            file=sys.stderr,
        )
        sys.exit(1)

    data = response.json()
    print("Optimized output (Prose Tax eliminated by middleware):")
    print(f"  {data['sanitized']!r}")
    print()
    print("Sovereign Receipt Headers:")
    sig = response.headers.get("x-sovereign-receipt-signature", "N/A")
    saved = response.headers.get("x-sovereign-tokens-saved", "N/A")
    print(f"  X-Sovereign-Receipt-Signature : {sig}")
    print(f"  X-Sovereign-Tokens-Saved      : {saved}")
    print()
    if data.get("payload_hash"):
        print(f"  payload_hash : {data['payload_hash']}")
    print()
    print("Sovereignty verified:", data.get("sovereign_verified", False))


if __name__ == "__main__":
    main()
