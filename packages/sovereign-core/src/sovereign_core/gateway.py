# packages/sovereign-core/src/sovereign_core/gateway.py
from typing import Any, Dict
from pydantic import BaseModel, Field

class SessionContext(BaseModel):
    """
    Maintains ephemeral local execution state across sequential tool activations.
    """
    session_id: str
    variables: Dict[str, Any] = Field(default_factory=dict)
    execution_depth: int = 0

    def set(self, key: str, value: Any) -> None:
        self.variables[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.variables.get(key, default)

    def increment_depth(self) -> None:
        self.execution_depth += 1
