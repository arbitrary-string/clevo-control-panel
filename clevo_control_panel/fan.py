"""Direct sysfs access to the Clevo/Tongfang continuous per-fan duty
control feature: read live duty/temperature and command an arbitrary
manual duty (0-100%), distinct from the discrete balanced/quiet/
performance/max-fan presets in performance.py.

Lives on the same platform device as the rest of clevo-acpi (added by the
clevo-acpi-dkms driver's fanN_duty/fanN_temp/fanN_manual_duty/
fan_manual_active/fan_watchdog_*/fan_release sysfs attributes), so this
reuses that backend's already-resolved device directory rather than
probing separately.

Unlike performance_mode, this genuinely reads live hardware state (there
is a real EC read-back for duty/temperature) -- but a *manually
commanded* duty only persists as long as something keeps "petting" the
kernel's own watchdog (see ping()/release() below and
~/odm-laptop-research/NOTES.md for the full design writeup); if nothing
does, the kernel itself reverts to firmware auto control on its own,
independent of this class or any userspace process still being alive.
This class is a thin, policy-free hardware facade -- see fan_curve.py for
the actual curve-following daemon built on top of it, and cli.py for the
`fan` subcommands, both of which share this same class the way the CLI
and tray helper already share performance.py's PerformanceMode.
"""

import os
from pathlib import Path

MAX_FAN_INDEX = 3


class FanControlError(RuntimeError):
    pass


class FanControl:
    """Per-fan duty/temperature reads and manual duty control, if the
    backing platform device exposes it (currently: clevo-acpi with the
    fan control attributes added alongside performance_mode)."""

    def __init__(self, device_dir):
        if device_dir is None:
            raise FanControlError(
                "No compatible platform device to look for fan control on."
            )

        self.device_dir = Path(device_dir)
        self._fans = [
            n
            for n in range(1, MAX_FAN_INDEX + 1)
            if (self.device_dir / f"fan{n}_duty").exists()
        ]

        if not self._fans:
            raise FanControlError(
                "No fan control attributes found on this device. This "
                "feature requires a clevo-acpi driver build with fan "
                "duty control support."
            )

    def fans(self):
        """Which fan indices (1-based) this board actually has -- e.g.
        [1, 2] on a board with two fans. Matches the kernel driver's own
        per-fan presence detection, since nonexistent fans' attribute
        files simply don't exist."""
        return list(self._fans)

    def get_duty(self, fan):
        return int((self.device_dir / f"fan{fan}_duty").read_text().strip())

    def get_temp(self, fan):
        return int((self.device_dir / f"fan{fan}_temp").read_text().strip())

    def set_manual_duty(self, fan, percent):
        if not 0 <= percent <= 100:
            raise ValueError(f"Invalid fan duty percent: {percent!r}")
        (self.device_dir / f"fan{fan}_manual_duty").write_text(str(percent))

    def ping(self):
        """Extends the kernel watchdog timeout without issuing a real EC
        write -- for control-loop ticks where the computed duty hasn't
        changed from last time, so there's nothing new to command."""
        (self.device_dir / "fan_watchdog_ping").write_text("1")

    def release(self):
        """Immediately hands fan control back to firmware auto control.
        Safe to call any time, including when no manual override is
        active (idempotent on the kernel side)."""
        (self.device_dir / "fan_release").write_text("1")

    def is_manual_active(self):
        return (self.device_dir / "fan_manual_active").read_text().strip() == "1"

    def set_watchdog_timeout_ms(self, ms):
        (self.device_dir / "fan_watchdog_timeout_ms").write_text(str(int(ms)))

    def is_writable(self):
        return os.access(self.device_dir / "fan_release", os.W_OK)
