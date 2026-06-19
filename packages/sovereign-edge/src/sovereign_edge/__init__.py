# packages/sovereign-edge/src/sovereign_edge/__init__.py
"""sovereign-edge — Middleware pipeline bridging sovereign-sensor raw intake and sovereign-ledger immutable storage.

Intercepts sealed sensor envelopes from sovereign-sensor, applies the sovereign-sieve
Prose Tax transformation to produce a verified, minimized payload, and dispatches a
signed ForensicReceipt to sovereign-ledger.  An off-grid JSONL buffer absorbs receipts
when the ledger is temporarily unreachable.  Zero network dependencies; all operations
are strictly local-first.
"""

from .buffer import OffGridBuffer
from .models import EdgeResult, SensorFrame
from .pipeline import EdgePipeline

__version__ = "0.1.0"

__all__ = [
    "EdgePipeline",
    "EdgeResult",
    "OffGridBuffer",
    "SensorFrame",
]
