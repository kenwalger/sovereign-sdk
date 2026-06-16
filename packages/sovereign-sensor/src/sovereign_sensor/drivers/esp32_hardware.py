# packages/sovereign-sensor/src/sovereign_sensor/drivers/esp32_hardware.py
"""ESP32 hardware-accelerated cryptographic signing driver.

Shell implementation targeting the ESP32's on-chip SHA and RSA/ECC
accelerator peripherals via the MicroPython ``machine`` and ``hashlib``
HAL bindings.  Full low-level register engineering is deferred to the
next sprint; this module establishes the class contract and import
surface so the bootstrap router can bind it without modification.
"""
from ..interface import SovereignCryptoDriver


class ESP32HardwareDriver(SovereignCryptoDriver):
    """Hardware-accelerated signing driver for ESP32 targets.

    On initialization, this driver will configure the on-chip crypto
    accelerator and load key material from the eFuse or external flash
    partition pointed to by ``private_key_path``.

    :param private_key_path: Path to the private key in the ESP32 VFS
        (e.g. ``/flash/keys/node.key``).
    :type private_key_path: str
    """

    def __init__(self, private_key_path: str) -> None:
        self._key_path: str = private_key_path
        self._initialized: bool = False

    def initialize_hardware(self) -> None:
        """Configure the ESP32 hardware crypto accelerator subsystem.

        :rtype: None
        :raises NotImplementedError: Until low-level register engineering is complete.
        """
        raise NotImplementedError(
            "ESP32 hardware crypto drivers are pending low-level register engineering "
            "in the next sprint."
        )

    def algorithm(self) -> str:
        """Return the canonical algorithm identifier for this driver.

        Next sprint: finalize the identifier once the hardware signing
        primitive (ECDSA P-256 via the ESP32 ECC accelerator) is confirmed.

        :return: ``"ecdsa-p256"`` — forward-looking placeholder for the
                 ESP32 on-chip ECC accelerator signing primitive.
        :rtype: str
        """
        return "ecdsa-p256"

    def sign(self, payload: bytes) -> bytes:
        """Produce a hardware-accelerated signature over ``payload``.

        Next sprint: delegate to the ESP32 SHA/ECC hardware engine via
        MicroPython ``hashlib`` acceleration bindings.  Returns raw binary
        signature bytes; hex encoding is the envelope layer's responsibility.

        :param payload: Raw preimage bytes to authenticate.
        :type payload: bytes
        :return: Raw binary signature bytes (no encoding applied).
        :rtype: bytes
        :raises NotImplementedError: Until hardware signing is implemented.
        """
        raise NotImplementedError(
            "ESP32 hardware crypto signing is pending next-sprint implementation."
        )
