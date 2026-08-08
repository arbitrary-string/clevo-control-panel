import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw

from .window import ClevoControlPanelWindow

APP_ID = "com.mupdike.ClevoControlPanel"


class ClevoControlPanelApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        window = self.props.active_window
        if not window:
            window = ClevoControlPanelWindow(application=self)
        window.present()
