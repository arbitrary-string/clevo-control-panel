"""NVIDIA PRIME GPU-selection mode (Ubuntu's `prime-select`), touched only
to close one real footgun in the GPU MUX mode switcher (see gpu_mode.py):
switching the firmware mux to dGPU-only mode while `prime-select` is still
set to "intel" leaves the graphics stack bound to a GPU that, in dGPU
mode, is no longer wired to the panel at all -- the hardware mux fully
disconnects Intel from every display output, not just adds a second path
-- producing a black screen on the very next boot. Since the OS has no
way to know in advance which driver preference is "compatible" with
dGPU-only mode other than "not exclusively Intel", the fix is to steer
away from "intel" specifically, to "on-demand" (which works correctly in
both MSHybrid and dGPU-only mode) rather than to "nvidia" outright.
"""

import subprocess

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

PRIME_SELECT_BIN = "/usr/bin/prime-select"
SET_SCRIPT = "/usr/lib/clevo-control-panel/set-prime-select.sh"


def query():
    """Returns "intel", "nvidia", "on-demand", or None if prime-select
    isn't installed / isn't applicable on this system. Unprivileged."""
    try:
        result = subprocess.run(
            [PRIME_SELECT_BIN, "query"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def set_on_demand_async(on_finished):
    """Privileged, async. prime-select's own on-demand switch runs
    update-initramfs and update-grub internally, so this can take tens of
    seconds -- this must never block the GTK main loop, hence
    Gio.Subprocess's async form rather than subprocess.run(). on_finished
    (success: bool, message: str) is called back on the main loop once the
    helper process exits; message is empty on success, else the script's
    own stderr (including the sudoers rule not being installed yet)."""
    try:
        proc = Gio.Subprocess.new(
            ["sudo", "-n", SET_SCRIPT, "on-demand"],
            Gio.SubprocessFlags.STDERR_PIPE,
        )
    except GLib.Error as e:
        on_finished(False, str(e))
        return

    def _done(source, result):
        try:
            _ok, _stdout, stderr = source.communicate_utf8_finish(result)
        except GLib.Error as e:
            on_finished(False, str(e))
            return
        if source.get_exit_status() == 0:
            on_finished(True, "")
        else:
            on_finished(False, stderr.strip() or "set-prime-select.sh failed")

    proc.communicate_utf8_async(None, None, _done)
