# packages/sovereign-sensor/src/sovereign_sensor/interface.py
"""Hardware Abstraction Layer interface for sovereign cryptographic signing drivers.

Defines the mandatory contract all platform-specific drivers must satisfy.
Intentionally avoids the ``abc`` module to remain compatible with constrained
MicroPython heap environments.
"""


class SovereignCryptoDriver:
    """Abstract HAL contract for a platform-specific signing driver.

    Concrete subclasses must override ``initialize_hardware``, ``sign``, and
    ``algorithm``.  Calling any method on the base class raises
    ``NotImplementedError``, enforcing the contract without pulling in the
    standard-library ``abc`` machinery.
    """

    def initialize_hardware(self) -> None:
        """Prepare the cryptographic subsystem for signing operations.

        :raises NotImplementedError: Always; subclasses must override.
        :rtype: None
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement initialize_hardware()"
        )

    def sign(self, payload: bytes) -> bytes:
        """Produce a raw binary signature over the supplied preimage bytes.

        Implementations must return the unencoded signature bytes exactly as
        produced by the underlying cryptographic primitive (e.g. raw Ed25519
        output, HMAC digest, SHA-256 digest).  Hex encoding or any other
        transport-safe encoding is the exclusive responsibility of the
        envelope serialization layer, not the driver.

        :param payload: Raw preimage bytes to authenticate.
        :type payload: bytes
        :return: Raw binary signature bytes (no encoding applied).
        :rtype: bytes
        :raises NotImplementedError: Always; subclasses must override.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement sign()"
        )

    def algorithm(self) -> str:
        """Return the canonical algorithm identifier for this driver's signing primitive.

        The returned string is embedded verbatim in every sealed transmission
        envelope under the ``alg`` key, enabling receivers to negotiate
        verification strategy without out-of-band configuration.  Identifiers
        must be lowercase, hyphen-delimited strings (e.g. ``"hmac-sha256"``,
        ``"ecdsa-p256"``).

        :return: Canonical algorithm identifier string.
        :rtype: str
        :raises NotImplementedError: Always; subclasses must override.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement algorithm()"
        )
