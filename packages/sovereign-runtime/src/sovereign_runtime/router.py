# packages/sovereign-runtime/src/sovereign_runtime/router.py
import sys
import json
import hashlib
from typing import Callable, Any, Dict, Awaitable
from sovereign_core.crypto import SovereignKeyManager, ForensicReceipt
from sovereign_core.gateway import SessionContext

# Define a type for asynchronous local tools
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
        """Registers an asynchronous local capability."""
        self.tool_registry[name] = func

    @staticmethod
    def _calculate_stable_arguments_hash(arguments: Dict[str, Any]) -> str:
        """
        Generates a deterministic, process-stable SHA-256 string hash for arbitrary
        argument dictionaries, sorting keys to avoid dictionary order mismatching
        and handling nested objects safely.
        """
        try:
            # json.dumps with sort_keys converts lists and nested dicts to a stable string representation
            canonical_json = json.dumps(arguments, sort_keys=True, default=str)
            sha256_digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
            return sha256_digest
        except Exception as e:
            # Fallback wrapper for un-serializable objects to prevent runtime crashes
            fallback_bytes = f"fallback_opaque_hash_{repr(arguments)}".encode("utf-8")
            return hashlib.sha256(fallback_bytes).hexdigest()

    async def dispatch(
            self,
            tool_name: str,
            arguments: Dict[str, Any],
            context: SessionContext
    ) -> ForensicReceipt:
        """
        Executes a target tool asynchronously within a tracking session context,
        mutating state and generating an immutable cryptographic execution receipt.
        """
        if tool_name not in self.tool_registry:
            raise ValueError(f"Tool '{tool_name}' not found in runtime registry.")

        context.increment_depth()

        # 1. Await concurrent/local tool execution safely
        try:
            execution_result = await self.tool_registry[tool_name](context, arguments)
        except Exception as e:
            execution_result = {"status": "FAILED", "error": str(e)}

        # 2. Package deterministic validation payload
        execution_payload = {
            "session_id": context.session_id,
            "execution_depth": context.execution_depth,
            "target_tool": tool_name,
            # FIXED: Stable SHA-256 serialization replaces process-unstable hash()
            "arguments_hash": self._calculate_stable_arguments_hash(arguments),
            "result": execution_result
        }

        # 3. Mint the validation signature via sovereign-core
        receipt = self.key_manager.generate_receipt(
            payload=execution_payload,
            metadata={"runtime": "async-sovereign-node", "py_ver": sys.version.split()[0]}
        )

        return receipt