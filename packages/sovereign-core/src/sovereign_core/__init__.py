"""
Sovereign Core
Data provenance, cryptographic identity, and ingestion boundaries.
"""

__version__ = "0.1.0"

from sovereign_core.gateway import IngestionBoundary
from sovereign_core.crypto import LocalSigner, ForensicReceipt

__all__ = [
    "IngestionBoundary",
    "LocalSigner",
    "ForensicReceipt",
]