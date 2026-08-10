"""One-shot override for the battery charge-stop threshold: "charge to
100% this time" temporarily raises charge_control_end_threshold to 100,
then app.py's power-source-changed callback restores the previous value
the next time the laptop is running on battery. That covers both a live
AC unplug while the app is running, and a full shutdown/reboot with AC
unplugged in between -- PowerSourceMonitor reports the current power
source once immediately when it's constructed at the next login, whether
or not anything actually changed while the app wasn't running to see it.

Persisted as a tiny JSON file (same directory/style as auto_profile.py's
own state file) specifically so it survives that shutdown/reboot case --
an in-memory-only flag would be lost the moment the app process exits.
"""

import json
from pathlib import Path

CONFIG_DIR = Path("/var/lib/clevo-control-panel")
CONFIG_FILE = CONFIG_DIR / "charge-override.json"


def get_pending_revert_end():
    """The end threshold to restore, or None if no override is pending."""
    try:
        data = json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return None
    value = data.get("revert_to_end")
    return value if isinstance(value, int) and 1 <= value <= 100 else None


def set_pending_revert_end(value):
    try:
        CONFIG_FILE.write_text(json.dumps({"revert_to_end": int(value)}))
    except OSError:
        pass  # best-effort, matches auto_profile.py's own persistence


def clear_pending_revert():
    try:
        CONFIG_FILE.unlink()
    except OSError:
        pass
