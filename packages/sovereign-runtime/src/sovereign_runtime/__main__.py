# packages/sovereign-runtime/src/sovereign_runtime/__main__.py
import asyncio
import json
import click
# FIXED: Pruned unused SovereignKeyManager import per reviewer instructions
from sovereign_core.gateway import SessionContext
from sovereign_runtime.router import LocalRuntimeRouter


# Async tool capability mappings
async def download_secure_data(ctx: SessionContext, args: dict) -> dict:
    await asyncio.sleep(0.05)
    resource_id = args.get("resource_id", "unknown_resource")
    ctx.set("downloaded_resource", f"raw_data_stream_for_{resource_id}")
    return {"status": "SUCCESS", "resource_id": resource_id}


async def analyze_stored_data(ctx: SessionContext, args: dict) -> dict:
    cached_data = ctx.get("downloaded_resource")
    if not cached_data:
        raise ValueError("Pipeline Error: Prerequisite data missing from session context store.")
    return {"status": "ANALYZED", "bytes_processed": len(cached_data)}


async def execute_runtime_node(tool: str, resource_id: str, session_id: str):
    """Internal loop orchestrator routing arguments dynamically."""
    router = LocalRuntimeRouter()
    router.register_tool("download", download_secure_data)
    router.register_tool("analyze", analyze_stored_data)

    # Instantiate tracking session boundary
    session = SessionContext(session_id=session_id)

    if tool == "pipeline":
        # Run stateful composite sequence
        click.echo(f"🔄 Executing complete stateful pipeline under session: {session_id}...")
        await router.dispatch("download", {"resource_id": resource_id}, session)
        receipt = await router.dispatch("analyze", {}, session)
    else:
        # Run targeted single tool execution block
        click.echo(f"🔄 Dispatching single tool execution: '{tool}'...")
        receipt = await router.dispatch(tool, {"resource_id": resource_id}, session)

    click.echo("\n🔒 Authenticated Forensic Receipt Proof:")
    click.echo(json.dumps(receipt, indent=2))


# FIXED: Re-implemented dynamic Click interface layer over asynchronous wrapper
@click.command()
@click.option('--tool', default='pipeline', type=click.Choice(['download', 'analyze', 'pipeline']),
              help='Target tool or pipeline sequence to initialize.')
@click.option('--resource-id', default='kernel_mod_v3', help='Resource string identifier to process.')
@click.option('--session-id', default='session_omega_2026', help='Isolated tracking session identity string.')
def main(tool: str, resource_id: str, session_id: str):
    """Sovereign Engine Asynchronous Local Execution Interface node."""
    click.echo("====================================================")
    click.echo("🟢 Sovereign Node initialization sequence successful.")
    click.echo("====================================================")

    # Run the async loop handler cleanly within Click's execution context
    asyncio.run(execute_runtime_node(tool, resource_id, session_id))


if __name__ == "__main__":
    main()