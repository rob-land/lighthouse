from gi.repository import Adw, Gio, GLib, Gtk

from lighthouse import APP_ID


_css_loaded = False


def _ensure_css_loaded() -> None:
    global _css_loaded
    if _css_loaded:
        return
    from gi.repository import Gdk
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_resource("/land/rob/lighthouse/ui/style.css")
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_loaded = True


@Gtk.Template(resource_path="/land/rob/lighthouse/ui/window.ui")
class LighthouseWindow(Adw.ApplicationWindow):
    __gtype_name__ = "LighthouseWindow"

    window_title:  Adw.WindowTitle  = Gtk.Template.Child()
    toast_overlay: Adw.ToastOverlay = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._settings = Gio.Settings.new(APP_ID)

        # Restore persisted geometry.
        self.set_default_size(
            self._settings.get_int("window-width"),
            self._settings.get_int("window-height"),
        )
        if self._settings.get_boolean("window-maximized"):
            self.maximize()
        self.connect("close-request", self._on_close_request)

        action = Gio.SimpleAction.new("show-help-overlay", None)
        action.connect("activate", self._show_help_overlay)
        self.add_action(action)

        # Suite-standard window action: any child widget can fire a
        # toast via widget.activate_action("win.toast", GLib.Variant("s", msg)).
        toast_action = Gio.SimpleAction.new("toast", GLib.VariantType.new("s"))
        toast_action.connect("activate",
            lambda _a, p: self.toast_overlay.add_toast(Adw.Toast.new(p.get_string())))
        self.add_action(toast_action)

        _ensure_css_loaded()

    def _on_close_request(self, *_):
        # `get_default_size()` returns the configured default, not the
        # live size; use `get_width()`/`get_height()` so user resizes
        # actually persist.
        if not self.is_maximized():
            self._settings.set_int("window-width",  self.get_width())
            self._settings.set_int("window-height", self.get_height())
        self._settings.set_boolean("window-maximized", self.is_maximized())
        return False

    def _show_help_overlay(self, *_):
        builder = Gtk.Builder.new_from_resource(
            "/land/rob/lighthouse/ui/help-overlay.ui")
        overlay = builder.get_object("help_overlay")
        overlay.set_transient_for(self)
        overlay.present()
