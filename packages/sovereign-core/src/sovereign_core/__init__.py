"""
Sovereign Core
Data provenance, cryptographic identity, and ingestion boundaries.
"""

__version__ = "1.0.0"

from .crypto import SovereignKeyManager, ForensicReceipt
from .gateway import SessionContext

__all__ = ["SovereignKeyManager", "ForensicReceipt", "SessionContext"]
