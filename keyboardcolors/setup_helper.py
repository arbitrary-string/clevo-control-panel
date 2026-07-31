"""Runs the one-time privileged setup script (udev rule, group, systemd units)."""

from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

# Repo checkout (data/install.sh copies files into place then runs setup),
# or an installed .deb (files are already in place; setup-runtime.sh just
# does group/udev/systemd activation).
CANDIDATE_SCRIPTS = [
    Path(__file__).resolve().parent.parent / "data" / "install.sh",
    Path("/usr/lib/keyboardcolors/setup-runtime.sh"),
]


def _find_script():
    for candidate in CANDIDATE_SCRIPTS:
        if candidate.exists():
            return candidate
    return None


def run_setup(on_finished):
    """Launch the setup script via pkexec. on_finished(success: bool, message: str)
    is called on the main loop once the helper process exits."""
    script = _find_script()
    if script is None:
        on_finished(False, "Setup script not found.")
        return

    try:
        proc = Gio.Subprocess.new(
            ["pkexec", str(script), GLib.get_user_name()],
            Gio.SubprocessFlags.STDERR_PIPE,
        )
    except GLib.Error as e:
        on_finished(False, str(e))
        return

    def _done(source, result):
        try:
            ok, _stdout, stderr = source.communicate_utf8_finish(result)
        except GLib.Error as e:
            on_finished(False, str(e))
            return
        if source.get_exit_status() == 0:
            on_finished(True, "Setup completed successfully.")
        else:
            on_finished(False, stderr.strip() or "Setup script failed.")

    proc.communicate_utf8_async(None, None, _done)
