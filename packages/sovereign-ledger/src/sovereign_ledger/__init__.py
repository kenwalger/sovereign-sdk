# packages/sovereign-ledger/src/sovereign_ledger/__init__.py
"""sovereign-ledger — Immutable, local-first, hash-chained SQLite provenance engine.

Provides an append-only audit ledger for ForensicReceipt transactions with
cryptographic hash-chain lineage verification and engine-level Write-Side
Custody enforcement.  Zero external dependencies beyond the Python standard
library.
"""

from .engine import SovereignLedger, SovereignStorageError

__version__ = "1.3.0"

__all__ = [
    "SovereignLedger",
    "SovereignStorageError",
]
