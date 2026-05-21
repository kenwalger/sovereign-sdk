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

    # Simulate a dynamic fault state that clears out on a second attempt
    retry_count = ctx.get("download_attempts", 0)
    if "fail" in resource_id and retry_count < 1:
        ctx.set("download_attempts", retry_count + 1)
        raise RuntimeError(f"Network Latency Spike: Storage target {resource_id} unreachable.")

    ctx.set("downloaded_resource", f"raw_data_stream_for_{resource_id}")
    return {"status": "SUCCESS", "resource_id": resource_id}


async def analyze_stored_data(ctx: SessionContext, args: dict) -> dict:
    cached_data = ctx.get("downloaded_resource")
    if not cached_data:
        click.echo("⚠️ Standalone mode detected: Context empty. Hydrating baseline diagnostic state...")
        ctx.set("downloaded_resource", "fallback_standalone_telemetry_stream")
        cached_data = ctx.get("downloaded_resource")

    return {"status": "ANALYZED", "bytes_processed": len(cached_data)}


async def execute_runtime_node(tool: str, resource_id: str, session_id: str):
    """Orchestrates runtime routing with verified non-colliding monotonic transaction indexing."""
    router = LocalRuntimeRouter()
    router.register_tool("download", download_secure_data)
    router.register_tool("analyze", analyze_stored_data)

    session = SessionContext(session_id=session_id)

    if tool == "pipeline":
        click.echo(f"🔄 Executing stateful pipeline under session: {session_id}...")

        # Step 1: First Attempt
        receipt_1 = await router.dispatch("download", {"resource_id": resource_id}, session)
        click.echo(
            f"\n🔒 Attempt 1 [download] Completed (Index: {receipt_1['payload_hash'][:8]}... Depth: {session.execution_depth - 1})")

        # If it failed, let's perform an immediate library-level recovery retry loop!
        if not receipt_1["metadata"].get("execution_success", False):
            click.echo(
                "⚠️ Attempt 1 failed under operational conditions. Initializing automated recovery retry loop...")
            receipt_1 = await router.dispatch("download", {"resource_id": resource_id}, session)
            click.echo(f"🔒 Attempt 2 [download Recovery] Completed (Depth: {session.execution_depth - 1})")
            click.echo(json.dumps(receipt_1, indent=2))

            if not receipt_1["metadata"].get("execution_success", False):
                click.echo("\n❌ Pipeline Terminated: Recovery retry exhausted.", err=True)
                raise click.Abort()

        # Step 2: Analyze
        click.echo("\n🔄 Advancing to Step 2 [analyze]...")
        receipt_2 = await router.dispatch("analyze", {}, session)
        click.echo(
            f"\n🔒 Step 2 [analyze] Completed. Pipeline Forensic Receipt Proof (Depth: {session.execution_depth - 1}):")
        click.echo(json.dumps(receipt_2, indent=2))

        if not receipt_2["metadata"].get("execution_success", False):
            click.echo("\n❌ Pipeline Execution Terminated: Step 2 failed.", err=True)
            raise click.Abort()

    else:
        click.echo(f"🔄 Dispatching single tool execution: '{tool}'...")
        receipt = await router.dispatch(tool, {"resource_id": resource_id}, session)
        click.echo("\n🔒 Authenticated Forensic Receipt Proof:")
        click.echo(json.dumps(receipt, indent=2))

        if not receipt["metadata"].get("execution_success", False):
            click.echo(f"\n❌ Operation Failure: Targeted tool '{tool}' failed execution sequence.", err=True)
            raise click.Abort()


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