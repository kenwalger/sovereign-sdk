# packages/sovereign-runtime/src/sovereign_runtime/router.py
import sys
import json
import hashlib
from typing import Callable, Any, Dict, Awaitable
from sovereign_core.crypto import SovereignKeyManager, ForensicReceipt
from sovereign_core.gateway import SessionContext

AsyncTool = Callable[[SessionContext, Dict[str, Any]], Awaitable[Any]]
"""Type alias for async tool callables registered with :class:`LocalRuntimeRouter`."""


class LocalRuntimeRouter:
    """Asynchronous execution engine that routes tool calls and seals their results.

    Dispatches registered async tool functions within a
    :class:`~sovereign_core.gateway.SessionContext`, wraps execution outputs in
    cryptographically signed :class:`~sovereign_core.crypto.ForensicReceipt`
    envelopes, and verifies each seal immediately after minting as a
    defense-in-depth measure.

    Args:
        key_manager: Optional pre-configured
            :class:`~sovereign_core.crypto.SovereignKeyManager` instance.  A
            default instance (using ``.keys`` as the key directory) is created
            when ``None``.
    """

    def __init__(self, key_manager: SovereignKeyManager | None = None) -> None:
        """Initialises the router with a key manager and an empty tool registry.

        Args:
            key_manager: Optional :class:`~sovereign_core.crypto.SovereignKeyManager`.
                Defaults to ``SovereignKeyManager()`` when ``None``.
        """
        self.key_manager = key_manager or SovereignKeyManager()
        self.tool_registry: Dict[str, AsyncTool] = {}

    def register_tool(self, name: str, func: AsyncTool) -> None:
        """Registers an async callable under a string name for later dispatch.

        Args:
            name: Unique string key used to identify the tool in
                :meth:`dispatch` calls.
            func: An async callable matching the :data:`AsyncTool` signature:
                ``(SessionContext, Dict[str, Any]) -> Awaitable[Any]``.
        """
        self.tool_registry[name] = func

    @staticmethod
    def _calculate_stable_arguments_hash(arguments: Dict[str, Any]) -> str:
        """Computes a deterministic SHA-256 hex digest of the tool arguments.

        Serialises ``arguments`` with ``json.dumps(sort_keys=True, default=str)``
        before hashing to guarantee identical output regardless of Python dict
        insertion order or complex value types such as lists and nested dicts.

        Args:
            arguments: Arbitrary key/value dictionary of tool input parameters.

        Returns:
            Lowercase hex-encoded SHA-256 digest string (64 characters).
        """
        canonical_json = json.dumps(arguments, sort_keys=True, default=str)
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    async def dispatch(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        context: SessionContext,
    ) -> tuple[ForensicReceipt, Dict[str, Any]]:
        """Executes a registered tool and returns its cryptographically sealed receipt.

        The execution depth counter is incremented **before** the tool is
        invoked, guaranteeing each attempt — including failed or retried ones —
        occupies a unique ledger index.  After minting the receipt, the seal is
        immediately verified as a defense-in-depth guard against key-manager or
        environment integrity faults.

        Args:
            tool_name: Name of the registered tool to invoke.  Must be present
                in :attr:`tool_registry`.
            arguments: Key/value dictionary of input parameters forwarded to
                the tool callable.
            context: Shared :class:`~sovereign_core.gateway.SessionContext`
                carrying inter-tool state and the monotonic depth counter.

        Returns:
            A 2-tuple of:

            - :class:`~sovereign_core.crypto.ForensicReceipt`: The sealed
              provenance envelope whose ``signature`` covers ``metadata``,
              ``payload_hash``, and ``timestamp`` atomically.
            - ``Dict[str, Any]``: The deterministic execution payload dict that
              was signed, returned so callers can independently re-verify the
              receipt via
              :meth:`~sovereign_core.crypto.SovereignKeyManager.verify_receipt`.

        Raises:
            ValueError: If ``tool_name`` is not registered in :attr:`tool_registry`.
            RuntimeError: If receipt seal verification fails immediately after
                minting, indicating a key-manager or environment integrity fault.
        """
        if tool_name not in self.tool_registry:
            raise ValueError(f"Tool '{tool_name}' not found in runtime registry.")

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
            "execution_depth": assigned_receipt_index,
            "target_tool": tool_name,
            "arguments_hash": self._calculate_stable_arguments_hash(arguments),
            "result": execution_result,
        }

        # 3. Mint the receipt — metadata is now inside the cryptographic signature
        receipt = self.key_manager.generate_receipt(
            payload=execution_payload,
            metadata={
                "runtime": "async-sovereign-node",
                "py_ver": sys.version.split()[0],
                "execution_success": execution_success,
            }
        )

        # 4. Defense-in-depth: confirm the seal is valid before returning
        if not SovereignKeyManager.verify_receipt(receipt, execution_payload):
            raise RuntimeError(
                f"Receipt seal verification failed immediately after minting "
                f"(tool='{tool_name}', depth={assigned_receipt_index})."
            )

        return receipt, execution_payload
