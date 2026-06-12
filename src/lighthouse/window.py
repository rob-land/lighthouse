import logging
import os
import subprocess
import sys
from gettext import gettext as _

from gi.repository import Adw, Gio, GLib, Gtk

from lighthouse import APP_ID
from lighthouse.service import AgentClient

log = logging.getLogger(__name__)

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

    window_title:      Adw.WindowTitle      = Gtk.Template.Child()
    toast_overlay:     Adw.ToastOverlay      = Gtk.Template.Child()
    test_ring_button:  Gtk.Button            = Gtk.Template.Child()
    status_row:        Adw.ActionRow         = Gtk.Template.Child()
    status_icon:       Gtk.Image             = Gtk.Template.Child()
    peers_group:       Adw.PreferencesGroup  = Gtk.Template.Child()

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

        # Connect to the agent (may not be running yet).
        self._peer_rows: list[Gtk.Widget] = []
        try:
            self._agent = AgentClient()
            self._agent.connect_peers_changed(self._refresh_peers)
        except GLib.Error:
            log.warning("could not connect to session bus", exc_info=True)
            self._agent = None

        self.test_ring_button.connect("clicked", self._on_test_ring)
        self._refresh_status()
        self._refresh_peers()

    # -- agent-backed UI -------------------------------------------------

    def _refresh_status(self) -> None:
        if self._agent is not None and self._agent.available:
            self.status_row.set_subtitle(_("Running — this phone is findable"))
            self.status_icon.set_from_icon_name("emblem-ok-symbolic")
        else:
            self.status_row.set_subtitle(
                _("Not running — start the lighthouse agent service"))
            self.status_icon.set_from_icon_name("dialog-warning-symbolic")

    def _refresh_peers(self, *_) -> None:
        for row in self._peer_rows:
            self.peers_group.remove(row)
        self._peer_rows.clear()

        peers = []
        if self._agent is not None and self._agent.available:
            try:
                peers = self._agent.list_peers()
            except GLib.Error:
                log.debug("ListPeers failed", exc_info=True)

        if not peers:
            row = Adw.ActionRow(
                title=_("No devices found"),
                subtitle=_("Pair a device in KDE Connect, or wait for "
                           "discovery"))
            row.set_sensitive(False)
            self.peers_group.add(row)
            self._peer_rows.append(row)
            return

        for peer in peers:
            row = Adw.ActionRow(title=peer.get("name", _("Unknown")))
            kind = peer.get("type", "desktop")
            paired = peer.get("paired")
            row.set_subtitle(_("Paired") if paired else kind.capitalize())
            ring = Gtk.Button(
                icon_name="audio-volume-high-symbolic",
                valign=Gtk.Align.CENTER,
                tooltip_text=_("Ring this device"))
            ring.add_css_class("flat")
            ring.connect("clicked", self._on_ring_peer, peer.get("id", ""))
            row.add_suffix(ring)
            self.peers_group.add(row)
            self._peer_rows.append(row)

    # -- actions ---------------------------------------------------------

    def _on_test_ring(self, *_) -> None:
        if self._agent is not None and self._agent.available:
            try:
                self._agent.test_ring()
                return
            except GLib.Error:
                log.debug("TestRing via agent failed; falling back", exc_info=True)
        # Fallback: spawn the beam directly so Test Ring works even with
        # no agent running.
        self._spawn_beam("Test ring")

    def _on_ring_peer(self, _button, device_id: str) -> None:
        if not device_id or self._agent is None or not self._agent.available:
            self.toast_overlay.add_toast(
                Adw.Toast.new(_("Agent not running")))
            return
        try:
            sent = self._agent.ring_peer(device_id)
        except GLib.Error:
            sent = False
        self.toast_overlay.add_toast(Adw.Toast.new(
            _("Ringing…") if sent else _("Could not reach that device")))

    @staticmethod
    def _spawn_beam(source: str) -> None:
        try:
            subprocess.Popen([os.path.abspath(sys.argv[0]), "beam",
                              "--source", source])
        except OSError:
            log.exception("failed to spawn beam")

    # -- window plumbing -------------------------------------------------

    def _on_close_request(self, *_):
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
