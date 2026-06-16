# packages/sovereign-sensor/src/sovereign_sensor/drivers/software_fallback.py
"""Pure-Python software fallback signing driver.

Used on any platform that does not expose on-chip hardware crypto
acceleration (desktop CI, Raspberry Pi Pico without accelerator, etc.).
Produces a keyed HMAC-SHA256 digest of the preimage using key material
read from the VFS at initialization time.  Raw bytes are returned;
hex encoding is the responsibility of the envelope serialization layer.
Not suitable for production custody chains; intended for structural
validation and desktop integration testing only.
"""
import hashlib
import hmac

from sovereign_sensor.interface import SovereignCryptoDriver


class SoftwareFallbackDriver(SovereignCryptoDriver):
    """HMAC-SHA256–based software signing driver for non-accelerated platforms.

    Key material is loaded from ``private_key_path`` during ``initialize_hardware()``.
    If the key file is absent (e.g. lightweight mock desktop tests without a
    provisioned key store), a fixed deterministic stub is substituted so the test
    harness remains operational without external secret provisioning.

    :param private_key_path: Filesystem path to the binary HMAC key material.
    :type private_key_path: str
    """

    # Fixed stub used only when the key file cannot be opened.  Never deploy in
    # production — this value provides zero cryptographic uniqueness guarantees.
    _MOCK_KEY: bytes = b"sovereign-sensor-mock-key-v1-do-not-use-in-production"

    def __init__(self, private_key_path: str) -> None:
        self._key_path: str = private_key_path
        self._initialized: bool = False
        self._secret_key: bytes = b""

    def initialize_hardware(self) -> None:
        """Load HMAC key material from the VFS and mark the driver ready.

        Attempts to open ``private_key_path`` in read-binary mode.  If the file
        does not exist or cannot be read (``OSError``), falls back to the class-level
        ``_MOCK_KEY`` stub so lightweight desktop tests without a provisioned key
        store continue to operate.

        :rtype: None
        """
        try:
            with open(self._key_path, "rb") as f:
                self._secret_key = f.read()
        except OSError:
            # Key file absent; substitute a fixed deterministic stub for mock
            # desktop testing only.  Production deployments must provision a real key.
            self._secret_key = self._MOCK_KEY
        self._initialized = True

    def algorithm(self) -> str:
        """Return the canonical algorithm identifier for this driver.

        :return: ``"hmac-sha256"`` — the HMAC-SHA256 keyed signing primitive.
        :rtype: str
        """
        return "hmac-sha256"

    def sign(self, payload: bytes) -> bytes:
        """Return the raw 32-byte HMAC-SHA256 digest of ``payload``.

        The digest is keyed with the secret material loaded during
        ``initialize_hardware()``.  Hex encoding is intentionally deferred to
        the envelope serialization layer so that this driver's return type is
        identical to the raw binary output expected from hardware accelerators.

        :param payload: Raw preimage bytes to authenticate.
        :type payload: bytes
        :return: Raw 32-byte HMAC-SHA256 digest.
        :rtype: bytes
        :raises RuntimeError: If ``initialize_hardware()`` has not been called
            prior to this invocation.
        """
        if not self._initialized or not hasattr(self, "_secret_key"):
            raise RuntimeError(
                "Driver must be initialized via initialize_hardware() before generating signatures."
            )
        return hmac.new(self._secret_key, payload, hashlib.sha256).digest()
