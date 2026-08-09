"""Automatic performance-profile switching based on AC/battery power
state. This is a policy layer on top of PerformanceMode -- it only
decides *which* profile should be active given the current power source
and a user-configured preference; the actual switch (EC + TLP + nvidia-smi
+ Intel iGPU) always goes through PerformanceMode.set_mode() unchanged,
same as a manual click would.

Config is a small system-wide JSON file (not per-user under ~/.config/):
the hardware being controlled is shared by whoever's logged in, so the
preference lives alongside performance-mode.state rather than in a
per-user config. Plain JSON + sysfs, no GTK dependency, so this is safe
to use from the GTK4 main app, the GTK3 tray helper, or the CLI alike.
"""

import json
from pathlib import Path

from .performance import POWER_PROFILE_MODES

CONFIG_DIR = Path("/var/lib/clevo-control-panel")
CONFIG_FILE = CONFIG_DIR / "auto-profile.json"

DEFAULT_AC_PROFILE = "balanced"
DEFAULT_BATTERY_PROFILE = "quiet"


class AutoProfileConfig:
    """enabled/ac_profile/battery_profile, loaded from and saved to
    CONFIG_FILE. Only quiet/balanced/performance are valid choices for
    ac_profile/battery_profile -- Max Fan is a manual cooling override,
    not a real profile, so it's deliberately not selectable here.

    ac_refresh_hz/battery_refresh_hz are optional (default None = "leave
    refresh rate alone") -- existing configs need no migration, and users
    who don't care about this never see it do anything. Applied through
    the same AC/battery transition callback that already applies
    ac_profile/battery_profile (see app.py's _on_power_source_changed),
    since it's the same underlying trigger."""

    def __init__(self):
        self.enabled = False
        self.ac_profile = DEFAULT_AC_PROFILE
        self.battery_profile = DEFAULT_BATTERY_PROFILE
        self.ac_refresh_hz = None
        self.battery_refresh_hz = None
        self.load()

    def load(self):
        try:
            data = json.loads(CONFIG_FILE.read_text())
        except (OSError, ValueError):
            return
        self.enabled = bool(data.get("enabled", self.enabled))
        ac = data.get("ac_profile")
        if ac in POWER_PROFILE_MODES:
            self.ac_profile = ac
        battery = data.get("battery_profile")
        if battery in POWER_PROFILE_MODES:
            self.battery_profile = battery
        self.ac_refresh_hz = _validated_refresh_hz(data.get("ac_refresh_hz"))
        self.battery_refresh_hz = _validated_refresh_hz(data.get("battery_refresh_hz"))

    def save(self):
        data = {
            "enabled": self.enabled,
            "ac_profile": self.ac_profile,
            "battery_profile": self.battery_profile,
            "ac_refresh_hz": self.ac_refresh_hz,
            "battery_refresh_hz": self.battery_refresh_hz,
        }
        try:
            CONFIG_FILE.write_text(json.dumps(data))
        except OSError:
            pass  # best-effort, matches performance.py's own state persistence

    def profile_for(self, on_ac):
        return self.ac_profile if on_ac else self.battery_profile

    def refresh_hz_for(self, on_ac):
        return self.ac_refresh_hz if on_ac else self.battery_refresh_hz


def _validated_refresh_hz(value):
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= 1000 else None


def is_on_ac():
    """Best-effort: find a Mains-type power_supply and report whether
    it's online. Returns None if no such supply exists (e.g. a desktop
    with no battery), meaning auto-switch has nothing to key off of."""
    base = Path("/sys/class/power_supply")
    if not base.is_dir():
        return None
    for supply in base.iterdir():
        try:
            if (supply / "type").read_text().strip() != "Mains":
                continue
            return (supply / "online").read_text().strip() == "1"
        except OSError:
            continue
    return None
