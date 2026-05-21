# packages/sovereign-runtime/src/sovereign_runtime/__main__.py
import asyncio
import json
import click
from sovereign_core.crypto import SovereignKeyManager
from sovereign_core.gateway import SessionContext
from sovereign_runtime.router import LocalRuntimeRouter


# Async Tool A: Writes data to local session memory
async def download_secure_data(ctx: SessionContext, args: dict) -> dict:
    await asyncio.sleep(0.5)  # Simulate I/O bound disk network latency
    resource_id = args["resource_id"]
    ctx.set("downloaded_resource", f"raw_data_stream_for_{resource_id}")
    return {"status": "SUCCESS", "stored_key": "downloaded_resource"}


# Async Tool B: Consumes data written by Tool A
async def analyze_stored_data(ctx: SessionContext, args: dict) -> dict:
    cached_data = ctx.get("downloaded_resource")
    if not cached_data:
        return {"status": "ERROR", "message": "No data found in session memory."}
    return {"status": "ANALYZED", "length": len(cached_data), "source": cached_data}


async def run_pipeline():
    click.echo("🔮 Starting Async Stateful Engine...")

    router = LocalRuntimeRouter()
    router.register_tool("download", download_secure_data)
    router.register_tool("analyze", analyze_stored_data)

    # Initialize a singular shared tracking context
    session = SessionContext(session_id="session_alpha_2026")

    # Step 1: Execute download tool
    receipt_1 = await router.dispatch("download", {"resource_id": "kernel_mod"}, session)
    click.echo(f"▶️ Tool 1 Complete. Depth: {session.execution_depth}")

    # Step 2: Execute analysis tool (reads memory from Step 1)
    receipt_2 = await router.dispatch("analyze", {}, session)
    click.echo(f"▶️ Tool 2 Complete. Depth: {session.execution_depth}\n")

    click.echo("🔒 Final Pipeline Receipt Payload Proof:")
    click.echo(json.dumps(receipt_2, indent=2))


@click.command()
def main():
    asyncio.run(run_pipeline())


if __name__ == "__main__":
    main()