import os
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib

from .backend import BacklightError, KeyboardBacklight
from .performance import PerformanceMode, PerformanceModeError
from .window import ClevoControlPanelWindow

APP_ID = "com.mupdike.ClevoControlPanel"

# The tray icon runs as a separate GTK3 process (see tray_helper.py for
# why) rather than in-process, so it needs its own PYTHONPATH to resolve
# this package the same way this process did.
_PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ClevoControlPanelApp(Adw.Application):
    def __init__(self, minimized=False, main_exec=None):
        super().__init__(application_id=APP_ID)
        self._start_minimized = minimized
        self._main_exec = main_exec or "clevo-control-panel"
        self._started = False
        self._tray_process = None

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda a, p: self.quit())
        self.add_action(quit_action)

        self.connect("activate", self._on_activate)
        self.connect("shutdown", self._on_shutdown)

    def _on_activate(self, app):
        window = self.props.active_window
        if not window:
            window = ClevoControlPanelWindow(application=self)
            self._spawn_tray_helper(window)
            self._restore_performance_mode()

        if self._start_minimized and not self._started:
            self._started = True
            return  # stay hidden; the tray helper is already starting

        self._started = True
        window.present()

    def _spawn_tray_helper(self, window):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [_PACKAGE_PARENT, *sys.path]
        )
        try:
            self._tray_process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "clevo_control_panel.tray_helper",
                    self._main_exec,
                ],
                env=env,
                stdout=subprocess.PIPE,
                text=True,
            )
        except OSError:
            return

        GLib.io_add_watch(
            self._tray_process.stdout,
            GLib.PRIORITY_DEFAULT,
            GLib.IO_IN | GLib.IO_HUP,
            self._on_tray_helper_output,
            window,
        )

    def _on_tray_helper_output(self, stdout, condition, window):
        if condition & GLib.IO_HUP:
            return False  # helper exited without ever becoming ready

        line = stdout.readline()
        if line.strip() == "TRAY_READY":
            window.connect("close-request", self._on_window_close_request)
            return False  # done watching; no more setup needed

        return True  # keep watching for the readiness line

    @staticmethod
    def _on_window_close_request(window):
        window.hide()
        return True  # stop the default close/destroy

    def _on_shutdown(self, app):
        if self._tray_process is not None:
            self._tray_process.terminate()

    @staticmethod
    def _restore_performance_mode():
        # Applying this specific EC command during early boot was found to
        # be unsafe (see ~/laptopissues/performance-mode/NOTES.md), so
        # restoring it happens here instead, once, at the first real app
        # startup -- always a fully-booted, interactive-desktop context,
        # the only context this has actually been tested in.
        try:
            backend = KeyboardBacklight()
            performance = PerformanceMode(backend.device_dir)
        except (BacklightError, PerformanceModeError):
            return
        performance.restore_saved_mode()
