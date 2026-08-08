"""Direct sysfs access to the Clevo/Tongfang battery charge threshold
("flexicharger") feature: stop charging at a configurable upper percentage,
resume at a configurable lower one, to reduce Li-ion battery wear from
staying near 100% or fully depleted for extended periods.

Lives on the same platform device as the clevo-acpi keyboard backlight
(added by the clevo-acpi-dkms driver's charge_control_start_threshold /
charge_control_end_threshold sysfs attributes, following the standard Linux
power_supply naming convention), so this reuses that backend's
already-resolved device directory rather than probing separately.
"""

import os
from pathlib import Path


class ChargeThresholdError(RuntimeError):
    pass


class ChargeThresholds:
    """Standard Linux power_supply-style charge threshold control, if the
    backing platform device exposes it (currently: clevo-acpi with
    flexicharger support)."""

    def __init__(self, device_dir):
        if device_dir is None:
            raise ChargeThresholdError(
                "No compatible platform device to look for battery charge "
                "threshold control on."
            )

        device_dir = Path(device_dir)
        self.start_file = device_dir / "charge_control_start_threshold"
        self.end_file = device_dir / "charge_control_end_threshold"

        if not self.start_file.exists() or not self.end_file.exists():
            raise ChargeThresholdError(
                "No charge threshold control found on this device. This "
                "feature requires a clevo-acpi driver build with battery "
                "flexicharger support."
            )

    def get_start(self):
        return int(self.start_file.read_text().strip())

    def set_start(self, value):
        value = int(value)
        if not 1 <= value <= 100:
            raise ValueError("start threshold must be 1-100")
        self.start_file.write_text(str(value))

    def get_end(self):
        return int(self.end_file.read_text().strip())

    def set_end(self, value):
        value = int(value)
        if not 1 <= value <= 100:
            raise ValueError("end threshold must be 1-100")
        self.end_file.write_text(str(value))

    def is_writable(self):
        return os.access(self.start_file, os.W_OK) and os.access(
            self.end_file, os.W_OK
        )
