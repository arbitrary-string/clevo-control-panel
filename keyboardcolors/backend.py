"""Direct sysfs access to the System76 keyboard backlight LED device."""

import os
import re
from pathlib import Path

LED_BASE = Path("/sys/class/leds")
DEVICE_NAME = "system76_acpi::kbd_backlight"

HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}$")


class BacklightError(RuntimeError):
    pass


class KeyboardBacklight:
    def __init__(self):
        self.path = LED_BASE / DEVICE_NAME
        if not self.path.exists():
            raise BacklightError(
                f"No keyboard backlight device found at {self.path}. "
                "This app currently only supports the system76_acpi driver."
            )
        self.color_file = self.path / "color"
        self.brightness_file = self.path / "brightness"
        self.max_brightness_file = self.path / "max_brightness"

    @property
    def max_brightness(self) -> int:
        return int(self.max_brightness_file.read_text().strip())

    def get_color(self) -> str:
        return self.color_file.read_text().strip().upper()

    def set_color(self, hex_color: str) -> None:
        hex_color = hex_color.lstrip("#").upper()
        if not HEX_RE.match(hex_color):
            raise ValueError(f"Invalid hex color: {hex_color!r}")
        self.color_file.write_text(hex_color)

    def get_brightness(self) -> int:
        return int(self.brightness_file.read_text().strip())

    def set_brightness(self, value: int) -> None:
        value = max(0, min(self.max_brightness, int(value)))
        self.brightness_file.write_text(str(value))

    def is_writable(self) -> bool:
        return os.access(self.color_file, os.W_OK) and os.access(
            self.brightness_file, os.W_OK
        )
