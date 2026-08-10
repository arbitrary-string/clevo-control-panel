"""Main application window."""

import collections
import json
import math
import time
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_foreign("cairo")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import colors as colors_mod
from . import config
from . import sensors
from . import setup_helper
from .auto_profile import AutoProfileConfig, is_on_ac
from .backend import BacklightError, KeyboardBacklight
from .battery import ChargeThresholdError, ChargeThresholds
from .charge_override import get_pending_revert, set_pending_revert
from .display import DisplayRefreshRate, DisplayRefreshRateError
from .fan import FanControl, FanControlError
from .fan_curve import FanCurveConfig, validate_curve
from .performance import PerformanceMode, PerformanceModeError

# Retrofuturistic instrument-panel palette for the Dashboard page only --
# deliberately different from the rest of the app's normal libadwaita
# look. Colors chosen for a phosphor-CRT feel: near-black background,
# green for nominal readings, amber for a caution zone, red-orange once
# a value is genuinely high.
DASHBOARD_BG = (0.04, 0.06, 0.04)
DASHBOARD_GREEN = (0.2, 1.0, 0.4)
DASHBOARD_AMBER = (1.0, 0.69, 0.0)
DASHBOARD_RED = (1.0, 0.3, 0.2)
DASHBOARD_GRID = (0.2, 0.4, 0.25)

# History window: 10 minutes at a 2s sample rate. Shorter than a longer
# 30-minute window some reference dashboards use -- chosen to keep a
# fixed-width trace legible rather than compressing a long window into a
# few hundred pixels.
DASHBOARD_HISTORY_LENGTH = 300
DASHBOARD_REFRESH_MS = 2000

# Order matters: index N here corresponds to index N in the AC/battery
# Gtk.DropDown widgets. Max Fan is deliberately excluded -- it's a manual
# cooling override, not a real profile to auto-switch into.
AUTO_SWITCH_MODES = ["balanced", "quiet", "performance"]
AUTO_SWITCH_LABELS = ["Balanced", "Quiet", "Performance"]

# "Don't change" (index 0, value None) plus a small, sensible set of
# common panel refresh rates -- the actual dropdown is trimmed down to
# whichever of these (plus the panel's own reported rates) are really
# available, via DisplayRefreshRate.get_available_rates().
REFRESH_RATE_DONT_CHANGE_LABEL = "Don't change"

# How stale the fan daemon's status file can be before the GUI treats it
# as "not responding" -- generous versus the daemon's own ~2s write
# cadence, so ordinary scheduling jitter doesn't false-positive.
FAN_DAEMON_STALE_SECONDS = 10


class ClevoControlPanelWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Clevo Control Panel")
        self.set_default_size(760, 720)

        try:
            self.backend = KeyboardBacklight()
            self._backend_error = None
        except BacklightError as e:
            self.backend = None
            self._backend_error = str(e)

        try:
            self.battery = ChargeThresholds(
                self.backend.device_dir if self.backend else None
            )
            self._battery_error = None
        except ChargeThresholdError as e:
            self.battery = None
            self._battery_error = str(e)

        try:
            self.performance = PerformanceMode(
                self.backend.device_dir if self.backend else None
            )
            self._performance_error = None
        except PerformanceModeError as e:
            self.performance = None
            self._performance_error = str(e)

        try:
            self.fan_control = FanControl(
                self.backend.device_dir if self.backend else None
            )
            self._fan_control_error = None
        except FanControlError as e:
            self.fan_control = None
            self._fan_control_error = str(e)

        try:
            self._display_refresh = DisplayRefreshRate()
        except DisplayRefreshRateError:
            self._display_refresh = None

        self._auto_config = AutoProfileConfig()
        self._fan_curve_config = FanCurveConfig()
        self._last_fan_health = "not_needed"
        self._available_refresh_rates = []
        if self._display_refresh:
            try:
                self._available_refresh_rates = (
                    self._display_refresh.get_available_rates()
                )
            except DisplayRefreshRateError:
                pass

        self._brightness_debounce_id = None
        self._threshold_debounce_id = None
        self._current_hex = "000000"

        # Dashboard history buffers + its own refresh timer, started/
        # stopped as the Dashboard page becomes visible/hidden rather
        # than running unconditionally like the app's other 5s checks --
        # the underlying reads (nvidia-smi, scanning /proc for the Xe
        # utilization technique) are too expensive to run when nobody's
        # looking at them.
        self._dashboard_timer_id = None
        self._dashboard_history = {
            "cpu_temp": collections.deque(maxlen=DASHBOARD_HISTORY_LENGTH),
            "gpu_temp": collections.deque(maxlen=DASHBOARD_HISTORY_LENGTH),
            "fan_duty": collections.deque(maxlen=DASHBOARD_HISTORY_LENGTH),
        }

        self._build_ui()
        self._refresh_status()
        self._refresh_system_status()
        self._refresh_battery_status()
        self._refresh_performance_status()

        # Performance mode can change from outside this window (the tray
        # menu's quick-select, or the CLI), unlike keyboard/battery, which
        # are normally only ever changed from within this same app -- so
        # this one needs to actively stay in sync, not just refresh once.
        # Matches the tray helper's own refresh cadence.
        GLib.timeout_add_seconds(5, self._periodic_performance_refresh)

        # Extra safety net alongside the page-navigation start/stop in
        # _on_sidebar_row_selected: if the whole window gets hidden to the
        # tray while Dashboard happens to be the selected page, stop
        # polling rather than continuing to run its expensive reads
        # against a window nobody can see.
        self.connect("notify::visible", self._on_window_visibility_changed)

    def _on_window_visibility_changed(self, _window, _pspec):
        if not self.get_visible() and self._dashboard_timer_id is not None:
            GLib.source_remove(self._dashboard_timer_id)
            self._dashboard_timer_id = None

    # ---- Top-level UI construction ----

    def _build_ui(self):
        # Built before the sidebar, since selecting the initial sidebar row
        # fires _on_sidebar_row_selected() immediately, which needs these.
        self.keyboard_page = self._build_keyboard_page()
        self.battery_page = self._build_battery_page()
        self.performance_page = self._build_performance_page()
        self.dashboard_page = self._build_dashboard_page()

        self.split_view = Adw.NavigationSplitView()
        self.split_view.set_min_sidebar_width(180)
        self.split_view.set_max_sidebar_width(220)
        # Selecting the sidebar's initial row (below) already sets the
        # right content via _on_sidebar_row_selected -- no need to also
        # set it here.
        self.split_view.set_sidebar(self._build_sidebar())

        self.set_content(self.split_view)

        self.settings_window = self._build_settings_window()

        self._rebuild_favorites()

    def _build_sidebar(self):
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        listbox = Gtk.ListBox()
        listbox.add_css_class("navigation-sidebar")
        listbox.append(
            self._make_sidebar_row("utilities-system-monitor-symbolic", "Dashboard")
        )
        listbox.append(
            self._make_sidebar_row(
                "power-profile-performance-symbolic", "Performance"
            )
        )
        listbox.append(self._make_sidebar_row("battery-good-symbolic", "Battery"))
        listbox.append(self._make_sidebar_row("input-keyboard-symbolic", "Keyboard"))
        listbox.connect("row-selected", self._on_sidebar_row_selected)
        listbox.select_row(listbox.get_row_at_index(0))

        toolbar_view.set_content(listbox)

        page = Adw.NavigationPage(title="Clevo Control Panel")
        page.set_child(toolbar_view)
        return page

    @staticmethod
    def _make_sidebar_row(icon_name, title):
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.append(Gtk.Image.new_from_icon_name(icon_name))
        box.append(Gtk.Label(label=title, xalign=0))
        row.set_child(box)
        row.page_name = title.lower()
        return row

    def _on_sidebar_row_selected(self, _listbox, row):
        if row is None:
            return
        pages = {
            "keyboard": self.keyboard_page,
            "battery": self.battery_page,
            "performance": self.performance_page,
            "dashboard": self.dashboard_page,
        }
        self.split_view.set_content(pages[row.page_name])

        # The Dashboard's refresh involves genuinely expensive reads
        # (spawning nvidia-smi, scanning /proc for the Xe utilization
        # technique) unlike this app's other cheap periodic checks, so
        # its timer only runs while the page is actually the one shown --
        # started fresh on every navigation to it (never left stacked),
        # stopped the moment you navigate away.
        if self._dashboard_timer_id is not None:
            GLib.source_remove(self._dashboard_timer_id)
            self._dashboard_timer_id = None
        if row.page_name == "dashboard":
            self._refresh_dashboard()
            self._dashboard_timer_id = GLib.timeout_add(
                DASHBOARD_REFRESH_MS, self._on_dashboard_timer_tick
            )

    def _on_dashboard_timer_tick(self):
        self._refresh_dashboard()
        return GLib.SOURCE_CONTINUE

    def _wrap_group(self, title, child_widget):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        label = Gtk.Label(label=title, xalign=0)
        label.add_css_class("heading")
        box.append(label)
        box.append(child_widget)
        return box

    # ---- Keyboard page ----

    def _build_keyboard_page(self):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Keyboard"))

        settings_btn = Gtk.Button(icon_name="preferences-system-symbolic")
        settings_btn.set_tooltip_text("System Integration Settings")
        settings_btn.connect("clicked", self._on_open_settings)
        header.pack_end(settings_btn)
        toolbar_view.add_top_bar(header)

        self.banner = Adw.Banner(title="")
        toolbar_view.add_top_bar(self.banner)

        self.toast_overlay = Adw.ToastOverlay()

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        clamp = Adw.Clamp(maximum_size=520)
        clamp.set_margin_top(12)
        clamp.set_margin_bottom(24)
        clamp.set_margin_start(12)
        clamp.set_margin_end(12)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        clamp.set_child(main_box)
        scroller.set_child(clamp)
        self.toast_overlay.set_child(scroller)
        toolbar_view.set_content(self.toast_overlay)

        main_box.append(self._build_current_section())

        for group_name, items in colors_mod.PRESETS:
            main_box.append(self._build_swatch_group(group_name, items))

        self.favorites_box = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            max_children_per_line=8,
            row_spacing=8,
            column_spacing=8,
            homogeneous=False,
        )
        self.favorites_section = self._wrap_group("Favorites", self.favorites_box)
        main_box.append(self.favorites_section)

        main_box.append(self._build_custom_section())
        main_box.append(self._build_brightness_section())

        page = Adw.NavigationPage(title="Keyboard")
        page.set_child(toolbar_view)
        return page

    def _build_current_section(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.current_area = Gtk.DrawingArea()
        self.current_area.set_content_width(48)
        self.current_area.set_content_height(48)
        self.current_area.set_draw_func(
            lambda area, cr, w, h: self._paint_swatch(cr, w, h, self._current_hex)
        )
        box.append(self.current_area)

        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        current_label = Gtk.Label(label="Current color", xalign=0)
        current_label.add_css_class("heading")
        self.current_hex_label = Gtk.Label(label="—", xalign=0)
        self.current_hex_label.add_css_class("dim-label")
        info_box.append(current_label)
        info_box.append(self.current_hex_label)
        box.append(info_box)

        spacer = Gtk.Box(hexpand=True)
        box.append(spacer)

        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.set_valign(Gtk.Align.CENTER)
        refresh_btn.set_tooltip_text("Refresh from hardware")
        refresh_btn.connect("clicked", lambda b: self._refresh_status())
        box.append(refresh_btn)
        return box

    def _build_swatch_group(self, title, items):
        flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            max_children_per_line=8,
            row_spacing=8,
            column_spacing=8,
            homogeneous=False,
        )
        for name, hex_color in items:
            flow.append(self._make_swatch(hex_color, name))
        return self._wrap_group(title, flow)

    def _make_swatch(self, hex_color, tooltip, size=40):
        area = Gtk.DrawingArea()
        area.set_content_width(size)
        area.set_content_height(size)
        area.set_draw_func(
            lambda a, cr, w, h, hc=hex_color: self._paint_swatch(cr, w, h, hc)
        )

        button = Gtk.Button()
        button.set_child(area)
        button.add_css_class("flat")
        button.set_has_frame(False)
        button.set_tooltip_text(f"{tooltip} (#{hex_color})")
        button.connect("clicked", lambda b, hc=hex_color: self.apply_color(hc))
        return button

    @staticmethod
    def _paint_swatch(cr, width, height, hex_color):
        r = int(hex_color[0:2], 16) / 255
        g = int(hex_color[2:4], 16) / 255
        b = int(hex_color[4:6], 16) / 255

        radius = min(8, width / 2, height / 2)
        cr.new_sub_path()
        cr.arc(width - radius, radius, radius, -math.pi / 2, 0)
        cr.arc(width - radius, height - radius, radius, 0, math.pi / 2)
        cr.arc(radius, height - radius, radius, math.pi / 2, math.pi)
        cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()

        cr.set_source_rgb(r, g, b)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.2)
        cr.set_line_width(1)
        cr.stroke()

    def _build_custom_section(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        dialog = Gtk.ColorDialog(title="Choose a keyboard color", with_alpha=False)
        self.color_button = Gtk.ColorDialogButton(dialog=dialog)
        self._color_notify_handler_id = self.color_button.connect(
            "notify::rgba", self._on_custom_color_chosen
        )
        box.append(self.color_button)

        add_fav_btn = Gtk.Button(label="Add to Favorites")
        add_fav_btn.connect("clicked", self._on_add_favorite)
        box.append(add_fav_btn)
        return self._wrap_group("Custom Color", box)

    def _build_brightness_section(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        max_b = self.backend.max_brightness if self.backend else 255
        self.brightness_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, max_b, 1
        )
        self.brightness_scale.set_hexpand(True)
        self.brightness_scale.set_draw_value(True)
        self.brightness_scale.connect("value-changed", self._on_brightness_changed)
        box.append(self.brightness_scale)
        return self._wrap_group("Brightness", box)

    # ---- Battery page ----

    def _build_battery_page(self):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Battery"))

        settings_btn = Gtk.Button(icon_name="preferences-system-symbolic")
        settings_btn.set_tooltip_text("System Integration Settings")
        settings_btn.connect("clicked", self._on_open_settings)
        header.pack_end(settings_btn)
        toolbar_view.add_top_bar(header)

        self.battery_banner = Adw.Banner(title="")
        toolbar_view.add_top_bar(self.battery_banner)

        self.battery_toast_overlay = Adw.ToastOverlay()

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        clamp = Adw.Clamp(maximum_size=520)
        clamp.set_margin_top(12)
        clamp.set_margin_bottom(24)
        clamp.set_margin_start(12)
        clamp.set_margin_end(12)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        clamp.set_child(main_box)
        scroller.set_child(clamp)
        self.battery_toast_overlay.set_child(scroller)
        toolbar_view.set_content(self.battery_toast_overlay)

        main_box.append(self._build_threshold_section())

        page = Adw.NavigationPage(title="Battery")
        page.set_child(toolbar_view)
        return page

    def _build_threshold_section(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        explainer = Gtk.Label(
            label=(
                "Limit the battery's charge range to reduce long-term wear. "
                "Charging resumes at the start percentage and stops at the "
                "end percentage."
            ),
            xalign=0,
            wrap=True,
        )
        explainer.add_css_class("dim-label")
        box.append(explainer)

        grid = Gtk.Grid(row_spacing=12, column_spacing=12)

        start_label = Gtk.Label(label="Start charging at", xalign=0)
        grid.attach(start_label, 0, 0, 1, 1)
        self.start_threshold_spin = Gtk.SpinButton.new_with_range(1, 100, 1)
        self.start_threshold_spin.set_hexpand(True)
        self.start_threshold_spin.connect(
            "value-changed", self._on_threshold_changed
        )
        grid.attach(self.start_threshold_spin, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="%"), 2, 0, 1, 1)

        end_label = Gtk.Label(label="Stop charging at", xalign=0)
        grid.attach(end_label, 0, 1, 1, 1)
        self.end_threshold_spin = Gtk.SpinButton.new_with_range(1, 100, 1)
        self.end_threshold_spin.set_hexpand(True)
        self.end_threshold_spin.connect("value-changed", self._on_threshold_changed)
        grid.attach(self.end_threshold_spin, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="%"), 2, 1, 1, 1)

        box.append(grid)

        charge_full_btn = Gtk.Button(label="Charge to 100% This Time")
        charge_full_btn.set_halign(Gtk.Align.START)
        charge_full_btn.add_css_class("suggested-action")
        charge_full_btn.connect("clicked", self._on_charge_to_full_clicked)
        box.append(charge_full_btn)

        refresh_btn = Gtk.Button(label="Refresh from hardware")
        refresh_btn.set_halign(Gtk.Align.START)
        refresh_btn.connect("clicked", lambda b: self._refresh_battery_status())
        box.append(refresh_btn)

        return self._wrap_group("Charge Thresholds", box)

    def _on_charge_to_full_clicked(self, _btn):
        if not self.battery:
            return
        if get_pending_revert() is not None:
            self._battery_toast("Already charging to 100% this time.")
            return
        try:
            current_start = self.battery.get_start()
            current_end = self.battery.get_end()
        except OSError as e:
            self._battery_toast(f"Couldn't read current charge thresholds: {e}")
            return

        set_pending_revert(current_start, current_end)
        try:
            # Overrides *both* thresholds, not just end: this hardware's
            # threshold is a hysteresis window, not a simple ceiling --
            # charging only resumes once capacity drops to/below start,
            # so if the battery is already sitting stopped between the
            # old start and end (the most likely reason to click this),
            # raising end alone would silently do nothing.
            #
            # Order and the 99-not-100 value both matter, confirmed live:
            # the EC rejects any write that would leave start >= end (the
            # previous value is silently kept, not clamped), so end must
            # be raised first, and start can only ever reach 99, never
            # 100, once end is 100. Applied directly rather than through
            # the spin buttons' debounced _apply_thresholds(), which
            # always writes start before end and would silently reject
            # the start write here.
            self.battery.set_end(100)
            self.battery.set_start(99)
        except (OSError, ValueError) as e:
            self._battery_toast(f"Couldn't set charge thresholds: {e}")
            return

        self.end_threshold_spin.set_value(100)
        self.start_threshold_spin.set_value(99)
        self._battery_toast(
            f"Charging to 100% this time -- reverts to "
            f"{current_start}/{current_end}% once unplugged."
        )

    def _on_threshold_changed(self, _spin):
        if not self.battery:
            return
        if self._threshold_debounce_id is not None:
            GLib.source_remove(self._threshold_debounce_id)
        start = int(self.start_threshold_spin.get_value())
        end = int(self.end_threshold_spin.get_value())
        self._threshold_debounce_id = GLib.timeout_add(
            300, self._apply_thresholds, start, end
        )

    def _apply_thresholds(self, start, end):
        self._threshold_debounce_id = None
        try:
            self.battery.set_start(start)
            self.battery.set_end(end)
        except (OSError, ValueError) as e:
            self._battery_toast(f"Couldn't set charge thresholds: {e}")
        return GLib.SOURCE_REMOVE

    def _refresh_battery_status(self):
        if not self.battery:
            self.battery_banner.set_title(
                self._battery_error
                or "No compatible battery charge threshold control found."
            )
            self.battery_banner.set_revealed(True)
            self.start_threshold_spin.set_sensitive(False)
            self.end_threshold_spin.set_sensitive(False)
            return

        try:
            start = self.battery.get_start()
            end = self.battery.get_end()
        except OSError as e:
            self._battery_toast(f"Couldn't read charge thresholds: {e}")
            return

        for spin, value in (
            (self.start_threshold_spin, start),
            (self.end_threshold_spin, end),
        ):
            if not spin.has_focus():
                spin.set_value(value)

        writable = self.battery.is_writable()
        self.start_threshold_spin.set_sensitive(writable)
        self.end_threshold_spin.set_sensitive(writable)
        if writable:
            self.battery_banner.set_revealed(False)
        else:
            self.battery_banner.set_title(
                "No write access to charge threshold control yet. Run "
                "system setup below."
            )
            self.battery_banner.set_revealed(True)

    def _battery_toast(self, message):
        self.battery_toast_overlay.add_toast(Adw.Toast(title=message, timeout=4))

    # ---- Performance page ----

    def _build_performance_page(self):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Performance"))

        settings_btn = Gtk.Button(icon_name="preferences-system-symbolic")
        settings_btn.set_tooltip_text("System Integration Settings")
        settings_btn.connect("clicked", self._on_open_settings)
        header.pack_end(settings_btn)
        toolbar_view.add_top_bar(header)

        self.performance_banner = Adw.Banner(title="")
        self.performance_banner.connect(
            "button-clicked", lambda b: self._on_fan_release_clicked(b)
        )
        toolbar_view.add_top_bar(self.performance_banner)

        self.performance_toast_overlay = Adw.ToastOverlay()

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        clamp = Adw.Clamp(maximum_size=520)
        clamp.set_margin_top(12)
        clamp.set_margin_bottom(24)
        clamp.set_margin_start(12)
        clamp.set_margin_end(12)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        clamp.set_child(main_box)
        scroller.set_child(clamp)
        self.performance_toast_overlay.set_child(scroller)
        toolbar_view.set_content(self.performance_toast_overlay)

        main_box.append(self._build_auto_switch_section())
        main_box.append(self._build_mode_section())
        main_box.append(self._build_fan_curve_section())

        page = Adw.NavigationPage(title="Performance")
        page.set_child(toolbar_view)
        return page

    def _build_auto_switch_section(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        explainer = Gtk.Label(
            label=(
                "Automatically switch profile when the power source "
                "changes. While this is on, use the choices below instead "
                "of the manual buttons, which stay in sync but aren't "
                "clickable."
            ),
            xalign=0,
            wrap=True,
        )
        explainer.add_css_class("dim-label")
        box.append(explainer)

        switch_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        switch_label = Gtk.Label(label="Switch automatically", xalign=0)
        switch_label.set_hexpand(True)
        switch_row.append(switch_label)
        self.auto_switch_toggle = Gtk.Switch()
        self.auto_switch_toggle.set_valign(Gtk.Align.CENTER)
        self._auto_switch_toggle_handler_id = self.auto_switch_toggle.connect(
            "notify::active", self._on_auto_switch_toggled
        )
        switch_row.append(self.auto_switch_toggle)
        box.append(switch_row)

        grid = Gtk.Grid(row_spacing=12, column_spacing=12)

        grid.attach(Gtk.Label(label="On AC power", xalign=0), 0, 0, 1, 1)
        self.auto_ac_dropdown = Gtk.DropDown.new_from_strings(AUTO_SWITCH_LABELS)
        self.auto_ac_dropdown.set_hexpand(True)
        self._auto_ac_handler_id = self.auto_ac_dropdown.connect(
            "notify::selected", self._on_auto_profiles_changed
        )
        grid.attach(self.auto_ac_dropdown, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="On battery", xalign=0), 0, 1, 1, 1)
        self.auto_battery_dropdown = Gtk.DropDown.new_from_strings(AUTO_SWITCH_LABELS)
        self.auto_battery_dropdown.set_hexpand(True)
        self._auto_battery_handler_id = self.auto_battery_dropdown.connect(
            "notify::selected", self._on_auto_profiles_changed
        )
        grid.attach(self.auto_battery_dropdown, 1, 1, 1, 1)

        # Refresh rate is optional -- only shown if the display backend
        # (GNOME Mutter's D-Bus interface) is actually available and
        # reported at least one selectable rate. Same grid, same
        # toggle-gated section as the profile dropdowns above, since it's
        # the same underlying trigger (an AC/battery transition), not a
        # separate feature.
        self.auto_ac_refresh_dropdown = None
        self.auto_battery_refresh_dropdown = None
        if self._available_refresh_rates:
            refresh_labels = [REFRESH_RATE_DONT_CHANGE_LABEL] + [
                f"{hz} Hz" for hz in self._available_refresh_rates
            ]

            grid.attach(Gtk.Label(label="Refresh rate on AC", xalign=0), 0, 2, 1, 1)
            self.auto_ac_refresh_dropdown = Gtk.DropDown.new_from_strings(
                refresh_labels
            )
            self.auto_ac_refresh_dropdown.set_hexpand(True)
            self._auto_ac_refresh_handler_id = self.auto_ac_refresh_dropdown.connect(
                "notify::selected", self._on_auto_profiles_changed
            )
            grid.attach(self.auto_ac_refresh_dropdown, 1, 2, 1, 1)

            grid.attach(
                Gtk.Label(label="Refresh rate on battery", xalign=0), 0, 3, 1, 1
            )
            self.auto_battery_refresh_dropdown = Gtk.DropDown.new_from_strings(
                refresh_labels
            )
            self.auto_battery_refresh_dropdown.set_hexpand(True)
            self._auto_battery_refresh_handler_id = (
                self.auto_battery_refresh_dropdown.connect(
                    "notify::selected", self._on_auto_profiles_changed
                )
            )
            grid.attach(self.auto_battery_refresh_dropdown, 1, 3, 1, 1)

        box.append(grid)

        self._sync_auto_switch_ui()

        return self._wrap_group("Automatic Switching", box)

    def _refresh_dropdown_index_for_hz(self, hz):
        if hz is None:
            return 0
        try:
            return 1 + self._available_refresh_rates.index(hz)
        except ValueError:
            return 0  # a previously-configured rate that's no longer available

    def _hz_for_refresh_dropdown_index(self, index):
        if index <= 0:
            return None
        return self._available_refresh_rates[index - 1]

    def _sync_auto_switch_ui(self):
        self._auto_config.load()

        self.auto_switch_toggle.handler_block(self._auto_switch_toggle_handler_id)
        self.auto_switch_toggle.set_active(self._auto_config.enabled)
        self.auto_switch_toggle.handler_unblock(self._auto_switch_toggle_handler_id)

        self.auto_ac_dropdown.handler_block(self._auto_ac_handler_id)
        self.auto_ac_dropdown.set_selected(
            AUTO_SWITCH_MODES.index(self._auto_config.ac_profile)
        )
        self.auto_ac_dropdown.handler_unblock(self._auto_ac_handler_id)

        self.auto_battery_dropdown.handler_block(self._auto_battery_handler_id)
        self.auto_battery_dropdown.set_selected(
            AUTO_SWITCH_MODES.index(self._auto_config.battery_profile)
        )
        self.auto_battery_dropdown.handler_unblock(self._auto_battery_handler_id)

        if self.auto_ac_refresh_dropdown is not None:
            self.auto_ac_refresh_dropdown.handler_block(
                self._auto_ac_refresh_handler_id
            )
            self.auto_ac_refresh_dropdown.set_selected(
                self._refresh_dropdown_index_for_hz(self._auto_config.ac_refresh_hz)
            )
            self.auto_ac_refresh_dropdown.handler_unblock(
                self._auto_ac_refresh_handler_id
            )

        if self.auto_battery_refresh_dropdown is not None:
            self.auto_battery_refresh_dropdown.handler_block(
                self._auto_battery_refresh_handler_id
            )
            self.auto_battery_refresh_dropdown.set_selected(
                self._refresh_dropdown_index_for_hz(
                    self._auto_config.battery_refresh_hz
                )
            )
            self.auto_battery_refresh_dropdown.handler_unblock(
                self._auto_battery_refresh_handler_id
            )

    def _on_auto_switch_toggled(self, switch, _pspec):
        self._auto_config.enabled = switch.get_active()
        if self._auto_config.enabled and self._fan_curve_config.enabled:
            # Curve mode and auto-switch both make continuous/automatic
            # claims that would conflict (auto-switch's profile write
            # touches the same EC state a curve is actively driving) --
            # turning one on forces the other off, one-directional.
            self._fan_curve_config.enabled = False
            self._fan_curve_config.save()
            self._performance_toast(
                "Custom fan curve turned off -- it's incompatible with "
                "automatic switching."
            )
        self._auto_config.save()
        if self._auto_config.enabled:
            self._apply_auto_profile_now()
        self._refresh_performance_status()

    def _on_auto_profiles_changed(self, _dropdown, _pspec):
        self._auto_config.ac_profile = AUTO_SWITCH_MODES[
            self.auto_ac_dropdown.get_selected()
        ]
        self._auto_config.battery_profile = AUTO_SWITCH_MODES[
            self.auto_battery_dropdown.get_selected()
        ]
        if self.auto_ac_refresh_dropdown is not None:
            self._auto_config.ac_refresh_hz = self._hz_for_refresh_dropdown_index(
                self.auto_ac_refresh_dropdown.get_selected()
            )
        if self.auto_battery_refresh_dropdown is not None:
            self._auto_config.battery_refresh_hz = (
                self._hz_for_refresh_dropdown_index(
                    self.auto_battery_refresh_dropdown.get_selected()
                )
            )
        self._auto_config.save()
        if self._auto_config.enabled:
            self._apply_auto_profile_now()

    def _apply_auto_profile_now(self):
        on_ac = is_on_ac()
        if on_ac is None:
            return
        if self.performance:
            try:
                self.performance.set_mode(self._auto_config.profile_for(on_ac))
            except (OSError, ValueError) as e:
                self._performance_toast(f"Couldn't set performance mode: {e}")
        hz = self._auto_config.refresh_hz_for(on_ac)
        if hz is not None and self._display_refresh is not None:
            try:
                self._display_refresh.set_rate(hz)
            except DisplayRefreshRateError:
                pass

    def _build_mode_section(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        explainer = Gtk.Label(
            label=(
                "Switch fan and thermal behavior. Max Fan pins both fans "
                "at high speed regardless of temperature, until switched "
                "away from -- use it only when you specifically want "
                "maximum cooling, not as a general-purpose profile."
            ),
            xalign=0,
            wrap=True,
        )
        explainer.add_css_class("dim-label")
        box.append(explainer)

        self.mode_buttons = {}
        self.mode_handler_ids = {}
        group_leader = None
        for mode, label in (
            ("balanced", "Balanced"),
            ("quiet", "Quiet"),
            ("performance", "Performance"),
            ("max-fan", "Max Fan"),
        ):
            btn = Gtk.CheckButton(label=label)
            if group_leader is None:
                group_leader = btn
            else:
                btn.set_group(group_leader)
            handler_id = btn.connect("toggled", self._on_mode_toggled, mode)
            self.mode_buttons[mode] = btn
            self.mode_handler_ids[mode] = handler_id
            box.append(btn)

        refresh_btn = Gtk.Button(label="Refresh from hardware")
        refresh_btn.set_halign(Gtk.Align.START)
        refresh_btn.connect(
            "clicked", lambda b: self._refresh_performance_status()
        )
        box.append(refresh_btn)

        return self._wrap_group("Performance Mode", box)

    def _on_mode_toggled(self, button, mode):
        if not button.get_active() or not self.performance:
            return
        try:
            self.performance.set_mode(mode)
        except (OSError, ValueError) as e:
            self._performance_toast(f"Couldn't set performance mode: {e}")

    # ---- Custom fan curve ----

    def _build_fan_curve_section(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        explainer = Gtk.Label(
            label=(
                "Drive the fans from a custom temperature/speed curve "
                "instead of the fixed profiles above. While this is on, "
                "the manual mode buttons above become informational only, "
                "and automatic switching is turned off -- the two aren't "
                "compatible."
            ),
            xalign=0,
            wrap=True,
        )
        explainer.add_css_class("dim-label")
        box.append(explainer)

        switch_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        switch_label = Gtk.Label(label="Use custom fan curve", xalign=0)
        switch_label.set_hexpand(True)
        switch_row.append(switch_label)
        self.fan_curve_toggle = Gtk.Switch()
        self.fan_curve_toggle.set_valign(Gtk.Align.CENTER)
        self._fan_curve_toggle_handler_id = self.fan_curve_toggle.connect(
            "notify::active", self._on_fan_curve_toggled
        )
        switch_row.append(self.fan_curve_toggle)
        box.append(switch_row)

        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.fan_daemon_status_icon = Gtk.Image.new_from_icon_name(
            "dialog-information-symbolic"
        )
        status_row.append(self.fan_daemon_status_icon)
        self.fan_daemon_status_label = Gtk.Label(xalign=0, wrap=True)
        self.fan_daemon_status_label.add_css_class("dim-label")
        status_row.append(self.fan_daemon_status_label)
        box.append(status_row)

        # Always reachable, independent of the toggle state -- an
        # explicit escape hatch that has to work even when the daemon
        # (which the toggle otherwise depends on) is exactly what's broken.
        self.fan_release_button = Gtk.Button(label="Release Fan Control to Firmware")
        self.fan_release_button.set_halign(Gtk.Align.START)
        self.fan_release_button.connect("clicked", self._on_fan_release_clicked)
        box.append(self.fan_release_button)

        self.fan_curve_points_box = Gtk.ListBox()
        self.fan_curve_points_box.add_css_class("boxed-list")
        self.fan_curve_points_box.set_selection_mode(Gtk.SelectionMode.NONE)

        points_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_point_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_point_btn.set_tooltip_text("Add point")
        add_point_btn.connect("clicked", self._on_add_curve_point)
        points_actions.append(add_point_btn)
        points_actions.append(Gtk.Box(hexpand=True))
        apply_curve_btn = Gtk.Button(label="Apply Curve")
        apply_curve_btn.add_css_class("suggested-action")
        apply_curve_btn.connect("clicked", self._on_apply_curve_clicked)
        points_actions.append(apply_curve_btn)

        points_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        points_box.append(self.fan_curve_points_box)
        points_box.append(points_actions)
        box.append(self._wrap_group("Curve Points", points_box))
        self._rebuild_curve_points(self._fan_curve_config.curve)

        safety_grid = Gtk.Grid(row_spacing=12, column_spacing=12)
        safety_grid.attach(
            Gtk.Label(label="Critical temperature", xalign=0), 0, 0, 1, 1
        )
        self.fan_critical_temp_spin = Gtk.SpinButton.new_with_range(60, 105, 1)
        self.fan_critical_temp_spin.set_hexpand(True)
        safety_grid.attach(self.fan_critical_temp_spin, 1, 0, 1, 1)
        safety_grid.attach(Gtk.Label(label="°C"), 2, 0, 1, 1)

        safety_grid.attach(Gtk.Label(label="Hysteresis", xalign=0), 0, 1, 1, 1)
        self.fan_hysteresis_spin = Gtk.SpinButton.new_with_range(0, 20, 1)
        self.fan_hysteresis_spin.set_hexpand(True)
        safety_grid.attach(self.fan_hysteresis_spin, 1, 1, 1, 1)
        safety_grid.attach(Gtk.Label(label="°C"), 2, 1, 1, 1)

        safety_grid.attach(Gtk.Label(label="Maximum duty", xalign=0), 0, 2, 1, 1)
        self.fan_max_duty_spin = Gtk.SpinButton.new_with_range(1, 100, 1)
        self.fan_max_duty_spin.set_hexpand(True)
        safety_grid.attach(self.fan_max_duty_spin, 1, 2, 1, 1)
        safety_grid.attach(Gtk.Label(label="%"), 2, 2, 1, 1)

        box.append(self._wrap_group("Safety & Preferences", safety_grid))

        self._sync_fan_curve_ui()

        return self._wrap_group("Custom Fan Curve", box)

    def _curve_point_rows(self):
        rows = []
        child = self.fan_curve_points_box.get_first_child()
        while child is not None:
            rows.append(child)
            child = child.get_next_sibling()
        return rows

    def _build_curve_point_row(self, temp_c, percent):
        row = Gtk.ListBoxRow()
        row.set_selectable(False)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hbox.set_margin_top(6)
        hbox.set_margin_bottom(6)
        hbox.set_margin_start(12)
        hbox.set_margin_end(12)

        temp_spin = Gtk.SpinButton.new_with_range(0, 105, 1)
        temp_spin.set_value(temp_c)
        hbox.append(temp_spin)
        hbox.append(Gtk.Label(label="°C →"))

        duty_spin = Gtk.SpinButton.new_with_range(0, 100, 1)
        duty_spin.set_value(percent)
        hbox.append(duty_spin)
        hbox.append(Gtk.Label(label="%"))

        hbox.append(Gtk.Box(hexpand=True))

        remove_btn = Gtk.Button(icon_name="window-close-symbolic")
        remove_btn.add_css_class("circular")
        remove_btn.set_tooltip_text("Remove point")
        remove_btn.connect(
            "clicked", lambda b, r=row: self.fan_curve_points_box.remove(r)
        )
        hbox.append(remove_btn)

        row.set_child(hbox)
        # Plain Python attributes on a Gtk widget -- the same pattern
        # already used for sidebar rows (row.page_name), safe since
        # PyGObject instances carry a normal __dict__.
        row._temp_spin = temp_spin
        row._duty_spin = duty_spin
        return row

    def _rebuild_curve_points(self, points):
        for row in self._curve_point_rows():
            self.fan_curve_points_box.remove(row)
        for point in points:
            self.fan_curve_points_box.append(
                self._build_curve_point_row(point["temp_c"], point["percent"])
            )

    def _on_add_curve_point(self, _button):
        rows = self._curve_point_rows()
        if rows:
            new_temp = min(105, rows[-1]._temp_spin.get_value() + 5)
            new_duty = min(100, rows[-1]._duty_spin.get_value() + 10)
        else:
            new_temp, new_duty = 60, 50
        self.fan_curve_points_box.append(
            self._build_curve_point_row(new_temp, new_duty)
        )

    def _on_apply_curve_clicked(self, _button):
        raw_points = [
            {
                "temp_c": int(row._temp_spin.get_value()),
                "percent": int(row._duty_spin.get_value()),
            }
            for row in self._curve_point_rows()
        ]
        try:
            points = validate_curve(raw_points)
        except ValueError as e:
            self._performance_toast(str(e))
            return

        self._fan_curve_config.curve = points
        self._fan_curve_config.critical_temp_c = int(
            self.fan_critical_temp_spin.get_value()
        )
        self._fan_curve_config.hysteresis_c = int(self.fan_hysteresis_spin.get_value())
        self._fan_curve_config.max_duty_percent = int(
            self.fan_max_duty_spin.get_value()
        )
        self._fan_curve_config.save()
        self._rebuild_curve_points(self._fan_curve_config.curve)
        self._performance_toast("Curve applied.")

    def _sync_fan_curve_ui(self):
        self._fan_curve_config.load()

        self.fan_curve_toggle.handler_block(self._fan_curve_toggle_handler_id)
        self.fan_curve_toggle.set_active(self._fan_curve_config.enabled)
        self.fan_curve_toggle.handler_unblock(self._fan_curve_toggle_handler_id)

        if not self.fan_critical_temp_spin.has_focus():
            self.fan_critical_temp_spin.set_value(self._fan_curve_config.critical_temp_c)
        if not self.fan_hysteresis_spin.has_focus():
            self.fan_hysteresis_spin.set_value(self._fan_curve_config.hysteresis_c)
        if not self.fan_max_duty_spin.has_focus():
            self.fan_max_duty_spin.set_value(self._fan_curve_config.max_duty_percent)

    def _on_fan_curve_toggled(self, switch, _pspec):
        self._fan_curve_config.enabled = switch.get_active()

        if self._fan_curve_config.enabled and self._auto_config.enabled:
            self._auto_config.enabled = False
            self._auto_config.save()
            self._sync_auto_switch_ui()
            self._performance_toast(
                "Automatic switching turned off -- it's incompatible with "
                "a custom fan curve."
            )

        self._fan_curve_config.save()

        if self._fan_curve_config.enabled:
            self._performance_toast("Fan curve enabled.")
        else:
            if self.fan_control:
                try:
                    self.fan_control.release()
                except OSError:
                    pass
            self._performance_toast(
                "Fan curve disabled -- fan released to firmware control."
            )

        self._refresh_performance_status()

    def _on_fan_release_clicked(self, _button):
        if not self.fan_control:
            return
        try:
            self.fan_control.release()
        except OSError as e:
            self._performance_toast(f"Couldn't release fan control: {e}")
            return
        self._fan_curve_config.enabled = False
        self._fan_curve_config.save()
        self._performance_toast("Released fan control to firmware.")
        self._refresh_performance_status()

    @staticmethod
    def _systemctl_is_active(service_name, user=False):
        try:
            args = ["systemctl", "--user", "is-active", service_name] if user \
                else ["systemctl", "is-active", service_name]
            proc = Gio.Subprocess.new(
                args,
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
            ok, stdout, _stderr = proc.communicate_utf8(None)
            return bool(ok) and stdout.strip() == "active"
        except GLib.Error:
            return False

    @staticmethod
    def _read_fan_daemon_status_file():
        try:
            return json.loads(
                Path("/var/lib/clevo-control-panel/fan-daemon-status.json").read_text()
            )
        except (OSError, ValueError):
            return None

    def _refresh_fan_daemon_status(self):
        self._sync_fan_curve_ui()

        if not self.fan_control:
            self._last_fan_health = "unavailable"
            self.fan_curve_toggle.set_sensitive(False)
            self.fan_release_button.set_sensitive(False)
            self.fan_daemon_status_icon.set_from_icon_name("dialog-error-symbolic")
            self.fan_daemon_status_label.set_label(
                self._fan_control_error or "No compatible fan control found."
            )
            return

        self.fan_curve_toggle.set_sensitive(True)
        self.fan_release_button.set_sensitive(self.fan_control.is_writable())

        if not self._fan_curve_config.enabled:
            self._last_fan_health = "not_needed"
            self.fan_daemon_status_icon.set_from_icon_name(
                "dialog-information-symbolic"
            )
            self.fan_daemon_status_label.set_label(
                "Daemon: not needed (curve mode is off)"
            )
            return

        if not self._systemctl_is_active("clevo-fan-curve.service", user=True):
            self._last_fan_health = "not_running"
            self.fan_daemon_status_icon.set_from_icon_name("dialog-error-symbolic")
            self.fan_daemon_status_label.set_label(
                "Daemon: not running -- fan speed could be stuck"
            )
            return

        status = self._read_fan_daemon_status_file()
        stale = status is None or (
            time.time() - status.get("timestamp", 0) > FAN_DAEMON_STALE_SECONDS
        )
        if stale:
            self._last_fan_health = "stale"
            self.fan_daemon_status_icon.set_from_icon_name("dialog-warning-symbolic")
            self.fan_daemon_status_label.set_label(
                "Daemon: not responding -- fan speed could be stuck"
            )
            return

        if status.get("critical_override_active"):
            self._last_fan_health = "critical"
            self.fan_daemon_status_icon.set_from_icon_name("dialog-warning-symbolic")
            self.fan_daemon_status_label.set_label(
                "Daemon: running -- critical temp override active (fans "
                "forced to maximum)"
            )
            return

        self._last_fan_health = "healthy"
        self.fan_daemon_status_icon.set_from_icon_name("emblem-ok-symbolic")
        self.fan_daemon_status_label.set_label("Daemon: running, curve active")

    def _refresh_performance_status(self):
        self._sync_auto_switch_ui()
        self._refresh_fan_daemon_status()

        if not self.performance:
            self.performance_banner.set_title(
                self._performance_error
                or "No compatible performance mode control found."
            )
            self.performance_banner.set_revealed(True)
            for btn in self.mode_buttons.values():
                btn.set_sensitive(False)
            return

        try:
            mode = self.performance.get_mode()
        except OSError as e:
            self._performance_toast(f"Couldn't read performance mode: {e}")
            return

        btn = self.mode_buttons.get(mode)
        if btn and not btn.get_active():
            handler_id = self.mode_handler_ids[mode]
            btn.handler_block(handler_id)
            btn.set_active(True)
            btn.handler_unblock(handler_id)

        # The three auto-switch-eligible buttons stay informational
        # (showing the current mode) but aren't clickable while
        # auto-switch owns the decision -- otherwise a click here would
        # just get overridden on the next power-source check, which would
        # be confusing. Max Fan is exempt from *that* one: it's an
        # independent cooling-boost toggle, not one of the two profiles
        # auto-switch picks between. Custom fan curve is different --
        # it and Max Fan both make continuous, competing claims on the
        # literal same fan-duty actuator, so curve mode disables all four
        # buttons without exception, Max Fan included.
        writable = self.performance.is_writable()
        fan_curve_governed = self._fan_curve_config.enabled
        for mode, b in self.mode_buttons.items():
            auto_governed = mode in AUTO_SWITCH_MODES and self._auto_config.enabled
            b.set_sensitive(writable and not auto_governed and not fan_curve_governed)

        fan_unhealthy = fan_curve_governed and self._last_fan_health in (
            "not_running",
            "stale",
        )
        if not writable:
            self.performance_banner.set_button_label("")
            self.performance_banner.set_title(
                "No write access to performance mode control yet. Run "
                "system setup below."
            )
            self.performance_banner.set_revealed(True)
        elif fan_unhealthy:
            self.performance_banner.set_button_label("Release to Auto")
            self.performance_banner.set_title(
                "Custom fan curve is on, but the daemon isn't responding "
                "-- fan speed could be stuck."
            )
            self.performance_banner.set_revealed(True)
        elif self._auto_config.enabled:
            self.performance_banner.set_button_label("")
            self.performance_banner.set_title(
                "Automatic switching is on -- turn it off above to set the "
                "mode manually."
            )
            self.performance_banner.set_revealed(True)
        else:
            self.performance_banner.set_revealed(False)

    def _performance_toast(self, message):
        self.performance_toast_overlay.add_toast(
            Adw.Toast(title=message, timeout=4)
        )

    def _periodic_performance_refresh(self):
        self._refresh_performance_status()
        return GLib.SOURCE_CONTINUE

    # ---- Dashboard ----
    #
    # Deliberately styled differently from the rest of the app -- a
    # retrofuturistic instrument panel (phosphor-CRT palette, analog
    # gauges, glowing console-style readouts, oscilloscope-style history
    # traces) instead of plain labels. Every custom widget below paints
    # its own complete background in Cairo rather than relying on GTK CSS
    # cascading, so the look is self-contained regardless of surrounding
    # theme/CSS behavior. Structurally it still follows this app's usual
    # `_wrap_group`-sectioned layout -- only the rendering of individual
    # values is custom, not the page's overall structure.

    def _build_dashboard_page(self):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Dashboard"))

        settings_btn = Gtk.Button(icon_name="preferences-system-symbolic")
        settings_btn.set_tooltip_text("System Integration Settings")
        settings_btn.connect("clicked", self._on_open_settings)
        header.pack_end(settings_btn)
        toolbar_view.add_top_bar(header)

        self.dashboard_banner = Adw.Banner(title="")
        toolbar_view.add_top_bar(self.dashboard_banner)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        clamp = Adw.Clamp(maximum_size=680)
        clamp.set_margin_top(12)
        clamp.set_margin_bottom(24)
        clamp.set_margin_start(12)
        clamp.set_margin_end(12)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        clamp.set_child(main_box)
        scroller.set_child(clamp)
        toolbar_view.set_content(scroller)

        main_box.append(self._build_dashboard_gauges_section())
        main_box.append(self._build_dashboard_readouts_section())
        main_box.append(self._build_dashboard_history_section())

        page = Adw.NavigationPage(title="Dashboard")
        page.set_child(toolbar_view)
        return page

    def _build_gauge(self, title, min_value, max_value, tooltip=None):
        area = Gtk.DrawingArea()
        area.set_content_width(140)
        area.set_content_height(140)
        if tooltip:
            area.set_tooltip_text(tooltip)
        state = {"value": min_value}

        def draw(_area, cr, w, h):
            self._paint_gauge(cr, w, h, state["value"], min_value, max_value)

        area.set_draw_func(draw)

        label = Gtk.Label(
            label=title, xalign=0.5, wrap=True, justify=Gtk.Justification.CENTER
        )
        label.add_css_class("dim-label")

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER
        )
        box.append(area)
        box.append(label)

        def set_value(value):
            if value is None:
                return
            state["value"] = value
            area.queue_draw()

        return box, set_value

    @staticmethod
    def _paint_gauge(cr, width, height, value, min_value, max_value):
        cx, cy = width / 2, height / 2 - 4
        radius = min(width, height) / 2 - 16

        cr.set_source_rgb(*DASHBOARD_BG)
        cr.paint()

        sweep = math.radians(270)
        start_angle = math.radians(135)

        frac = 0.0
        if max_value > min_value:
            frac = (value - min_value) / (max_value - min_value)
        frac = max(0.0, min(1.0, frac))

        if frac < 0.6:
            color = DASHBOARD_GREEN
        elif frac < 0.85:
            color = DASHBOARD_AMBER
        else:
            color = DASHBOARD_RED

        cr.set_line_width(6)
        cr.set_source_rgba(*DASHBOARD_GREEN, 0.25)
        cr.arc(cx, cy, radius, start_angle, start_angle + sweep)
        cr.stroke()

        if frac > 0:
            cr.set_line_width(11)
            cr.set_source_rgba(*color, 0.35)
            cr.arc(cx, cy, radius, start_angle, start_angle + sweep * frac)
            cr.stroke()

            cr.set_line_width(5)
            cr.set_source_rgba(*color, 1.0)
            cr.arc(cx, cy, radius, start_angle, start_angle + sweep * frac)
            cr.stroke()

            needle_angle = start_angle + sweep * frac
            nx = cx + math.cos(needle_angle) * (radius - 10)
            ny = cy + math.sin(needle_angle) * (radius - 10)
            cr.set_line_width(2)
            cr.set_source_rgba(*color, 0.9)
            cr.move_to(cx, cy)
            cr.line_to(nx, ny)
            cr.stroke()

        cr.select_font_face(
            "monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD
        )
        cr.set_font_size(20)
        text = f"{value:.0f}%"
        extents = cr.text_extents(text)
        cr.set_source_rgb(*color)
        cr.move_to(cx - extents.width / 2 - extents.x_bearing, cy + extents.height / 2)
        cr.show_text(text)

    def _build_dashboard_gauges_section(self):
        grid = Gtk.Grid(row_spacing=12, column_spacing=12, column_homogeneous=True)

        self._dashboard_gauges = {}
        specs = [
            ("cpu_util", "CPU Utilization", None),
            ("gpu_util", "GPU Utilization (NVIDIA)", None),
            (
                "igpu_util",
                "iGPU Utilization (Xe)",
                "Approximate -- summed across processes using the GPU via "
                "the kernel's DRM client-stats interface, not a single "
                "hardware counter.",
            ),
        ]
        for i, (key, title, tooltip) in enumerate(specs):
            box, set_value = self._build_gauge(title, 0, 100, tooltip=tooltip)
            grid.attach(box, i, 0, 1, 1)
            self._dashboard_gauges[key] = set_value

        return self._wrap_group("Utilization", grid)

    def _build_readout(self, title, unit):
        area = Gtk.DrawingArea()
        area.set_content_width(150)
        area.set_content_height(56)
        state = {"text": "--"}

        def draw(_area, cr, w, h):
            self._paint_readout(cr, w, h, state["text"], title, unit)

        area.set_draw_func(draw)

        def set_value(value):
            state["text"] = "--" if value is None else f"{value:.0f}"
            area.queue_draw()

        return area, set_value

    @staticmethod
    def _paint_readout(cr, width, height, text, title, unit):
        cr.set_source_rgb(*DASHBOARD_BG)
        cr.paint()

        color = DASHBOARD_GREEN
        display_text = f"{text} {unit}"
        tx, ty = 8, height * 0.66

        cr.select_font_face(
            "monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD
        )
        cr.set_font_size(22)

        # Cheap "CRT glow": the same text drawn a few times at low alpha,
        # slightly offset, behind a crisp final pass -- avoids needing a
        # real Gaussian blur/shader for a similar visual effect.
        cr.set_source_rgba(*color, 0.3)
        for dx, dy in ((-1, -1), (1, 1), (-1, 1), (1, -1)):
            cr.move_to(tx + dx, ty + dy)
            cr.show_text(display_text)

        cr.set_source_rgb(*color)
        cr.move_to(tx, ty)
        cr.show_text(display_text)

        cr.set_font_size(11)
        cr.set_source_rgba(*color, 0.65)
        cr.move_to(tx, 14)
        cr.show_text(title.upper())

    def _build_dashboard_readouts_section(self):
        grid = Gtk.Grid(row_spacing=10, column_spacing=10, column_homogeneous=True)

        self._dashboard_readouts = {}
        specs = [
            ("cpu_temp", "CPU Temp", "°C"),
            ("gpu_temp", "GPU Temp", "°C"),
            ("cpu_freq", "CPU Freq", "MHz"),
            ("fan1_rpm", "Fan 1", "RPM"),
            ("fan2_rpm", "Fan 2", "RPM"),
            ("fan1_duty", "Fan 1 Duty", "%"),
            ("fan2_duty", "Fan 2 Duty", "%"),
        ]
        for i, (key, title, unit) in enumerate(specs):
            area, set_value = self._build_readout(title, unit)
            grid.attach(area, i % 3, i // 3, 1, 1)
            self._dashboard_readouts[key] = set_value

        return self._wrap_group("Readouts", grid)

    @staticmethod
    def _paint_trace(cr, width, height, series, value_range):
        cr.set_source_rgb(*DASHBOARD_BG)
        cr.paint()

        cr.set_source_rgba(*DASHBOARD_GRID, 0.4)
        cr.set_line_width(1)
        for frac in (0.25, 0.5, 0.75):
            y = height * frac
            cr.move_to(0, y)
            cr.line_to(width, y)
            cr.stroke()

        lo, hi = value_range
        span = max(1e-6, hi - lo)

        for history, color in series:
            points = list(history)
            n = len(points)
            if n < 2:
                continue
            for line_width, alpha in ((5, 0.3), (2, 1.0)):
                cr.set_line_width(line_width)
                cr.set_source_rgba(*color, alpha)
                started = False
                for i, value in enumerate(points):
                    if value is None:
                        started = False
                        continue
                    x = width * i / (n - 1)
                    frac = max(0.0, min(1.0, (value - lo) / span))
                    y = height - frac * height
                    if not started:
                        cr.move_to(x, y)
                        started = True
                    else:
                        cr.line_to(x, y)
                cr.stroke()

    def _draw_temp_trace(self, _area, cr, w, h):
        series = [
            (self._dashboard_history["cpu_temp"], DASHBOARD_GREEN),
            (self._dashboard_history["gpu_temp"], DASHBOARD_AMBER),
        ]
        self._paint_trace(cr, w, h, series, value_range=(20, 100))

    def _draw_fan_trace(self, _area, cr, w, h):
        series = [(self._dashboard_history["fan_duty"], DASHBOARD_GREEN)]
        self._paint_trace(cr, w, h, series, value_range=(0, 100))

    def _build_dashboard_history_section(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)

        self._temp_trace_area = Gtk.DrawingArea()
        self._temp_trace_area.set_content_height(120)
        self._temp_trace_area.set_hexpand(True)
        self._temp_trace_area.set_draw_func(self._draw_temp_trace)
        box.append(
            self._wrap_group(
                "Temperature History (10 min, green=CPU, amber=GPU)",
                self._temp_trace_area,
            )
        )

        self._fan_trace_area = Gtk.DrawingArea()
        self._fan_trace_area.set_content_height(120)
        self._fan_trace_area.set_hexpand(True)
        self._fan_trace_area.set_draw_func(self._draw_fan_trace)
        box.append(
            self._wrap_group("Fan Duty History (10 min)", self._fan_trace_area)
        )

        return box

    def _refresh_dashboard(self):
        cpu_temp = sensors.read_cpu_temp_c()
        cpu_freq = sensors.read_cpu_frequency_mhz()
        cpu_util = sensors.read_cpu_utilization_percent()
        gpu_temp, gpu_util = sensors.read_nvidia_metrics()
        igpu_util = sensors.read_xe_igpu_utilization_percent()
        fan_rpms = sensors.read_fan_rpms()

        fan1_duty = fan2_duty = None
        if self.fan_control:
            try:
                fans = self.fan_control.fans()
                if 1 in fans:
                    fan1_duty = self.fan_control.get_duty(1)
                if 2 in fans:
                    fan2_duty = self.fan_control.get_duty(2)
            except OSError:
                pass

        self._dashboard_gauges["cpu_util"](cpu_util)
        self._dashboard_gauges["gpu_util"](gpu_util)
        self._dashboard_gauges["igpu_util"](igpu_util)

        self._dashboard_readouts["cpu_temp"](cpu_temp)
        self._dashboard_readouts["gpu_temp"](gpu_temp)
        self._dashboard_readouts["cpu_freq"](cpu_freq)
        self._dashboard_readouts["fan1_rpm"](
            fan_rpms[0] if len(fan_rpms) > 0 else None
        )
        self._dashboard_readouts["fan2_rpm"](
            fan_rpms[1] if len(fan_rpms) > 1 else None
        )
        self._dashboard_readouts["fan1_duty"](fan1_duty)
        self._dashboard_readouts["fan2_duty"](fan2_duty)

        self._dashboard_history["cpu_temp"].append(cpu_temp)
        self._dashboard_history["gpu_temp"].append(gpu_temp)
        duties = [d for d in (fan1_duty, fan2_duty) if d is not None]
        self._dashboard_history["fan_duty"].append(
            sum(duties) / len(duties) if duties else None
        )

        self._temp_trace_area.queue_draw()
        self._fan_trace_area.queue_draw()

        if gpu_temp is None:
            self.dashboard_banner.set_title(
                'No NVIDIA GPU detected (or nvidia-smi unavailable) -- GPU '
                'rows will show "--".'
            )
            self.dashboard_banner.set_revealed(True)
        else:
            self.dashboard_banner.set_revealed(False)

    # ---- Settings dialog ----

    def _build_system_section(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.system_status_label = Gtk.Label(xalign=0, wrap=True)
        self.system_status_label.add_css_class("dim-label")
        box.append(self.system_status_label)

        self.setup_btn = Gtk.Button(label="Repair Setup")
        self.setup_btn.set_halign(Gtk.Align.START)
        self.setup_btn.set_tooltip_text(
            "Grants this account hardware access and enables boot persistence. "
            "Only needed if something's not set up correctly, or for another "
            "user account on this machine that hasn't run it yet."
        )
        self.setup_btn.connect("clicked", self._on_run_setup)
        box.append(self.setup_btn)
        return self._wrap_group("System Integration", box)

    def _build_settings_window(self):
        dialog = Adw.Dialog()
        dialog.set_title("Settings")
        dialog.set_content_width(420)
        dialog.set_content_height(220)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        clamp = Adw.Clamp(maximum_size=420)
        clamp.set_margin_top(18)
        clamp.set_margin_bottom(18)
        clamp.set_margin_start(18)
        clamp.set_margin_end(18)
        clamp.set_child(self._build_system_section())

        toolbar_view.set_content(clamp)
        dialog.set_child(toolbar_view)
        return dialog

    def _on_open_settings(self, _button):
        self.settings_window.present(self)

    # ---- Behavior ----

    def apply_color(self, hex_color):
        if not self.backend:
            return
        try:
            self.backend.set_color(hex_color)
        except (OSError, ValueError) as e:
            self._toast(f"Couldn't set color: {e}")
            return
        self._refresh_status()

    def _on_custom_color_chosen(self, button, _pspec):
        self.apply_color(self._rgba_to_hex(button.get_rgba()))

    def _sync_color_button(self, hex_color):
        rgba = Gdk.RGBA()
        rgba.parse(f"#{hex_color}")
        self.color_button.handler_block(self._color_notify_handler_id)
        self.color_button.set_rgba(rgba)
        self.color_button.handler_unblock(self._color_notify_handler_id)

    def _on_add_favorite(self, _button):
        hex_color = self._rgba_to_hex(self.color_button.get_rgba())
        config.add_favorite(hex_color)
        self._rebuild_favorites()

    @staticmethod
    def _rgba_to_hex(rgba):
        return "{:02X}{:02X}{:02X}".format(
            round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255)
        )

    def _rebuild_favorites(self):
        child = self.favorites_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.favorites_box.remove(child)
            child = nxt

        favorites = config.load_favorites()
        self.favorites_section.set_visible(bool(favorites))
        for fav in favorites:
            overlay = Gtk.Overlay()
            overlay.set_child(self._make_swatch(fav["hex"], fav["name"]))
            remove_btn = Gtk.Button(icon_name="window-close-symbolic")
            remove_btn.add_css_class("circular")
            remove_btn.add_css_class("osd")
            remove_btn.set_valign(Gtk.Align.START)
            remove_btn.set_halign(Gtk.Align.END)
            remove_btn.set_tooltip_text("Remove from favorites")
            remove_btn.connect(
                "clicked", lambda b, h=fav["hex"]: self._on_remove_favorite(h)
            )
            overlay.add_overlay(remove_btn)
            self.favorites_box.append(overlay)

    def _on_remove_favorite(self, hex_color):
        config.remove_favorite(hex_color)
        self._rebuild_favorites()

    def _on_brightness_changed(self, scale):
        if self._brightness_debounce_id is not None:
            GLib.source_remove(self._brightness_debounce_id)
        value = scale.get_value()
        self._brightness_debounce_id = GLib.timeout_add(
            80, self._apply_brightness, value
        )

    def _apply_brightness(self, value):
        self._brightness_debounce_id = None
        if self.backend:
            try:
                self.backend.set_brightness(int(value))
            except OSError as e:
                self._toast(f"Couldn't set brightness: {e}")
        return GLib.SOURCE_REMOVE

    def _refresh_status(self):
        """Cheap refresh: current color swatch + brightness slider position."""
        if not self.backend:
            self.current_hex_label.set_label("No backlight device found")
            return

        try:
            hex_color = self.backend.get_color()
        except OSError:
            return
        self.current_hex_label.set_label(f"#{hex_color}")
        self._current_hex = hex_color
        self.current_area.queue_draw()
        self._sync_color_button(hex_color)

        if not self.brightness_scale.has_focus():
            try:
                self.brightness_scale.set_value(self.backend.get_brightness())
            except OSError:
                pass

    def _refresh_system_status(self):
        """Slower refresh: writability + systemd persistence status."""
        if not self.backend:
            self.banner.set_title(
                self._backend_error or "No compatible keyboard backlight found."
            )
            self.banner.set_revealed(True)
            self.system_status_label.set_label("No compatible hardware detected.")
            return

        writable = self.backend.is_writable()
        if writable:
            self.banner.set_revealed(False)
        else:
            self.banner.set_title(
                "No write access to the keyboard backlight yet. Run system setup below."
            )
            self.banner.set_revealed(True)

        keyboard_persistence_ok = self._persistence_enabled(
            "restore-keyboard-color.service"
        )
        performance_persistence_ok = self._persistence_enabled(
            "restore-performance-mode.service"
        )
        battery_status = (
            "yes" if self.battery and self.battery.is_writable() else "no"
        )
        performance_status = (
            "yes" if self.performance and self.performance.is_writable() else "no"
        )
        self.system_status_label.set_label(
            "Keyboard hardware access: {}\n"
            "Battery threshold access: {}\n"
            "Performance mode access: {}\n"
            "Keyboard boot persistence: {}\n"
            "Performance mode boot persistence: {}".format(
                "yes" if writable else "no",
                battery_status,
                performance_status,
                "enabled" if keyboard_persistence_ok else "not enabled",
                "enabled" if performance_persistence_ok else "not enabled",
            )
        )

    @staticmethod
    def _persistence_enabled(service_name):
        try:
            proc = Gio.Subprocess.new(
                ["systemctl", "is-enabled", service_name],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
            ok, stdout, _stderr = proc.communicate_utf8(None)
            return bool(ok) and "enabled" in stdout
        except GLib.Error:
            return False

    def _on_run_setup(self, _button):
        self.system_status_label.set_label(
            "Running setup — check for an authentication prompt…"
        )

        def done(success, message):
            def _update():
                self._toast(message)
                self._refresh_system_status()
                self._refresh_battery_status()
                self._refresh_performance_status()

            GLib.idle_add(_update)

        setup_helper.run_setup(done)

    def _toast(self, message):
        self.toast_overlay.add_toast(Adw.Toast(title=message, timeout=4))
