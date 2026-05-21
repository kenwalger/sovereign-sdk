# packages/sovereign-runtime/src/sovereign_runtime/__main__.py
import asyncio
import json
import click
from sovereign_core.crypto import SovereignKeyManager
from sovereign_core.gateway import SessionContext
from sovereign_runtime.router import LocalRuntimeRouter

async def download_secure_data(ctx: SessionContext, args: dict) -> dict:
    await asyncio.sleep(0.1)
    resource_id = args["resource_id"]
    ctx.set("downloaded_resource", f"raw_data_stream_for_{resource_id}")
    return {"status": "SUCCESS", "metadata_received": args["nested_config"]}

async def run_pipeline():
    click.echo("🔮 Starting Async Stateful Engine with Deterministic Hashes...")
    router = LocalRuntimeRouter()
    router.register_tool("download", download_secure_data)

    session = SessionContext(session_id="session_stable_omega")

    # REAL-WORLD NESTED PAYLOAD (Previously would raise TypeError with python's hash())
    complex_arguments = {
        "resource_id": "kernel_mod_v2",
        "nested_config": {
            "encryption": "AES-GCM",
            "allowed_nodes": ["node_alpha", "node_beta"],
            "flags": {"force_verify": True}
        }
    }

    receipt = await router.dispatch("download", complex_arguments, session)

    click.echo("\n🔒 Final Production Pipeline Receipt Proof:")
    click.echo(json.dumps(receipt, indent=2))

@click.command()
def main():
    asyncio.run(run_pipeline())

if __name__ == "__main__":
    main()