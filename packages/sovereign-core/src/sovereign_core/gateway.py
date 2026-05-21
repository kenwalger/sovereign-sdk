# packages/sovereign-core/src/sovereign_core/gateway.py
from typing import Any, Dict
from pydantic import BaseModel, Field


class SessionContext(BaseModel):
    """Ephemeral, stateful execution context shared across sequential tool invocations.

    Maintains a mutable variable store and a monotonically increasing
    ``execution_depth`` counter that acts as a tamper-evident ledger index
    for :class:`~sovereign_core.crypto.ForensicReceipt` sequencing.

    Attributes:
        session_id: Unique string identifier for this execution session.
        variables: Key/value store for inter-tool data dependencies persisted
            across dispatch calls within the same session.
        execution_depth: Running count of dispatch attempts within this
            session.  Incremented unconditionally before each tool call to
            prevent ledger index collisions during retry loops.
    """

    session_id: str
    variables: Dict[str, Any] = Field(default_factory=dict)
    execution_depth: int = 0

    def set(self, key: str, value: Any) -> None:
        """Stores a value under ``key`` in the session variable store.

        Args:
            key: String identifier for the stored value.
            value: Arbitrary value to associate with ``key``.  Overwrites any
                existing value for the same key.
        """
        self.variables[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieves a value from the session variable store.

        Args:
            key: String identifier to look up.
            default: Value returned when ``key`` is absent.  Defaults to
                ``None``.

        Returns:
            The stored value for ``key``, or ``default`` if the key does not
            exist in the variable store.
        """
        return self.variables.get(key, default)

    def increment_depth(self) -> None:
        """Increments the execution depth counter by one.

        Called unconditionally at the start of every
        :meth:`~sovereign_runtime.router.LocalRuntimeRouter.dispatch` call so
        that each attempt — including failed or retried ones — receives a unique
        ledger index, preventing forensic receipt collisions.
        """
        self.execution_depth += 1
