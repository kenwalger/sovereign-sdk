# packages/sovereign-runtime/src/sovereign_runtime/router.py
import sys
import json
import hashlib
from typing import Callable, Any, Dict, Awaitable
from sovereign_core.crypto import SovereignKeyManager, ForensicReceipt
from sovereign_core.gateway import SessionContext

AsyncTool = Callable[[SessionContext, Dict[str, Any]], Awaitable[Any]]

class LocalRuntimeRouter:
    """
    An asynchronous engine that dispatches operations within a stateful session context
    and seals the results into validated, cryptographically verifiable Forensic Receipts.
    """

    def __init__(self, key_manager: SovereignKeyManager | None = None):
        self.key_manager = key_manager or SovereignKeyManager()
        self.tool_registry: Dict[str, AsyncTool] = {}

    def register_tool(self, name: str, func: AsyncTool) -> None:
        self.tool_registry[name] = func

    @staticmethod
    def _calculate_stable_arguments_hash(arguments: Dict[str, Any]) -> str:
        canonical_json = json.dumps(arguments, sort_keys=True, default=str)
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    async def dispatch(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        context: SessionContext
    ) -> ForensicReceipt:
        """
        Executes a target tool asynchronously within a tracking session context.
        Consumes an execution slot unconditionally to preserve total chronological uniqueness
        and prevent duplicate ledger index collisions during external application retry loops.
        """
        if tool_name not in self.tool_registry:
            raise ValueError(f"Tool '{tool_name}' not found in runtime registry.")

        # FIXED: Capture current position and instantly advance context depth
        # unconditionally to defend the timeline index allocation against concurrent/retry races.
        assigned_receipt_index = context.execution_depth
        context.increment_depth()

        # 1. Await cooperative tool execution safely
        try:
            execution_result = await self.tool_registry[tool_name](context, arguments)
            execution_success = True
        except Exception as e:
            execution_result = {"status": "FAILED", "error": str(e)}
            execution_success = False

        # 2. Package deterministic validation payload
        execution_payload = {
            "session_id": context.session_id,
            "execution_depth": assigned_receipt_index,  # Guaranteed unique across retries
            "target_tool": tool_name,
            "arguments_hash": self._calculate_stable_arguments_hash(arguments),
            "result": execution_result
        }

        # 3. Mint the validation signature via sovereign-core
        receipt = self.key_manager.generate_receipt(
            payload=execution_payload,
            metadata={
                "runtime": "async-sovereign-node",
                "py_ver": sys.version.split()[0],
                "execution_success": execution_success
            }
        )

        return receipt