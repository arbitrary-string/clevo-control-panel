"""Display refresh-rate control via GNOME Mutter's own D-Bus interface,
org.gnome.Mutter.DisplayConfig -- the same one GNOME Settings' own
Displays panel uses internally. This is the Wayland-native equivalent of
what TUXEDO Control Center's DisplayRefreshRateWorker does on X11 (its
XDisplayRefreshRateController shells out to X11-only display-mode APIs
and explicitly refuses to run at all under Wayland); this system runs
Wayland, so that approach doesn't transfer. Only works under GNOME/
Mutter, not other Wayland compositors -- an accepted scope limit given
this project already targets a GNOME desktop throughout.

This is a comfort/battery-saving feature, not a safety-critical one:
callers should catch DisplayRefreshRateError and simply skip the
refresh-rate part of whatever they were doing, the same way
performance.py's _apply_power_profile() swallows its own failures rather
than blocking the rest of a profile switch.
"""

import gi

gi.require_version("Gio", "2.0")

from gi.repository import Gio, GLib

DISPLAY_CONFIG_BUS_NAME = "org.gnome.Mutter.DisplayConfig"
DISPLAY_CONFIG_OBJECT_PATH = "/org/gnome/Mutter/DisplayConfig"
DISPLAY_CONFIG_INTERFACE = "org.gnome.Mutter.DisplayConfig"

# Mutter's MetaMonitorsConfigMethod enum. Temporary (not Persistent):
# this feature re-applies on every AC/battery transition anyway, so
# there's no need for Mutter to remember the choice across logins itself.
APPLY_METHOD_TEMPORARY = 1


class DisplayRefreshRateError(RuntimeError):
    pass


class DisplayRefreshRate:
    def __init__(self):
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.NONE,
                None,
                DISPLAY_CONFIG_BUS_NAME,
                DISPLAY_CONFIG_OBJECT_PATH,
                DISPLAY_CONFIG_INTERFACE,
                None,
            )
        except GLib.Error as exc:
            raise DisplayRefreshRateError(str(exc)) from exc

    def _get_state(self):
        try:
            result = self._proxy.call_sync(
                "GetCurrentState", None, Gio.DBusCallFlags.NONE, -1, None
            )
        except GLib.Error as exc:
            raise DisplayRefreshRateError(str(exc)) from exc
        serial, monitors, logical_monitors, _properties = result.unpack()
        return serial, monitors, logical_monitors

    @staticmethod
    def _current_mode(modes):
        for mode_id, width, height, refresh_rate, _scale, _scales, props in modes:
            if props.get("is-current"):
                return mode_id, width, height, refresh_rate
        return None

    @staticmethod
    def _best_mode_id(modes, width, height, target_hz):
        # Excludes VRR-only mode variants ("+vrr" suffix) -- a plain
        # fixed rate is simpler and more predictable for this feature
        # than reasoning about variable refresh ranges.
        candidates = [
            (mode_id, refresh_rate)
            for mode_id, w, h, refresh_rate, *_rest in modes
            if w == width and h == height and "+vrr" not in mode_id
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda c: abs(c[1] - target_hz))[0]

    def get_available_rates(self):
        """Distinct nominal refresh rates (Hz, rounded) available at each
        monitor's current resolution, e.g. [60, 120, 165]."""
        _serial, monitors, _logical = self._get_state()

        rates = set()
        for _spec, modes, _mprops in monitors:
            current = self._current_mode(modes)
            if not current:
                continue
            _mode_id, width, height, _rr = current
            for mode_id, w, h, rr, *_rest in modes:
                if w == width and h == height and "+vrr" not in mode_id:
                    rates.add(round(rr))

        return sorted(rates)

    def set_rate(self, hz):
        """Applies the given refresh rate (Hz) to every connected
        monitor, keeping each one's current resolution, position, scale,
        and orientation unchanged."""
        serial, monitors, logical_monitors = self._get_state()

        modes_by_connector = {}
        target_mode_by_connector = {}
        for (connector, *_ids), modes, _mprops in monitors:
            modes_by_connector[connector] = modes
            current = self._current_mode(modes)
            if not current:
                continue
            _mode_id, width, height, _rr = current
            mode_id = self._best_mode_id(modes, width, height, hz)
            if mode_id:
                target_mode_by_connector[connector] = mode_id

        if not target_mode_by_connector:
            raise DisplayRefreshRateError(f"no monitor mode found close to {hz}Hz")

        new_logical_monitors = []
        for x, y, scale, transform, primary, lm_monitors, _lmprops in logical_monitors:
            new_monitors = []
            for (connector, *_ids) in lm_monitors:
                mode_id = target_mode_by_connector.get(connector)
                if mode_id is None:
                    # Never drop a monitor from the layout entirely --
                    # keep its current mode if we didn't find a target.
                    current = self._current_mode(modes_by_connector.get(connector, []))
                    mode_id = current[0] if current else None
                if mode_id is None:
                    continue
                new_monitors.append((connector, mode_id, {}))
            if new_monitors:
                new_logical_monitors.append((x, y, scale, transform, primary, new_monitors))

        try:
            self._proxy.call_sync(
                "ApplyMonitorsConfig",
                GLib.Variant(
                    "(uua(iiduba(ssa{sv}))a{sv})",
                    (serial, APPLY_METHOD_TEMPORARY, new_logical_monitors, {}),
                ),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
        except GLib.Error as exc:
            raise DisplayRefreshRateError(str(exc)) from exc
