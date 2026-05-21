# packages/sovereign-runtime/src/sovereign_runtime/__main__.py
import asyncio
import json
import click
from typing import Any, Dict
from sovereign_core.crypto import SovereignKeyManager, ForensicReceipt
from sovereign_core.gateway import SessionContext
from sovereign_runtime.router import LocalRuntimeRouter


async def download_secure_data(ctx: SessionContext, args: Dict[str, Any]) -> Dict[str, Any]:
    """Simulates a secure remote data fetch with transient fault-recovery semantics.

    On the first call for a ``resource_id`` containing the substring ``"fail"``,
    a simulated network error is raised and a retry counter is stored in the
    session context.  A second call for the same resource succeeds, modelling
    real-world transient-fault retry patterns.

    Args:
        ctx: Active :class:`~sovereign_core.gateway.SessionContext` carrying
            inter-tool state such as retry counters and cached payloads.
        args: Tool argument dictionary.  Expected key:

            - ``resource_id`` (``str``): Identifier of the remote resource to
              fetch.  Defaults to ``"unknown_resource"`` when absent.

    Returns:
        Dictionary with keys:

        - ``status`` (``str``): ``"SUCCESS"`` on a successful fetch.
        - ``resource_id`` (``str``): Echo of the requested resource identifier.

    Raises:
        RuntimeError: On the first call when ``resource_id`` contains ``"fail"``,
            simulating a transient network latency spike.
    """
    await asyncio.sleep(0.05)
    resource_id = args.get("resource_id", "unknown_resource")

    retry_count = ctx.get("download_attempts", 0)
    if "fail" in resource_id and retry_count < 1:
        ctx.set("download_attempts", retry_count + 1)
        raise RuntimeError(f"Network Latency Spike: Storage target {resource_id} unreachable.")

    ctx.set("downloaded_resource", f"raw_data_stream_for_{resource_id}")
    return {"status": "SUCCESS", "resource_id": resource_id}


async def analyze_stored_data(ctx: SessionContext, args: Dict[str, Any]) -> Dict[str, Any]:
    """Analyses the data cached in the session context by a preceding download step.

    If the session context is empty (standalone invocation without a prior
    download), a diagnostic baseline telemetry stream is hydrated automatically
    so the tool remains functional for out-of-band testing.

    Args:
        ctx: Active :class:`~sovereign_core.gateway.SessionContext`.  Reads the
            ``downloaded_resource`` variable set by :func:`download_secure_data`.
        args: Tool argument dictionary.  Currently unused; reserved for future
            filtering parameters.

    Returns:
        Dictionary with keys:

        - ``status`` (``str``): ``"ANALYZED"`` on completion.
        - ``bytes_processed`` (``int``): Character length of the analysed data
          stream.
    """
    cached_data = ctx.get("downloaded_resource")
    if not cached_data:
        click.echo("⚠️ Standalone mode detected: Context empty. Hydrating baseline diagnostic state...")
        ctx.set("downloaded_resource", "fallback_standalone_telemetry_stream")
        cached_data = ctx.get("downloaded_resource")

    return {"status": "ANALYZED", "bytes_processed": len(cached_data)}


def _audit_receipt(receipt: ForensicReceipt, payload: Dict[str, Any], label: str) -> None:
    """Cryptographically audits a ForensicReceipt and aborts the process on any failure.

    Performs two sequential checks:

    1. :meth:`~sovereign_core.crypto.SovereignKeyManager.verify_receipt` —
       confirms the Ed25519 signature covers the current envelope contents; a
       ``False`` result indicates post-issuance tampering.
    2. ``metadata["execution_success"]`` — confirms the underlying tool
       invocation completed without an exception.

    Args:
        receipt: The :class:`~sovereign_core.crypto.ForensicReceipt` envelope
            returned by
            :meth:`~sovereign_runtime.router.LocalRuntimeRouter.dispatch`.
        payload: The execution payload dict returned alongside the receipt by
            :meth:`~sovereign_runtime.router.LocalRuntimeRouter.dispatch`, used
            to reconstruct the signed manifest for verification.
        label: Human-readable identifier for the pipeline step used in error
            output, e.g. ``"download recovery"`` or ``"analyze"``.

    Raises:
        click.Abort: If signature verification fails or ``execution_success``
            is ``False``, forcing a non-zero process exit code.
    """
    if not SovereignKeyManager.verify_receipt(receipt, payload):
        click.echo(f"\n❌ Seal verification failed for '{label}': receipt has been tampered with.", err=True)
        raise click.Abort()
    if not receipt["metadata"].get("execution_success", False):
        click.echo(f"\n❌ Execution failure for '{label}': execution_success is False.", err=True)
        raise click.Abort()


async def execute_runtime_node(tool: str, resource_id: str, session_id: str) -> None:
    """Builds the tool registry and orchestrates single-tool or full pipeline execution.

    Constructs a :class:`~sovereign_runtime.router.LocalRuntimeRouter`, registers
    the available async tools, and dispatches either a single tool or a two-step
    download → analyze pipeline depending on ``tool``.  Every receipt is audited
    via :func:`_audit_receipt` before the pipeline advances; a failed or tampered
    receipt terminates the process immediately with a non-zero exit code.

    Args:
        tool: One of ``"download"``, ``"analyze"``, or ``"pipeline"``.  When
            ``"pipeline"``, both tools are executed sequentially with an automatic
            retry on a transient download failure.
        resource_id: String identifier for the remote resource passed to the
            ``"download"`` tool.
        session_id: Unique session identifier used to initialise the
            :class:`~sovereign_core.gateway.SessionContext`.

    Raises:
        click.Abort: Propagated from :func:`_audit_receipt` on verification or
            execution failure.
        ValueError: Propagated from the router when an unregistered tool name
            is dispatched (does not occur under normal CLI usage).
    """
    router = LocalRuntimeRouter()
    router.register_tool("download", download_secure_data)
    router.register_tool("analyze", analyze_stored_data)

    session = SessionContext(session_id=session_id)

    if tool == "pipeline":
        click.echo(f"🔄 Executing stateful pipeline under session: {session_id}...")

        # Step 1: Download (with one automatic recovery retry on transient failure)
        receipt_1, payload_1 = await router.dispatch("download", {"resource_id": resource_id}, session)
        click.echo(
            f"\n🔒 Attempt 1 [download] Completed (Index: {receipt_1['payload_hash'][:8]}... Depth: {session.execution_depth - 1})")

        if not receipt_1["metadata"].get("execution_success", False):
            click.echo(
                "⚠️ Attempt 1 failed under operational conditions. Initializing automated recovery retry loop...")
            receipt_1, payload_1 = await router.dispatch("download", {"resource_id": resource_id}, session)
            click.echo(f"🔒 Attempt 2 [download Recovery] Completed (Depth: {session.execution_depth - 1})")
            click.echo(json.dumps(receipt_1, indent=2))

        _audit_receipt(receipt_1, payload_1, "download")

        # Step 2: Analyze
        click.echo("\n🔄 Advancing to Step 2 [analyze]...")
        receipt_2, payload_2 = await router.dispatch("analyze", {}, session)
        click.echo(
            f"\n🔒 Step 2 [analyze] Completed. Pipeline Forensic Receipt Proof (Depth: {session.execution_depth - 1}):")
        click.echo(json.dumps(receipt_2, indent=2))
        _audit_receipt(receipt_2, payload_2, "analyze")

    else:
        click.echo(f"🔄 Dispatching single tool execution: '{tool}'...")
        receipt, payload = await router.dispatch(tool, {"resource_id": resource_id}, session)
        click.echo("\n🔒 Authenticated Forensic Receipt Proof:")
        click.echo(json.dumps(receipt, indent=2))
        _audit_receipt(receipt, payload, tool)


@click.command()
@click.option('--tool', default='pipeline', type=click.Choice(['download', 'analyze', 'pipeline']),
              help='Target tool or pipeline sequence to initialize.')
@click.option('--resource-id', default='kernel_mod_v3', help='Resource string identifier to process.')
@click.option('--session-id', default='session_omega_2026', help='Isolated tracking session identity string.')
def main(tool: str, resource_id: str, session_id: str) -> None:
    """Sovereign Engine Asynchronous Local Execution Interface node.

    Boots the async runtime, builds a stateful session context, and executes the
    requested tool or pipeline sequence under cryptographic provenance controls.
    Exits with a non-zero status code on any execution or verification failure.

    Args:
        tool: CLI value for ``--tool``.  One of ``download``, ``analyze``, or
            ``pipeline``.
        resource_id: CLI value for ``--resource-id``.  Identifies the remote
            resource passed to the download tool.
        session_id: CLI value for ``--session-id``.  Scopes the ephemeral
            session context for this invocation.
    """
    click.echo("====================================================")
    click.echo("🟢 Sovereign Node initialization sequence successful.")
    click.echo("====================================================")

    asyncio.run(execute_runtime_node(tool, resource_id, session_id))


if __name__ == "__main__":
    main()
