"""The ``land.rob.lighthouse.Agent`` D-Bus interface, shared by the agent
(which exports it) and the GUI (which consumes it via :class:`AgentClient`).

Keeping the introspection XML in one place means the two roles can never
drift out of sync.
"""

from __future__ import annotations

import json
import logging

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

BUS_NAME = "land.rob.lighthouse.Agent"
OBJECT_PATH = "/land/rob/lighthouse/Agent"
IFACE = "land.rob.lighthouse.Agent"

INTROSPECTION_XML = f"""
<node>
  <interface name="{IFACE}">
    <!-- Ring this phone locally (the Test Ring path). -->
    <method name="TestRing"/>
    <!-- Stage a ring as if paged; `source` is shown on the beam. -->
    <method name="Page">
      <arg type="s" name="source" direction="in"/>
    </method>
    <!-- Dismiss any active ring. -->
    <method name="StopRing"/>
    <!-- JSON array of known devices: [{{id,name,type,reachable}}]. -->
    <method name="ListPeers">
      <arg type="s" name="peers_json" direction="out"/>
    </method>
    <!-- Ask a paired LAN peer to ring. Returns whether it was sent. -->
    <method name="RingPeer">
      <arg type="s" name="device_id" direction="in"/>
      <arg type="b" name="sent" direction="out"/>
    </method>
    <signal name="Paged">
      <arg type="s" name="source"/>
    </signal>
    <signal name="PeersChanged"/>
  </interface>
</node>
"""


class AgentClient:
    """Thin session-bus client used by the GUI.

    All calls are defensive: if the agent is not running (and cannot be
    D-Bus activated) the methods raise GLib.Error, which the caller treats
    as "agent unavailable" and degrades gracefully.
    """

    def __init__(self) -> None:
        self._proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.DO_NOT_AUTO_START_AT_CONSTRUCTION,
            None, BUS_NAME, OBJECT_PATH, IFACE, None,
        )

    @property
    def available(self) -> bool:
        return self._proxy.get_name_owner() is not None

    def test_ring(self) -> None:
        self._proxy.call_sync("TestRing", None,
                              Gio.DBusCallFlags.NONE, -1, None)

    def page(self, source: str) -> None:
        self._proxy.call_sync("Page", GLib.Variant("(s)", (source,)),
                              Gio.DBusCallFlags.NONE, -1, None)

    def stop_ring(self) -> None:
        self._proxy.call_sync("StopRing", None,
                              Gio.DBusCallFlags.NONE, -1, None)

    def list_peers(self) -> list[dict]:
        result = self._proxy.call_sync("ListPeers", None,
                                       Gio.DBusCallFlags.NONE, -1, None)
        return json.loads(result.unpack()[0] or "[]")

    def ring_peer(self, device_id: str) -> bool:
        result = self._proxy.call_sync(
            "RingPeer", GLib.Variant("(s)", (device_id,)),
            Gio.DBusCallFlags.NONE, -1, None)
        return bool(result.unpack()[0])

    def connect_peers_changed(self, callback) -> None:
        """Invoke `callback()` whenever the agent emits PeersChanged."""
        def _on_signal(_proxy, _sender, signal_name, _params):
            if signal_name == "PeersChanged":
                callback()
        self._proxy.connect("g-signal", _on_signal)
