"""Keeps the "Soft Brightness Plus" GNOME Shell extension in sync with
this board's GPU MUX mode (see gpu_mode.py).

That extension exists to give the brightness slider an effect at all in
MSHybrid mode, where the panel is muxed to the Intel iGPU and this board
has no real hardware brightness control on that path -- it dims via a
software overlay instead. In dGPU mode the panel is muxed directly to the
NVIDIA GPU, where oled_luminance.py's direct DPCD writes give a real,
full-range hardware brightness control -- confirmed live, the two fight
over the same slider and visibly conflict when both are active at once.

Deliberately does this on a plain timer (piggybacking on the same
periodic tick that already re-checks detect_current_mode(), see
window.py) rather than a separate daemon: this only ever matters while a
GNOME session is actually running to enable/disable extensions in, which
this GTK app already only runs inside anyway, and unlike the OLED
brightness daemon there's no root-only device access involved here at
all -- gnome-extensions enable/disable talks to the user's own running
GNOME Shell over the session bus, no privilege boundary to cross.
"""

import subprocess

EXTENSION_UUID = "soft-brightness-plus@joelkitching.com"


def _list_extensions(args):
    try:
        result = subprocess.run(
            ["gnome-extensions", "list"] + args,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.splitlines()


def is_installed():
    installed = _list_extensions([])
    return installed is not None and EXTENSION_UUID in installed


def is_enabled():
    enabled = _list_extensions(["--enabled"])
    return enabled is not None and EXTENSION_UUID in enabled


def set_enabled(enabled):
    action = "enable" if enabled else "disable"
    try:
        subprocess.run(
            ["gnome-extensions", action, EXTENSION_UUID],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def sync_to_gpu_mode(mode):
    """No-op if the extension isn't installed for this user, if GPU mode
    couldn't be determined, or if it's already in the right state --
    only ever calls gnome-extensions enable/disable on an actual change,
    even though this runs on a periodic tick."""
    if mode not in ("mshybrid", "dgpu"):
        return
    if not is_installed():
        return
    want_enabled = mode == "mshybrid"
    if is_enabled() != want_enabled:
        set_enabled(want_enabled)
