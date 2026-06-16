# packages/sovereign-sensor/src/sovereign_sensor/__init__.py
"""sovereign-sensor — Bare-metal Write-Side Custody enforcement for MicroPython sensor nodes.

Provides a Hardware Abstraction Layer that selects between on-chip hardware
cryptography and pure-Python software fallbacks at runtime, then seals each
sensor observation into a tamper-evident, minified JSON transmission envelope.
Zero external dependencies; targets ESP32 and Raspberry Pi Pico via MicroPython.
"""
import sys

from .envelope import SovereignEnvelope
from .interface import SovereignCryptoDriver

__version__ = "0.1.0"

__all__ = [
    "SovereignCryptoDriver",
    "SovereignEnvelope",
    "bootstrap_sensor_node",
]


def bootstrap_sensor_node(node_id: str, private_key_path: str) -> SovereignEnvelope:
    """Instantiate and configure a platform-appropriate sensor envelope factory.

    Inspects ``sys.platform`` to route between the ESP32 hardware-accelerated
    driver and the pure-Python software fallback.  The selected driver is
    initialized before the envelope is constructed, guaranteeing the signing
    subsystem is ready on first ``seal()`` call.

    :param node_id: Immutable identifier string for this sensor node.
    :type node_id: str
    :param private_key_path: Filesystem path to the node's private signing key.
    :type private_key_path: str
    :return: A fully configured ``SovereignEnvelope`` bound to the active driver.
    :rtype: SovereignEnvelope
    """
    platform: str = sys.platform.lower()
    if "esp32" in platform:
        from .drivers.esp32_hardware import ESP32HardwareDriver
        driver: SovereignCryptoDriver = ESP32HardwareDriver(private_key_path)
    else:
        from .drivers.software_fallback import SoftwareFallbackDriver
        driver = SoftwareFallbackDriver(private_key_path)
    driver.initialize_hardware()
    return SovereignEnvelope(node_id, driver)
