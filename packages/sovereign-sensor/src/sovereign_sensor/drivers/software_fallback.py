# packages/sovereign-sensor/src/sovereign_sensor/drivers/software_fallback.py
"""Pure-Python software fallback signing driver.

Used on any platform that does not expose on-chip hardware crypto
acceleration (desktop CI, Raspberry Pi Pico without accelerator, etc.).
Returns the raw 32-byte SHA-256 digest of the preimage.  Hex encoding is
the responsibility of the envelope serialization layer, not the driver.
Not suitable for production custody chains; intended for structural
validation and desktop integration testing only.
"""
import hashlib

from sovereign_sensor.interface import SovereignCryptoDriver


class SoftwareFallbackDriver(SovereignCryptoDriver):
    """SHA-256–based software signing driver for non-accelerated platforms.

    :param private_key_path: Filesystem path reserved for future HMAC key
        material binding.  Stored but not read during this placeholder phase.
    :type private_key_path: str
    """

    def __init__(self, private_key_path: str) -> None:
        self._key_path: str = private_key_path
        self._initialized: bool = False

    def initialize_hardware(self) -> None:
        """Mark the software driver as ready for signing operations.

        :rtype: None
        """
        self._initialized = True

    def algorithm(self) -> str:
        """Return the canonical algorithm identifier for this driver.

        :return: ``"hmac-sha256"`` — the identifier for the SHA-256–based
                 software signing primitive used by this fallback driver.
        :rtype: str
        """
        return "hmac-sha256"

    def sign(self, payload: bytes) -> bytes:
        """Return the raw 32-byte SHA-256 digest of ``payload``.

        Hex encoding is intentionally deferred to the envelope serialization
        layer so that this driver's return type is identical to the raw binary
        output expected from hardware accelerators (Ed25519, ECDSA, HMAC).

        :param payload: Raw preimage bytes to digest.
        :type payload: bytes
        :return: Raw 32-byte SHA-256 digest.
        :rtype: bytes
        """
        return hashlib.sha256(payload).digest()
