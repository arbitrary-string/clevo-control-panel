"""Push notification of AC/battery power-source changes, for auto-switch.

Prefers UPower's system D-Bus service (org.freedesktop.UPower) over
polling: UPower is already running on virtually any GNOME/KDE desktop --
it's what already feeds the battery icon and power settings -- so
subscribing to its OnBattery property costs nothing between events and
reacts the instant AC is plugged/unplugged, instead of within a poll
interval. Falls back to polling /sys/class/power_supply (the same check
CLI/window one-shot reads use) only if UPower isn't present, e.g. a
minimal setup without a full desktop environment.

Only depends on Gio/GLib, not GTK itself, so this stays safe to reuse
from a GTK3 process if it's ever needed there too, same as auto_profile.py.
"""

from gi.repository import Gio, GLib

from .auto_profile import is_on_ac as _sysfs_is_on_ac

UPOWER_BUS_NAME = "org.freedesktop.UPower"
UPOWER_OBJECT_PATH = "/org/freedesktop/UPower"
UPOWER_INTERFACE = "org.freedesktop.UPower"

# Only used as a fallback, when UPower itself isn't available to push
# change notifications.
POLL_FALLBACK_SECONDS = 5

# Distinct from True/False/None (a legitimate "no distinction available"
# result), so the very first report always fires regardless of what it is.
_UNSET = object()


class PowerSourceMonitor:
    """Calls on_change(on_ac) once immediately with the current power
    source, then again every time it changes. on_ac is True/False, or
    None if no AC/battery distinction can be determined at all (e.g. a
    desktop with no battery and no UPower)."""

    def __init__(self, on_change):
        self._on_change = on_change
        self._last_on_ac = _UNSET
        self._proxy = self._connect_upower()

        if self._proxy is not None:
            self._proxy.connect("g-properties-changed", self._on_properties_changed)
            self._report(self._upower_on_ac())
        else:
            self._report(_sysfs_is_on_ac())
            GLib.timeout_add_seconds(POLL_FALLBACK_SECONDS, self._poll)

    @staticmethod
    def _connect_upower():
        try:
            proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM,
                Gio.DBusProxyFlags.NONE,
                None,
                UPOWER_BUS_NAME,
                UPOWER_OBJECT_PATH,
                UPOWER_INTERFACE,
                None,
            )
        except GLib.Error:
            return None
        # A proxy object is handed back even when nothing actually
        # provides this service -- only a populated cached property
        # confirms UPower genuinely answered.
        return proxy if proxy.get_cached_property("OnBattery") is not None else None

    def _upower_on_ac(self):
        on_battery = self._proxy.get_cached_property("OnBattery")
        return None if on_battery is None else not on_battery.get_boolean()

    def _on_properties_changed(self, _proxy, changed, _invalidated):
        if "OnBattery" in changed.unpack():
            self._report(self._upower_on_ac())

    def _poll(self):
        self._report(_sysfs_is_on_ac())
        return GLib.SOURCE_CONTINUE

    def _report(self, on_ac):
        if on_ac != self._last_on_ac:
            self._last_on_ac = on_ac
            self._on_change(on_ac)
