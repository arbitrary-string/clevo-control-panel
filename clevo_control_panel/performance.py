"""Direct sysfs access to the Clevo/Tongfang "performance mode" feature:
switches between fan/thermal behaviors (balanced/quiet/performance/
max-fan), similar to the Windows Control Center's mode selector.

Lives on the same platform device as the clevo-acpi keyboard backlight and
battery charge thresholds (added by the clevo-acpi-dkms driver's
performance_mode sysfs attribute), so this reuses that backend's
already-resolved device directory rather than probing separately.

Unlike charge thresholds, there's no hardware read-back for this feature --
the driver (and this class) can only report what was last written, not the
live EC state. Reboot persistence is therefore handled entirely at this
layer (see STATE_FILE below), applied once at app startup rather than via
an early-boot systemd service -- restoring this specific EC command that
early was found to be unsafe (see
~/laptopissues/performance-mode/NOTES.md, 2026-08-08 incident writeup).
"""

import os
from pathlib import Path

MODES = ["balanced", "quiet", "performance", "max-fan"]

STATE_DIR = Path("/var/lib/clevo-control-panel")
STATE_FILE = STATE_DIR / "performance-mode.state"


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
        try:
            STATE_FILE.write_text(mode)
        except OSError:
            pass  # best-effort persistence; the hardware write already succeeded

    def restore_saved_mode(self):
        """Apply the last-persisted mode, if any was saved. Meant to be
        called once, early at app/CLI startup -- not from set_mode() itself,
        which already persists on every real change."""
        try:
            saved = STATE_FILE.read_text().strip()
        except OSError:
            return
        if saved in MODES:
            try:
                self.set_mode(saved)
            except OSError:
                pass

    def is_writable(self):
        return os.access(self.mode_file, os.W_OK)
