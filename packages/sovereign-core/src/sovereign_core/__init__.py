"""
Sovereign Core
Data provenance, cryptographic identity, and ingestion boundaries.
"""

__version__ = "0.1.0"

from .crypto import SovereignKeyManager, ForensicReceipt

__all__ = ["SovereignKeyManager", "ForensicReceipt"]
