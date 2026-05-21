import sys
from typing import Callable, Any, Dict
# Import the primitive directly from our core workspace member
from sovereign_core.crypto import SovereignKeyManager, ForensicReceipt


class LocalRuntimeRouter:
    """
    Dispatches inbound payload payloads to validated tool execution chains,
    wrapping the execution results inside cryptographic forensic receipts.
    """

    def __init__(self, key_manager: SovereignKeyManager | None = None):
        # Fallback to a default managed local key store instance if none injected
        self.key_manager = key_manager or SovereignKeyManager()
        self.tool_registry: Dict[str, Callable[..., Any]] = {}

    def register_tool(self, name: str, func: Callable[..., Any]) -> None:
        """Registers a deterministic or local execution tool into the runtime engine."""
        self.tool_registry[name] = func

    def dispatch(self, tool_name: str, arguments: Dict[str, Any]) -> ForensicReceipt:
        """
        Executes a targeted tool and returns a cryptographically signed receipt of the result.
        """
        if tool_name not in self.tool_registry:
            raise ValueError(f"Execution Error: Tool '{tool_name}' not registered in this node environment.")

        # 1. Execute the local tool routine
        try:
            execution_result = self.tool_registry[tool_name](**arguments)
        except Exception as e:
            execution_result = {"status": "FAILED", "error": str(e)}

        # 2. Package structured target payload data
        execution_payload = {
            "target_tool": tool_name,
            "arguments_hash": hash(tuple(sorted(arguments.items()))),
            "result": execution_result
        }

        # 3. Structural contextual forensic data
        execution_metadata = {
            "runtime_environment": "sovereign-local-node",
            "python_version": sys.version.split()[0],
            "execution_success": "error" not in str(execution_result)
        }

        # 4. Invoke sovereign-core to mint our immutable cryptographic artifact
        receipt = self.key_manager.generate_receipt(
            payload=execution_payload,
            metadata=execution_metadata
        )

        return receipt