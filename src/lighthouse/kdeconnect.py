"""KDE Connect LAN transport — discovery + the ``findmyphone`` plugin.

Two halves with very different maturity:

* **Discovery** (UDP identity broadcast/receive) is complete and runs in
  the GLib main loop. It populates the device list the GUI shows.
* **The encrypted link** (TCP + mutual TLS + pairing) is a first draft.
  The handshake roles and trust-on-first-use pinning follow the KDE
  Connect protocol as documented, but have **not yet been validated
  against a real KDE Connect / Valent peer** — that needs two devices and
  is the first on-device task (see TODO.md P0). Each link runs in its own
  worker thread; failures are contained so discovery and the local ring
  keep working regardless.

Canonical link sequence (protocol v7): the device that *broadcasts* over
UDP becomes the TLS **server**; the device that *receives* the broadcast
opens the TCP connection, sends its own identity, and becomes the TLS
**client**.
"""

from __future__ import annotations

import logging
import socket
import ssl
import threading
import time

from gi.repository import GLib

from lighthouse.protocol import (
    DEFAULT_PORT,
    PACKET_FINDMYPHONE,
    PACKET_IDENTITY,
    PACKET_PAIR,
    PACKET_PING,
    DeviceIdentity,
    NetworkPacket,
)

log = logging.getLogger(__name__)

ANNOUNCE_INTERVAL_S = 60
DEVICE_STALE_S = 5 * 60


class Device:
    """A peer seen on the LAN."""

    def __init__(self, body: dict, address: str):
        self.id: str = body.get("deviceId", "")
        self.name: str = body.get("deviceName", "Unknown")
        self.type: str = body.get("deviceType", "desktop")
        self.address: str = address
        self.tcp_port: int = int(body.get("tcpPort", DEFAULT_PORT))
        self.paired: bool = False
        self.last_seen: float = time.monotonic()

    @property
    def reachable(self) -> bool:
        return (time.monotonic() - self.last_seen) < DEVICE_STALE_S

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "type": self.type,
                "reachable": self.reachable, "paired": self.paired}


class LanProvider:
    """Owns the UDP discovery sockets, the TCP server, and active links."""

    def __init__(self, identity: DeviceIdentity, on_page) -> None:
        self._identity = identity
        self._on_page = on_page          # called (source_name) in main loop
        self._on_devices_changed = None  # optional callback, main loop
        self._devices: dict[str, Device] = {}
        self._links: dict[str, "_Link"] = {}
        self._udp: socket.socket | None = None
        self._tcp: socket.socket | None = None
        self._announce_id = 0

    def set_devices_changed_callback(self, cb) -> None:
        self._on_devices_changed = cb

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            self._udp.bind(("", DEFAULT_PORT))
        except OSError as exc:
            log.error("cannot bind UDP %d (%s); discovery disabled",
                      DEFAULT_PORT, exc)
            self._udp = None
        else:
            self._udp.setblocking(False)
            GLib.unix_fd_add_full(GLib.PRIORITY_DEFAULT, self._udp.fileno(),
                                  GLib.IOCondition.IN, self._on_udp_readable)

        self._tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._tcp.bind(("", DEFAULT_PORT))
            self._tcp.listen(8)
        except OSError as exc:
            log.error("cannot listen TCP %d (%s); inbound links disabled",
                      DEFAULT_PORT, exc)
            self._tcp = None
        else:
            self._tcp.setblocking(False)
            GLib.unix_fd_add_full(GLib.PRIORITY_DEFAULT, self._tcp.fileno(),
                                  GLib.IOCondition.IN, self._on_tcp_acceptable)

        self.announce()
        self._announce_id = GLib.timeout_add_seconds(
            ANNOUNCE_INTERVAL_S, self._on_announce_tick)
        log.info("KDE Connect provider started (device id %s)",
                 self._identity.device_id)

    def stop(self) -> None:
        if self._announce_id:
            GLib.source_remove(self._announce_id)
            self._announce_id = 0
        for link in list(self._links.values()):
            link.close()
        for sock in (self._udp, self._tcp):
            if sock is not None:
                sock.close()
        self._udp = self._tcp = None

    # -- discovery -------------------------------------------------------

    def announce(self) -> None:
        if self._udp is None:
            return
        packet = self._identity.identity_packet().serialize()
        try:
            self._udp.sendto(packet, ("255.255.255.255", DEFAULT_PORT))
        except OSError:
            log.debug("UDP announce failed", exc_info=True)

    def _on_announce_tick(self) -> bool:
        self.announce()
        self._prune_devices()
        return GLib.SOURCE_CONTINUE

    def _on_udp_readable(self, _fd, _cond) -> bool:
        try:
            data, addr = self._udp.recvfrom(65535)
        except OSError:
            return GLib.SOURCE_CONTINUE
        try:
            packet = NetworkPacket.parse(data)
        except (ValueError, KeyError):
            return GLib.SOURCE_CONTINUE
        if packet.type != PACKET_IDENTITY:
            return GLib.SOURCE_CONTINUE
        dev_id = packet.body.get("deviceId")
        if not dev_id or dev_id == self._identity.device_id:
            return GLib.SOURCE_CONTINUE  # ignore our own broadcast / noise

        self._note_device(packet.body, addr[0])
        # We received a broadcast → we are the TLS client; connect out.
        if dev_id not in self._links:
            self._initiate_link(packet.body, addr[0])
        return GLib.SOURCE_CONTINUE

    def _note_device(self, body: dict, address: str) -> None:
        dev_id = body["deviceId"]
        existing = self._devices.get(dev_id)
        if existing is None:
            self._devices[dev_id] = Device(body, address)
            log.info("discovered %s (%s) at %s",
                     body.get("deviceName"), dev_id, address)
            self._notify_devices_changed()
        else:
            existing.last_seen = time.monotonic()
            existing.address = address

    def _prune_devices(self) -> None:
        stale = [d for d, dev in self._devices.items() if not dev.reachable]
        for dev_id in stale:
            del self._devices[dev_id]
        if stale:
            self._notify_devices_changed()

    def _notify_devices_changed(self) -> None:
        if self._on_devices_changed is not None:
            self._on_devices_changed()

    def list_devices(self) -> list[dict]:
        return [d.as_dict() for d in self._devices.values()]

    # -- links -----------------------------------------------------------

    def _on_tcp_acceptable(self, _fd, _cond) -> bool:
        try:
            conn, addr = self._tcp.accept()
        except OSError:
            return GLib.SOURCE_CONTINUE
        # Inbound connection → the peer received our broadcast, so we are
        # the TLS server.
        link = _Link(self, conn, addr[0], role="server")
        link.start()
        return GLib.SOURCE_CONTINUE

    def _initiate_link(self, body: dict, address: str) -> None:
        port = int(body.get("tcpPort", DEFAULT_PORT))
        try:
            conn = socket.create_connection((address, port), timeout=5)
        except OSError as exc:
            log.debug("connect to %s:%d failed: %s", address, port, exc)
            return
        link = _Link(self, conn, address, role="client",
                     peer_identity=body)
        link.start()

    def ring_peer(self, device_id: str) -> bool:
        link = self._links.get(device_id)
        if link is None:
            log.info("ring_peer: no active link to %s", device_id)
            return False
        return link.send(NetworkPacket(PACKET_FINDMYPHONE))

    # -- called from link threads (always re-enter the main loop) --------

    def _register_link(self, device_id: str, link: "_Link") -> None:
        GLib.idle_add(self._links.__setitem__, device_id, link)

    def _unregister_link(self, device_id: str) -> None:
        GLib.idle_add(lambda: self._links.pop(device_id, None))

    def _dispatch_page(self, source_name: str) -> None:
        GLib.idle_add(self._on_page, source_name)

    def _mark_paired(self, device_id: str, paired: bool) -> None:
        def _apply():
            dev = self._devices.get(device_id)
            if dev is not None:
                dev.paired = paired
                self._notify_devices_changed()
            return False
        GLib.idle_add(_apply)


class _Link(threading.Thread):
    """One TLS link to a peer, run on a worker thread.

    NOTE (first draft): TLS uses CERT_NONE on both sides, which brings up
    an encrypted channel but does not yet pin/verify the peer certificate.
    Real pairing — exchanging and trusting certs, and a pairing-confirm UI
    — is the next step and must be validated with a real KDE Connect peer
    before this is trustworthy. Until then, pairing requests are
    auto-accepted, which is fine for a controlled bring-up test and NOT
    for production.
    """

    def __init__(self, provider: LanProvider, sock: socket.socket,
                 address: str, role: str, peer_identity: dict | None = None):
        super().__init__(daemon=True)
        self._provider = provider
        self._sock = sock
        self._address = address
        self._role = role
        self._peer = peer_identity or {}
        self._tls: ssl.SSLSocket | None = None
        self._send_lock = threading.Lock()
        self._device_id = self._peer.get("deviceId", "")
        self._running = True

    def run(self) -> None:
        try:
            self._handshake()
        except (ssl.SSLError, OSError, ValueError) as exc:
            log.warning("link %s handshake failed: %s", self._address, exc)
            self._sock.close()
            return
        if self._device_id:
            self._provider._register_link(self._device_id, self)
        log.info("link up: %s (%s) via %s",
                 self._peer.get("deviceName", "?"), self._device_id, self._role)
        try:
            self._read_loop()
        finally:
            if self._device_id:
                self._provider._unregister_link(self._device_id)
            self._close_socket()

    def _handshake(self) -> None:
        ident = self._provider._identity
        if self._role == "client":
            # We already have the peer identity (from its UDP broadcast);
            # send ours over TCP, then upgrade as TLS client.
            self._sock.sendall(ident.identity_packet().serialize())
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ctx.load_cert_chain(ident.cert_path, ident.key_path)
            self._tls = ctx.wrap_socket(self._sock, server_side=False)
        else:
            # Inbound: read the peer's identity line, then upgrade as
            # TLS server.
            self._peer = self._read_plaintext_identity().body
            self._device_id = self._peer.get("deviceId", "")
            self._provider._note_device(self._peer, self._address)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.verify_mode = ssl.CERT_NONE
            ctx.load_cert_chain(ident.cert_path, ident.key_path)
            self._tls = ctx.wrap_socket(self._sock, server_side=True)

    def _read_plaintext_identity(self) -> NetworkPacket:
        buf = b""
        self._sock.settimeout(5)
        while b"\n" not in buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise OSError("peer closed before identity")
            buf += chunk
        self._sock.settimeout(None)
        line = buf.split(b"\n", 1)[0]
        packet = NetworkPacket.parse(line)
        if packet.type != PACKET_IDENTITY:
            raise ValueError(f"expected identity, got {packet.type}")
        return packet

    def _read_loop(self) -> None:
        buf = b""
        while self._running:
            try:
                chunk = self._tls.recv(4096)
            except (ssl.SSLError, OSError):
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    self._handle(line)

    def _handle(self, line: bytes) -> None:
        try:
            packet = NetworkPacket.parse(line)
        except (ValueError, KeyError):
            return
        if packet.type == PACKET_FINDMYPHONE:
            name = self._peer.get("deviceName", "a paired device")
            log.info("findmyphone request from %s", name)
            self._provider._dispatch_page(name)
        elif packet.type == PACKET_PAIR:
            self._handle_pair(packet)
        elif packet.type == PACKET_PING:
            log.debug("ping from %s", self._peer.get("deviceName"))

    def _handle_pair(self, packet: NetworkPacket) -> None:
        wants = bool(packet.body.get("pair", False))
        if wants:
            # First-draft TOFU: accept and echo a pair confirmation.
            self.send(NetworkPacket(PACKET_PAIR, {"pair": True}))
            self._provider._mark_paired(self._device_id, True)
            log.info("auto-accepted pairing with %s (first-draft TOFU)",
                     self._peer.get("deviceName"))
        else:
            self._provider._mark_paired(self._device_id, False)

    def send(self, packet: NetworkPacket) -> bool:
        if self._tls is None:
            return False
        try:
            with self._send_lock:
                self._tls.sendall(packet.serialize())
            return True
        except (ssl.SSLError, OSError) as exc:
            log.debug("send to %s failed: %s", self._device_id, exc)
            return False

    def close(self) -> None:
        self._running = False
        self._close_socket()

    def _close_socket(self) -> None:
        try:
            (self._tls or self._sock).close()
        except OSError:
            pass
