"""Direct sysfs access to the keyboard backlight LED device.

Two hardware backends are supported, auto-detected at startup:

- system76_acpi: older System76 laptops (e.g. Darter Pro 7). Single RGB
  zone, exposed as one `color` sysfs file directly on the LED classdev.
- clevo-acpi: generic Clevo/Tongfang barebones (including unbranded ones
  the clevo-acpi-dkms package enables support for). Up to 5 independently
  addressable zones (left/center/right/numpad/lightbar), exposed as
  `color_<zone>` files on the LED classdev's parent platform device.
"""

import os
import re
from pathlib import Path

LED_BASE = Path("/sys/class/leds")

HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}$")


class BacklightError(RuntimeError):
    pass


class _System76AcpiBackend:
    """Single-zone backend: one `color` file speaks for the whole keyboard."""

    name = "system76_acpi"
    zones = None

    def __init__(self, led_path):
        self.led_path = led_path
        self.color_file = led_path / "color"
        self.brightness_file = led_path / "brightness"
        self.max_brightness_file = led_path / "max_brightness"

    @property
    def max_brightness(self):
        return int(self.max_brightness_file.read_text().strip())

    def get_color(self, zone=None):
        return self.color_file.read_text().strip().upper()

    def set_color(self, hex_color, zone=None):
        self.color_file.write_text(hex_color)

    def get_brightness(self):
        return int(self.brightness_file.read_text().strip())

    def set_brightness(self, value):
        self.brightness_file.write_text(str(value))

    def is_writable(self):
        return os.access(self.color_file, os.W_OK) and os.access(
            self.brightness_file, os.W_OK
        )


class _ClevoAcpiBackend:
    """Multi-zone backend: independent `color_<zone>` files on the parent
    platform device, plus one shared LED `brightness` file."""

    name = "clevo-acpi"
    zones = ["left", "center", "right", "numpad", "lightbar"]

    def __init__(self, led_path):
        self.led_path = led_path
        self.device_dir = led_path / "device"
        self.brightness_file = led_path / "brightness"
        self.max_brightness_file = led_path / "max_brightness"

    def _zone_file(self, zone):
        return self.device_dir / f"color_{zone}"

    @property
    def max_brightness(self):
        return int(self.max_brightness_file.read_text().strip())

    def get_color(self, zone=None):
        # With no specific zone requested, report the first zone as the
        # representative color (matches what set_color(..., zone=None) does:
        # write the same color to every zone).
        zone = zone or self.zones[0]
        return self._zone_file(zone).read_text().strip().upper()

    def set_color(self, hex_color, zone=None):
        targets = self.zones if zone is None else [zone]
        for z in targets:
            self._zone_file(z).write_text(hex_color)

    def get_brightness(self):
        return int(self.brightness_file.read_text().strip())

    def set_brightness(self, value):
        self.brightness_file.write_text(str(value))

    def is_writable(self):
        return os.access(self.brightness_file, os.W_OK) and all(
            os.access(self._zone_file(z), os.W_OK) for z in self.zones
        )


# A real machine only ever has one of these LED devices present, since they
# come from mutually exclusive kernel drivers for different hardware
# generations, so candidate order doesn't matter.
_BACKEND_CANDIDATES = [
    ("system76_acpi::kbd_backlight", _System76AcpiBackend),
    ("clevo-acpi::kbd_backlight", _ClevoAcpiBackend),
]


class KeyboardBacklight:
    """Facade presenting whichever backend is detected on this machine."""

    def __init__(self):
        self._impl = None
        for led_name, backend_cls in _BACKEND_CANDIDATES:
            led_path = LED_BASE / led_name
            if led_path.exists():
                self._impl = backend_cls(led_path)
                break

        if self._impl is None:
            supported = ", ".join(name for name, _ in _BACKEND_CANDIDATES)
            raise BacklightError(
                "No supported keyboard backlight device found under "
                f"{LED_BASE}. Supported devices: {supported}."
            )

    @property
    def name(self):
        return self._impl.name

    @property
    def zones(self):
        """None for single-zone hardware, otherwise a list of zone names."""
        return self._impl.zones

    @property
    def max_brightness(self):
        return self._impl.max_brightness

    def get_color(self, zone=None):
        return self._impl.get_color(zone).upper()

    def set_color(self, hex_color, zone=None):
        hex_color = hex_color.lstrip("#").upper()
        if not HEX_RE.match(hex_color):
            raise ValueError(f"Invalid hex color: {hex_color!r}")
        if zone is not None and (self.zones is None or zone not in self.zones):
            raise ValueError(f"Invalid zone {zone!r} for backend {self.name!r}")
        self._impl.set_color(hex_color, zone)

    def get_brightness(self):
        return self._impl.get_brightness()

    def set_brightness(self, value):
        value = max(0, min(self.max_brightness, int(value)))
        self._impl.set_brightness(value)

    def is_writable(self):
        return self._impl.is_writable()
