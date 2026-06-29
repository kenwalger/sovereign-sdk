# packages/sovereign-sensor/src/sovereign_sensor/__init__.py
"""sovereign-sensor — Bare-metal Write-Side Custody enforcement for MicroPython sensor nodes.

Provides a Hardware Abstraction Layer that selects between on-chip hardware
cryptography and pure-Python software fallbacks at runtime, then seals each
sensor observation into a tamper-evident, versioned, minified JSON transmission
envelope with monotonic replay protection that survives hardware reboots.
Zero external dependencies; targets ESP32 and Raspberry Pi Pico via MicroPython.
"""
import sys

from .envelope import SovereignEnvelope
from .interface import SovereignCryptoDriver

__version__ = "1.3.0"

__all__ = [
    "SovereignCryptoDriver",
    "SovereignEnvelope",
    "bootstrap_sensor_node",
]


def bootstrap_sensor_node(
    node_id: str,
    private_key_path: str,
    sequence_file: str = ".sovereign_sequence",
) -> SovereignEnvelope:
    """Instantiate and configure a platform-appropriate sensor envelope factory.

    Inspects ``sys.platform`` to route between the ESP32 hardware-accelerated
    driver and the pure-Python software fallback.  The selected driver is
    initialized (loading key material from ``private_key_path``) before the
    envelope is constructed, guaranteeing the signing subsystem is ready on
    the first ``seal()`` call.

    If the selected driver's ``initialize_hardware()`` raises
    ``NotImplementedError`` — indicating that hardware crypto acceleration is
    still a skeleton placeholder — a warning is printed and the node falls back
    to ``SoftwareFallbackDriver`` so that the sealing, sequencing, and VFS
    serialization layers remain exercisable on the workbench before
    register-level engineering is complete.

    :param node_id: Immutable identifier string for this sensor node.
    :type node_id: str
    :param private_key_path: Filesystem path to the node's private signing key,
        or ``SoftwareFallbackDriver.MOCK_KEY_SENTINEL`` for desktop testing.
    :type private_key_path: str
    :param sequence_file: VFS path used to persist the monotonic sequence
        counter across reboots.  Defaults to ``".sovereign_sequence"`` in the
        current working directory.
    :type sequence_file: str
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
    _hw_not_implemented: bool = False
    try:
        driver.initialize_hardware()
    except NotImplementedError:
        _hw_not_implemented = True

    if _hw_not_implemented:
        # Emit the warning outside the except block so the fallback initialization
        # path carries no implicit exception context.  Any exception raised by the
        # SoftwareFallbackDriver below will surface with a clean traceback rather
        # than chaining the original NotImplementedError, preventing doubled
        # tracebacks from flooding the serial monitor on constrained MicroPython targets.
        print(
            "WARNING: Hardware crypto accelerator is not yet implemented "
            "(pending low-level register engineering in the next sprint). "
            "Falling back to SoftwareFallbackDriver for this node instance."
        )
        from .drivers.software_fallback import SoftwareFallbackDriver
        driver = SoftwareFallbackDriver(private_key_path)
        driver.initialize_hardware()
    return SovereignEnvelope(node_id, driver, sequence_file=sequence_file)
