"""Direct sysfs access to the Clevo/Tongfang "performance mode" feature:
switches between fan/thermal behaviors (balanced/quiet/performance/
max-fan), similar to the Windows Control Center's mode selector.

Lives on the same platform device as the clevo-acpi keyboard backlight and
battery charge thresholds (added by the clevo-acpi-dkms driver's
performance_mode sysfs attribute), so this reuses that backend's
already-resolved device directory rather than probing separately.

Unlike charge thresholds, there's no hardware read-back for this feature --
the driver (and this class) can only report what was last written, not the
live EC state.
"""

import os
from pathlib import Path

MODES = ["balanced", "quiet", "performance", "max-fan"]


class PerformanceModeError(RuntimeError):
    pass


class PerformanceMode:
    """Balanced/quiet/performance/max-fan switch, if the backing platform
    device exposes it (currently: clevo-acpi with performance_mode
    support)."""

    def __init__(self, device_dir):
        if device_dir is None:
            raise PerformanceModeError(
                "No compatible platform device to look for performance "
                "mode control on."
            )

        self.mode_file = Path(device_dir) / "performance_mode"

        if not self.mode_file.exists():
            raise PerformanceModeError(
                "No performance mode control found on this device. This "
                "feature requires a clevo-acpi driver build with "
                "performance_mode support."
            )

    def get_mode(self):
        return self.mode_file.read_text().strip()

    def set_mode(self, mode):
        if mode not in MODES:
            raise ValueError(f"Invalid performance mode: {mode!r}")
        self.mode_file.write_text(mode)

    def is_writable(self):
        return os.access(self.mode_file, os.W_OK)
