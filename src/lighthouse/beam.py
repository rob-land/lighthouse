"""``lighthouse beam`` — the "I'm here" takeover surface.

This is what makes a phone *findable* once a page arrives on any reach
tier. It is spawned as its own process by the agent (or directly by the
GUI's Test Ring), so it survives the GUI being closed.

The ring is deliberately hard to silence:

* Audio is a **raw GStreamer stream**, not a feedbackd event, so it is
  not gated by the ``silent`` feedback profile — it pierces silent mode
  the way an alarm does.
* The default sink is unmuted and raised via ``wpctl`` for the duration,
  then restored, so a turned-down phone still rings at full volume.
* Haptics go through libfeedback when present (best-effort).

P2 will promote the window to a ``gtk4-layer-shell`` overlay over the
lock screen, add the torch strobe, and take a logind wake-inhibit; for
now it is a normal fullscreen always-on-top window.
"""

from __future__ import annotations

import argparse
import logging
import subprocess

from gi.repository import Adw, Gio, GLib, Gst, Gtk

from lighthouse import APP_ID
from lighthouse.logging_setup import configure_logging

log = logging.getLogger(__name__)

DEFAULT_DURATION_S = 60
_RING_CSS = b"""
.beam-surface { background-color: #1a5fb4; color: white; }
.beam-title { font-size: 28px; font-weight: 800; }
.beam-source { font-size: 16px; opacity: 0.85; }
.beam-stop {
  font-size: 22px; font-weight: 700;
  padding: 24px; margin: 24px; border-radius: 24px;
}
"""


class Ringer:
    """Loud, silent-piercing audio via GStreamer + a wpctl sink override."""

    def __init__(self) -> None:
        Gst.init(None)
        # A synthesised two-tone klaxon — no bundled asset needed. The
        # pipewire/pulse sink chosen by autoaudiosink is not subject to
        # the feedbackd profile, which is what lets us override silent.
        self._pipeline = Gst.parse_launch(
            "audiotestsrc name=src is-live=true wave=sine freq=900 volume=1.0 "
            "! audioconvert ! audioresample ! autoaudiosink"
        )
        self._src = self._pipeline.get_by_name("src")
        self._toggle_id = 0
        self._high = False
        self._saved_sink: tuple[str, bool] | None = None

    def start(self) -> None:
        self._raise_sink()
        self._pipeline.set_state(Gst.State.PLAYING)
        # Alternate the pitch a few times a second for a klaxon feel.
        self._toggle_id = GLib.timeout_add(450, self._toggle_pitch)

    def _toggle_pitch(self) -> bool:
        self._high = not self._high
        self._src.set_property("freq", 1180.0 if self._high else 760.0)
        return GLib.SOURCE_CONTINUE

    def stop(self) -> None:
        if self._toggle_id:
            GLib.source_remove(self._toggle_id)
            self._toggle_id = 0
        self._pipeline.set_state(Gst.State.NULL)
        self._restore_sink()

    # -- wpctl sink override (save → max+unmute → restore) ---------------

    def _raise_sink(self) -> None:
        try:
            vol = subprocess.run(
                ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                capture_output=True, text=True, check=True,
            ).stdout
            # "Volume: 0.42 [MUTED]"
            level = float(vol.split()[1])
            muted = "MUTED" in vol
            self._saved_sink = (f"{level:.2f}", muted)
            subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"],
                           check=False)
            subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "1.0"],
                           check=False)
        except (OSError, subprocess.CalledProcessError, ValueError, IndexError):
            log.warning("could not adjust default sink via wpctl", exc_info=True)
            self._saved_sink = None

    def _restore_sink(self) -> None:
        if not self._saved_sink:
            return
        level, muted = self._saved_sink
        subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", level],
                       check=False)
        subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@",
                        "1" if muted else "0"], check=False)
        self._saved_sink = None


def _start_haptics() -> "object | None":
    """Best-effort repeating vibration through libfeedback, if available.

    Returns the running Lfb.Event (so it can be ended) or None. libfeedback
    is present on Phosh devices but not on a typical desktop dev box.
    """
    try:
        import gi
        gi.require_version("Lfb", "0.0")
        from gi.repository import Lfb
        Lfb.init(APP_ID)
        event = Lfb.Event.new("phone-incoming-call")
        event.set_timeout(0)  # until explicitly ended
        event.trigger_feedback()
        return event
    except (ValueError, ImportError, GLib.Error):
        log.debug("libfeedback unavailable; skipping haptics")
        return None


class BeamWindow(Adw.ApplicationWindow):
    __gtype_name__ = "LighthouseBeamWindow"

    def __init__(self, source: str, duration: int, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Lighthouse")
        self.set_default_size(360, 720)

        provider = Gtk.CssProvider()
        provider.load_from_data(_RING_CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self._ringer = Ringer()
        self._haptics = None
        self._timeout_id = 0

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      valign=Gtk.Align.CENTER, halign=Gtk.Align.FILL,
                      margin_top=24, margin_bottom=24,
                      margin_start=12, margin_end=12)
        box.add_css_class("beam-surface")

        icon = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
        icon.set_pixel_size(72)
        title = Gtk.Label(label="Someone’s looking for this phone")
        title.add_css_class("beam-title")
        title.set_wrap(True)
        title.set_justify(Gtk.Justification.CENTER)
        src_label = Gtk.Label(label=source)
        src_label.add_css_class("beam-source")

        stop = Gtk.Button(label="I’ve got it")
        stop.add_css_class("beam-stop")
        stop.add_css_class("suggested-action")
        stop.connect("clicked", lambda *_: self.dismiss())

        spacer_top = Gtk.Box(vexpand=True)
        spacer_bottom = Gtk.Box(vexpand=True)
        for w in (spacer_top, icon, title, src_label, spacer_bottom, stop):
            box.append(w)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar(show_title=False))
        toolbar.add_css_class("beam-surface")
        toolbar.set_content(box)
        self.set_content(toolbar)

        # Escape also dismisses.
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self.add_controller(key)

        self.connect("map", self._on_map)
        self._duration = duration

    def _on_map(self, *_):
        self.fullscreen()
        self._ringer.start()
        self._haptics = _start_haptics()
        if self._duration > 0:
            self._timeout_id = GLib.timeout_add_seconds(
                self._duration, self._on_timeout)

    def _on_key(self, _ctrl, keyval, _code, _state):
        from gi.repository import Gdk
        if keyval == Gdk.KEY_Escape:
            self.dismiss()
            return True
        return False

    def _on_timeout(self) -> bool:
        log.info("ring timed out after %ds", self._duration)
        self.dismiss()
        return GLib.SOURCE_REMOVE

    def dismiss(self) -> None:
        if self._timeout_id:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = 0
        self._ringer.stop()
        if self._haptics is not None:
            try:
                self._haptics.end_feedback()
            except GLib.Error:
                pass
            self._haptics = None
        self.close()


class BeamApplication(Adw.Application):
    def __init__(self, source: str, duration: int):
        # NON_UNIQUE so each page spawns an independent ring surface.
        super().__init__(application_id=APP_ID + ".Beam",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self._source = source
        self._duration = duration

    def do_activate(self):
        win = self.props.active_window
        if win is None:
            win = BeamWindow(self._source, self._duration, application=self)
        win.present()


def run_beam(argv: list[str]) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="lighthouse beam")
    parser.add_argument("--source", default="this device",
                        help="who/what triggered the ring (shown on screen)")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION_S,
                        help="auto-stop after N seconds (0 = until dismissed)")
    args = parser.parse_args(argv)
    log.info("beam: ringing (source=%r, duration=%ds)", args.source, args.duration)
    return BeamApplication(args.source, args.duration).run([])
