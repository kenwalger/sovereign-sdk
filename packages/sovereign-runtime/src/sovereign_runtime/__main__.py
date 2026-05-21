# packages/sovereign-runtime/src/sovereign_runtime/__main__.py
import asyncio
import json
import click
from sovereign_core.gateway import SessionContext
from sovereign_runtime.router import LocalRuntimeRouter


# Async tool capability mappings
async def download_secure_data(ctx: SessionContext, args: dict) -> dict:
    await asyncio.sleep(0.05)
    resource_id = args.get("resource_id", "unknown_resource")
    if "fail" in resource_id:
        raise RuntimeError(f"Network Timeout: Failed to reach remote storage pool for {resource_id}.")
    ctx.set("downloaded_resource", f"raw_data_stream_for_{resource_id}")
    return {"status": "SUCCESS", "resource_id": resource_id}


async def analyze_stored_data(ctx: SessionContext, args: dict) -> dict:
    cached_data = ctx.get("downloaded_resource")
    if not cached_data:
        raise ValueError("Pipeline Pre-Condition Error: Target telemetry data is missing from session state.")
    return {"status": "ANALYZED", "bytes_processed": len(cached_data)}


async def execute_runtime_node(tool: str, resource_id: str, session_id: str):
    """Internal loop orchestrator routing arguments dynamically with fail-fast validation."""
    router = LocalRuntimeRouter()
    router.register_tool("download", download_secure_data)
    router.register_tool("analyze", analyze_stored_data)

    session = SessionContext(session_id=session_id)

    if tool == "pipeline":
        click.echo(f"🔄 Executing complete stateful pipeline under session: {session_id}...")

        # Step 1: Execute download tool and inspect its receipt metrics
        receipt_1 = await router.dispatch("download", {"resource_id": resource_id}, session)
        click.echo("\n🔒 Step 1 [download] Completed. Inspecting Forensic Receipt...")
        click.echo(json.dumps(receipt_1, indent=2))

        # FIXED: Inspect execution success and fail-fast to prevent masked errors
        if not receipt_1["metadata"].get("execution_success", False):
            click.echo("\n❌ Pipeline Execution Terminated: Step 1 failed. Aborting downstream tools.", err=True)
            return

        # Step 2: Run downstream tools only if prerequisite succeeded
        click.echo("\n🔄 Step 1 Verified. Advancing to Step 2 [analyze]...")
        receipt_2 = await router.dispatch("analyze", {}, session)
        click.echo("\n🔒 Step 2 [analyze] Completed. Pipeline Forensic Receipt Proof:")
        click.echo(json.dumps(receipt_2, indent=2))

    else:
        click.echo(f"🔄 Dispatching single tool execution: '{tool}'...")
        receipt = await router.dispatch(tool, {"resource_id": resource_id}, session)
        click.echo("\n🔒 Authenticated Forensic Receipt Proof:")
        click.echo(json.dumps(receipt, indent=2))


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

    asyncio.run(execute_runtime_node(tool, resource_id, session_id))


if __name__ == "__main__":
    main()