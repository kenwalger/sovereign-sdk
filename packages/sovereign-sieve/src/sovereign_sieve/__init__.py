# packages/sovereign-sieve/src/sovereign_sieve/__init__.py
"""sovereign-sieve — Zero-dependency Prose Tax token optimization utility.

Exposes the pure synchronous sieve primitives for use in scripts, batch
pipelines, and offline data-processing workers that do not require a web
framework or cryptographic signing layer.
"""

from .sieve import SieveOutput, pure_sieve, sieve_with_metrics

__version__ = "1.1.0"

__all__ = [
    "pure_sieve",
    "sieve_with_metrics",
    "SieveOutput",
]
