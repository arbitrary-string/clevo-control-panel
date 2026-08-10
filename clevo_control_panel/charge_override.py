"""One-shot override for the battery charge thresholds: "charge to 100%
this time" temporarily raises *both* charge_control_start_threshold (to
99) and charge_control_end_threshold (to 100), then app.py's power-
source-changed callback restores the previous values the next time the
laptop is running on battery. That covers both a live AC unplug while
the app is running, and a full shutdown/reboot with AC unplugged in
between -- PowerSourceMonitor reports the current power source once
immediately when it's constructed at the next login, whether or not
anything actually changed while the app wasn't running to see it.

Both thresholds are overridden, not just the end one: this hardware's
charge threshold is a hysteresis window, not a simple ceiling -- charging
only *resumes* once capacity drops to/below the *start* threshold, so if
the battery is already sitting stopped somewhere between the old start
and end (a very likely reason to want this button in the first place),
raising the end threshold alone would silently do nothing until capacity
later drifted down on its own (see
~/laptopissues/battery-threshold/NOTES.md's hysteresis finding). Raising
start too guarantees "capacity <= start" is immediately true regardless
of current capacity, forcing an immediate resume.

99, not 100, confirmed live: the EC rejects any write that would leave
start >= end (silently keeping the previous value, not clamping to one),
so start can never actually reach 100 while end is 100 -- and end has to
be raised to 100 *first*, before start can be raised past its old value
at all. See window.py's _on_charge_to_full_clicked for the write order
this requires, and app.py's _maybe_revert_charge_override for why the
reverse order (start, then end) is what's needed to revert.

Persisted as a tiny JSON file (same directory/style as auto_profile.py's
own state file) specifically so it survives that shutdown/reboot case --
an in-memory-only flag would be lost the moment the app process exits.
"""

import json
from pathlib import Path

CONFIG_DIR = Path("/var/lib/clevo-control-panel")
CONFIG_FILE = CONFIG_DIR / "charge-override.json"


def get_pending_revert():
    """(start, end) to restore, or None if no override is pending."""
    try:
        data = json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return None
    start = data.get("revert_to_start")
    end = data.get("revert_to_end")
    if (
        isinstance(start, int)
        and 1 <= start <= 100
        and isinstance(end, int)
        and 1 <= end <= 100
    ):
        return start, end
    return None


def set_pending_revert(start, end):
    try:
        CONFIG_FILE.write_text(
            json.dumps({"revert_to_start": int(start), "revert_to_end": int(end)})
        )
    except OSError:
        pass  # best-effort, matches auto_profile.py's own persistence


def clear_pending_revert():
    try:
        CONFIG_FILE.unlink()
    except OSError:
        pass
