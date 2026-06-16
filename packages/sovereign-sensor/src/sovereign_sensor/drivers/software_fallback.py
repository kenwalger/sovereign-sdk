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

from ..interface import SovereignCryptoDriver


class SoftwareFallbackDriver(SovereignCryptoDriver):
    """HMAC-SHA256–based software signing driver for non-accelerated platforms.

    Key material is loaded from ``private_key_path`` during ``initialize_hardware()``.
    If ``private_key_path`` equals ``_MOCK_KEY_SENTINEL``, the fixed deterministic
    ``_MOCK_KEY`` stub is substituted so lightweight desktop tests can operate without
    a provisioned key store.  Any other path that cannot be opened raises
    ``RuntimeError`` immediately — there is no silent key substitution for
    non-sentinel paths.

    :param private_key_path: Filesystem path to the binary HMAC key material, or
        ``SoftwareFallbackDriver._MOCK_KEY_SENTINEL`` to opt into the deterministic
        mock key for desktop testing.
    :type private_key_path: str
    """

    # Explicit opt-in sentinel for desktop testing without a provisioned key store.
    # Any path other than this that cannot be opened raises RuntimeError immediately.
    _MOCK_KEY_SENTINEL: str = "/mock/test_gateway.key"

    # Fixed stub keyed exclusively to _MOCK_KEY_SENTINEL paths.  Never deploy in
    # production — this value provides zero cryptographic uniqueness guarantees.
    _MOCK_KEY: bytes = b"sovereign-sensor-mock-key-v1-do-not-use-in-production"

    def __init__(self, private_key_path: str) -> None:
        self._key_path: str = private_key_path
        self._initialized: bool = False
        self._secret_key: bytes = b""

    def initialize_hardware(self) -> None:
        """Load HMAC key material from the VFS and mark the driver ready.

        If ``private_key_path`` equals ``_MOCK_KEY_SENTINEL``, substitutes the
        fixed deterministic ``_MOCK_KEY`` stub so lightweight desktop tests operate
        without a provisioned key store.  For all other paths, opens the file in
        read-binary mode, re-raises any ``OSError`` as ``RuntimeError``, and raises
        ``ValueError`` if the file exists but contains zero bytes — a zero-length
        key produces an HMAC keyed with ``b""``, which is deterministic across all
        nodes that share the same empty-file failure and provides no cryptographic
        uniqueness.

        :rtype: None
        :raises RuntimeError: If ``private_key_path`` is not the mock sentinel and
            the key file cannot be opened or read.
        :raises ValueError: If the key file exists but contains zero bytes.
        """
        if self._key_path == self._MOCK_KEY_SENTINEL:
            self._secret_key = self._MOCK_KEY
            self._initialized = True
            return
        try:
            with open(self._key_path, "rb") as f:
                key_bytes: bytes = f.read()
        except OSError as exc:
            raise RuntimeError(
                f"Key material loading failed: '{self._key_path}' could not be read: {exc}"
            ) from exc
        if not key_bytes:
            raise ValueError(
                f"Key material at '{self._key_path}' is empty; a non-zero-byte key is "
                "strictly required for secure HMAC authentication."
            )
        self._secret_key = key_bytes
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
        if not self._initialized:
            raise RuntimeError(
                "Driver must be initialized via initialize_hardware() before generating signatures."
            )
        return hmac.new(self._secret_key, payload, hashlib.sha256).digest()
