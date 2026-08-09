"""Main application window."""

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_foreign("cairo")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import colors as colors_mod
from . import config
from . import setup_helper
from .backend import BacklightError, KeyboardBacklight
from .battery import ChargeThresholdError, ChargeThresholds
from .performance import PerformanceMode, PerformanceModeError


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

        self._brightness_debounce_id = None
        self._threshold_debounce_id = None
        self._current_hex = "000000"

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

    # ---- Top-level UI construction ----

    def _build_ui(self):
        # Built before the sidebar, since selecting the initial sidebar row
        # fires _on_sidebar_row_selected() immediately, which needs these.
        self.keyboard_page = self._build_keyboard_page()
        self.battery_page = self._build_battery_page()
        self.performance_page = self._build_performance_page()

        self.split_view = Adw.NavigationSplitView()
        self.split_view.set_min_sidebar_width(180)
        self.split_view.set_max_sidebar_width(220)
        self.split_view.set_sidebar(self._build_sidebar())
        self.split_view.set_content(self.keyboard_page)

        self.set_content(self.split_view)

        self.settings_window = self._build_settings_window()

        self._rebuild_favorites()

    def _build_sidebar(self):
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        listbox = Gtk.ListBox()
        listbox.add_css_class("navigation-sidebar")
        listbox.append(self._make_sidebar_row("input-keyboard-symbolic", "Keyboard"))
        listbox.append(self._make_sidebar_row("battery-good-symbolic", "Battery"))
        listbox.append(
            self._make_sidebar_row(
                "power-profile-performance-symbolic", "Performance"
            )
        )
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
        }
        self.split_view.set_content(pages[row.page_name])

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

        refresh_btn = Gtk.Button(label="Refresh from hardware")
        refresh_btn.set_halign(Gtk.Align.START)
        refresh_btn.connect("clicked", lambda b: self._refresh_battery_status())
        box.append(refresh_btn)

        return self._wrap_group("Charge Thresholds", box)

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

        main_box.append(self._build_mode_section())

        page = Adw.NavigationPage(title="Performance")
        page.set_child(toolbar_view)
        return page

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

    def _refresh_performance_status(self):
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

        writable = self.performance.is_writable()
        for b in self.mode_buttons.values():
            b.set_sensitive(writable)
        if writable:
            self.performance_banner.set_revealed(False)
        else:
            self.performance_banner.set_title(
                "No write access to performance mode control yet. Run "
                "system setup below."
            )
            self.performance_banner.set_revealed(True)

    def _performance_toast(self, message):
        self.performance_toast_overlay.add_toast(
            Adw.Toast(title=message, timeout=4)
        )

    def _periodic_performance_refresh(self):
        self._refresh_performance_status()
        return GLib.SOURCE_CONTINUE

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
