"""``lighthouse agent`` — the always-on ``lighthoused`` role.

Headless GLib service that owns the reach-tier transports (P0: KDE Connect
on the LAN), exports the ``land.rob.lighthouse.Agent`` D-Bus interface,
and spawns the beam surface whenever a page arrives or Test Ring is
called. Closing the GUI never stops this; it runs under a systemd-user
unit (see ``data/land.rob.lighthouse.service``).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

from gi.repository import Gio, GLib

from lighthouse.kdeconnect import LanProvider
from lighthouse.logging_setup import configure_logging
from lighthouse.protocol import DeviceIdentity
from lighthouse.service import BUS_NAME, IFACE, INTROSPECTION_XML, OBJECT_PATH

log = logging.getLogger(__name__)


def _self_executable() -> str:
    """Path to the installed ``lighthouse`` launcher (for spawning beam)."""
    return os.path.abspath(sys.argv[0])


class Agent:
    def __init__(self) -> None:
        self._loop = GLib.MainLoop()
        self._identity = DeviceIdentity()
        self._provider = LanProvider(self._identity, on_page=self._on_page)
        self._provider.set_devices_changed_callback(self._emit_peers_changed)
        self._conn: Gio.DBusConnection | None = None
        self._reg_id = 0
        self._beam: subprocess.Popen | None = None

    def run(self) -> int:
        Gio.bus_own_name(
            Gio.BusType.SESSION, BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired, None, self._on_name_lost,
        )
        self._provider.start()
        try:
            self._loop.run()
        except KeyboardInterrupt:
            pass
        finally:
            self._provider.stop()
        return 0

    # -- D-Bus plumbing --------------------------------------------------

    def _on_bus_acquired(self, conn: Gio.DBusConnection, _name: str) -> None:
        self._conn = conn
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        self._reg_id = conn.register_object(
            OBJECT_PATH, node.interfaces[0], self._on_method_call, None, None)
        log.info("exported %s on the session bus", IFACE)

    def _on_name_lost(self, _conn, _name) -> None:
        log.error("lost (or could not acquire) %s — is another agent running?",
                  BUS_NAME)
        self._loop.quit()

    def _on_method_call(self, _conn, _sender, _path, _iface,
                        method, params, invocation) -> None:
        try:
            if method == "TestRing":
                self._ring("Test ring")
                invocation.return_value(None)
            elif method == "Page":
                (source,) = params.unpack()
                self._ring(source)
                invocation.return_value(None)
            elif method == "StopRing":
                self._stop_beam()
                invocation.return_value(None)
            elif method == "ListPeers":
                peers = json.dumps(self._provider.list_devices())
                invocation.return_value(GLib.Variant("(s)", (peers,)))
            elif method == "RingPeer":
                (device_id,) = params.unpack()
                sent = self._provider.ring_peer(device_id)
                invocation.return_value(GLib.Variant("(b)", (sent,)))
            else:
                invocation.return_error_literal(
                    Gio.dbus_error_quark(),
                    Gio.DBusError.UNKNOWN_METHOD, f"no method {method}")
        except Exception as exc:  # never let a bad call kill the agent
            log.exception("method %s failed", method)
            invocation.return_error_literal(
                Gio.dbus_error_quark(), Gio.DBusError.FAILED, str(exc))

    # -- pages / ringing -------------------------------------------------

    def _on_page(self, source: str) -> None:
        """Called (in the main loop) when a reach tier delivers a page."""
        self._ring(source)
        if self._conn is not None:
            self._conn.emit_signal(None, OBJECT_PATH, IFACE, "Paged",
                                   GLib.Variant("(s)", (source,)))

    def _ring(self, source: str) -> None:
        self._stop_beam()
        argv = [_self_executable(), "beam", "--source", source]
        log.info("staging beam: %s", source)
        try:
            self._beam = subprocess.Popen(argv)
        except OSError:
            log.exception("failed to spawn beam")

    def _stop_beam(self) -> None:
        if self._beam is not None and self._beam.poll() is None:
            self._beam.terminate()
        self._beam = None

    def _emit_peers_changed(self) -> None:
        if self._conn is not None:
            self._conn.emit_signal(None, OBJECT_PATH, IFACE,
                                   "PeersChanged", None)


def run_agent(_argv: list[str]) -> int:
    configure_logging()
    log.info("lighthouse agent starting")
    return Agent().run()
