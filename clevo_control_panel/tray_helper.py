"""Standalone tray icon helper process for Clevo Control Panel.

Runs as a SEPARATE process from the main GTK4/libadwaita app, on purpose:
this uses the classic GTK3-based AyatanaAppIndicator3, which (unlike the
GLib-only AyatanaAppIndicatorGlib) actually implements the com.canonical.
dbusmenu protocol GNOME's "ubuntu-appindicators" shell extension needs to
show a working menu -- confirmed by testing 2026-08-08, see
~/laptopissues/performance-mode/NOTES.md. GTK3 and GTK4 cannot be loaded
in the same process, hence the separate process, talking to the main app
over D-Bus (GApplication's own built-in remote-actions support) for
Open/Quit rather than sharing any state directly.

Performance mode is handled differently: backend.py/performance.py are
plain Python with no GTK dependency at all, so this reads/writes hardware
state directly, exactly like the CLI does, rather than routing through
the main app. This does mean the main window's own radio buttons won't
notice a change made from here until manually refreshed there, and vice
versa -- an accepted limitation for now, not worth cross-process live
syncing for a quick-access tray menu.

Not meant to be run directly by a user -- spawned by
clevo_control_panel.app on startup. Exits on its own shortly after the
main app's D-Bus name disappears, however that happens (normal quit,
crash, kill).
"""

import json
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")

from gi.repository import AyatanaAppIndicator3 as AppIndicator
from gi.repository import Gio, GLib, Gtk

from .auto_profile import AutoProfileConfig
from .backend import BacklightError, KeyboardBacklight
from .fan import FanControl, FanControlError
from .fan_curve import STATUS_FILE, FanCurveConfig
from .performance import MODES, POWER_PROFILE_MODES, PerformanceMode, PerformanceModeError

APP_ID = "com.mupdike.ClevoControlPanel"
APP_OBJECT_PATH = "/" + APP_ID.replace(".", "/")

MODE_LABELS = {
    "balanced": "Balanced",
    "quiet": "Quiet",
    "performance": "Performance",
    "max-fan": "Max Fan",
}

MODE_REFRESH_INTERVAL_SECONDS = 5


def _activate_remote_action(action_name):
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        actions = Gio.DBusActionGroup.get(bus, APP_ID, APP_OBJECT_PATH)
        actions.activate_action(action_name, None)
    except GLib.Error as e:
        print(f"clevo-control-panel tray helper: {e}", file=sys.stderr)


def _on_open(_item, main_exec):
    # Re-invoking the app's own launcher hits GApplication's standard
    # single-instance path: since an instance is already registered on
    # the session bus, this just remotely activates it (shows the
    # existing window) instead of starting a second process.
    Gio.Subprocess.new([main_exec], Gio.SubprocessFlags.NONE)


def _on_quit(_item):
    _activate_remote_action("quit")


def _open_performance():
    try:
        backend = KeyboardBacklight()
        return PerformanceMode(backend.device_dir)
    except (BacklightError, PerformanceModeError):
        return None


def _on_mode_toggled(item, mode, performance):
    if not item.get_active():
        return
    try:
        performance.set_mode(mode)
    except (OSError, ValueError) as e:
        print(
            f"clevo-control-panel tray helper: couldn't set performance mode: {e}",
            file=sys.stderr,
        )


def _refresh_mode_items(items, performance):
    try:
        current = performance.get_mode()
    except OSError:
        return GLib.SOURCE_CONTINUE
    item = items.get(current)
    if item and not item.get_active():
        item.set_active(True)

    # Same rule as the main window: the three auto-switch-eligible items
    # stay informational but unclickable while auto-switch owns the
    # decision. Max Fan is exempt -- an independent boost toggle, not one
    # of the two profiles auto-switch picks between.
    auto_enabled = AutoProfileConfig().enabled
    for mode, mode_item in items.items():
        mode_item.set_sensitive(not (mode in POWER_PROFILE_MODES and auto_enabled))

    return GLib.SOURCE_CONTINUE


def _add_mode_items(menu, performance):
    try:
        current = performance.get_mode()
    except OSError:
        current = None

    items = {}
    group = None
    for mode in MODES:
        item = Gtk.RadioMenuItem.new_with_label_from_widget(group, MODE_LABELS[mode])
        group = item
        item.set_active(mode == current)
        menu.append(item)
        items[mode] = item

    # Connected only after all initial states are set, so building the
    # group doesn't itself trigger a write.
    for mode, item in items.items():
        item.connect("toggled", _on_mode_toggled, mode, performance)

    GLib.timeout_add_seconds(
        MODE_REFRESH_INTERVAL_SECONDS, _refresh_mode_items, items, performance
    )


def _open_fan_control():
    try:
        backend = KeyboardBacklight()
        return FanControl(backend.device_dir)
    except (BacklightError, FanControlError):
        return None


def _fan_daemon_unhealthy(config):
    """True only when curve mode is on but the daemon doesn't look alive
    -- mirrors cli.py's `fan status` health hint, simplified to a bool
    since the tray only needs to decide whether to show a warning row."""
    if not config.enabled:
        return False
    try:
        status = json.loads(STATUS_FILE.read_text())
    except (OSError, ValueError):
        return True
    return (time.time() - status.get("timestamp", 0)) > 10


def _on_fan_curve_toggled(item):
    # Writes FanCurveConfig directly, the same direct-hardware pattern
    # already used above for performance mode -- no daemon restart is
    # needed, since the daemon itself polls this file for the enabled flag.
    config = FanCurveConfig()
    if item.get_active() == config.enabled:
        return
    config.enabled = item.get_active()
    config.save()


def _refresh_fan_curve_item(item, warning_item):
    config = FanCurveConfig()
    if item.get_active() != config.enabled:
        item.handler_block_by_func(_on_fan_curve_toggled)
        item.set_active(config.enabled)
        item.handler_unblock_by_func(_on_fan_curve_toggled)
    warning_item.set_visible(_fan_daemon_unhealthy(config))
    return GLib.SOURCE_CONTINUE


def _add_fan_curve_items(menu):
    config = FanCurveConfig()

    item = Gtk.CheckMenuItem(label="Fan Curve Enabled")
    item.set_active(config.enabled)
    item.connect("toggled", _on_fan_curve_toggled)
    menu.append(item)

    # Non-interactive warning row, shown only when curve mode is on but
    # the daemon doesn't look healthy -- a Gtk.Menu can't reasonably host
    # a release button or curve editor, but this at least makes a stuck
    # daemon visible from the tray, matching the main window's own
    # daemon-health status row.
    warning_item = Gtk.MenuItem(label="⚠ Fan curve daemon not responding")
    warning_item.set_sensitive(False)
    warning_item.set_visible(_fan_daemon_unhealthy(config))
    menu.append(warning_item)

    GLib.timeout_add_seconds(
        MODE_REFRESH_INTERVAL_SECONDS, _refresh_fan_curve_item, item, warning_item
    )


def main():
    main_exec = sys.argv[1] if len(sys.argv) > 1 else "clevo-control-panel"

    indicator = AppIndicator.Indicator.new(
        "clevo-control-panel",
        "com.mupdike.ClevoControlPanel",
        AppIndicator.IndicatorCategory.APPLICATION_STATUS,
    )
    indicator.set_title("Clevo Control Panel")

    menu = Gtk.Menu()

    open_item = Gtk.MenuItem(label="Open Clevo Control Panel")
    open_item.connect("activate", _on_open, main_exec)
    menu.append(open_item)

    performance = _open_performance()
    if performance is not None:
        menu.append(Gtk.SeparatorMenuItem())
        _add_mode_items(menu, performance)

    fan_control = _open_fan_control()
    if fan_control is not None:
        menu.append(Gtk.SeparatorMenuItem())
        _add_fan_curve_items(menu)

    menu.append(Gtk.SeparatorMenuItem())

    quit_item = Gtk.MenuItem(label="Quit")
    quit_item.connect("activate", _on_quit)
    menu.append(quit_item)

    menu.show_all()
    indicator.set_menu(menu)
    indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

    # Signal readiness to the parent process (watched via a pipe) so it
    # only enables "close hides to tray" once a tray icon genuinely exists
    # -- otherwise closing the window would strand the user with no way
    # to get it back.
    print("TRAY_READY", flush=True)

    def on_name_vanished(_connection, _name):
        Gtk.main_quit()

    Gio.bus_watch_name(
        Gio.BusType.SESSION,
        APP_ID,
        Gio.BusNameWatcherFlags.NONE,
        None,
        on_name_vanished,
    )

    Gtk.main()


if __name__ == "__main__":
    main()
