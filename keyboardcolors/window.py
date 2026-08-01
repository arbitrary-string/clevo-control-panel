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


class KeyboardColorsWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Keyboard Colors")
        self.set_default_size(460, 720)

        try:
            self.backend = KeyboardBacklight()
            self._backend_error = None
        except BacklightError as e:
            self.backend = None
            self._backend_error = str(e)

        self._brightness_debounce_id = None
        self._current_hex = "000000"

        self._build_ui()
        self._refresh_status()
        self._refresh_system_status()

    # ---- UI construction ----

    def _build_ui(self):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        settings_btn = Gtk.Button(icon_name="preferences-system-symbolic")
        settings_btn.set_tooltip_text("System Integration Settings")
        settings_btn.connect("clicked", self._on_open_settings)
        header.pack_end(settings_btn)

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
        self.set_content(toolbar_view)

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

        self.settings_window = self._build_settings_window()

        self._rebuild_favorites()

    def _wrap_group(self, title, child_widget):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        label = Gtk.Label(label=title, xalign=0)
        label.add_css_class("heading")
        box.append(label)
        box.append(child_widget)
        return box

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

        persistence_ok = self._persistence_enabled()
        self.system_status_label.set_label(
            "Direct hardware access: {}\nBoot persistence service: {}".format(
                "yes" if writable else "no",
                "enabled" if persistence_ok else "not enabled",
            )
        )

    @staticmethod
    def _persistence_enabled():
        try:
            proc = Gio.Subprocess.new(
                ["systemctl", "is-enabled", "restore-keyboard-color.service"],
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

            GLib.idle_add(_update)

        setup_helper.run_setup(done)

    def _toast(self, message):
        self.toast_overlay.add_toast(Adw.Toast(title=message, timeout=4))
