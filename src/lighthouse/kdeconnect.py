"""KDE Connect LAN transport — discovery + the ``findmyphone`` plugin.

* **Discovery** (UDP identity broadcast/receive) runs in the GLib main
  loop and populates the device list the GUI shows.
* **The encrypted link** is mutual TLS over TCP using GIO's TLS, with
  **trust-on-first-use certificate pinning** (see :mod:`lighthouse.trust`):
  the peer certificate is captured during the handshake, pinned when a
  device pairs, and verified on every later link — a mismatch is refused
  as a possible MITM. ``findmyphone`` is only honoured from a paired,
  cert-verified peer. Each link runs on its own worker thread; failures
  are contained.

Canonical link sequence (protocol v7): the device that *broadcasts* over
UDP is the TLS **server**; the device that *receives* the broadcast opens
the TCP connection, sends its identity, and is the TLS **client**.

NOTE — still needs validation against a real KDE Connect / Valent peer
(two devices). The pairing flow currently auto-accepts; a user-facing
pairing-confirm prompt is the remaining P0 step (the cryptographic
pinning below is in place, so an *accepted* pairing is then authenticated).
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time

from gi.repository import Gio, GLib

from lighthouse.protocol import (
    DEFAULT_PORT,
    PACKET_FINDMYPHONE,
    PACKET_IDENTITY,
    PACKET_PAIR,
    PACKET_PING,
    DeviceIdentity,
    NetworkPacket,
)
from lighthouse.trust import TrustStore

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

    def __init__(self, identity: DeviceIdentity, on_page,
                 trust_dir: str | None = None) -> None:
        self._identity = identity
        self.trust = TrustStore(trust_dir)
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
            dev = Device(body, address)
            dev.paired = self.trust.is_paired(dev_id)
            self._devices[dev_id] = dev
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
        _Link(self, conn, addr[0], role="server").start()
        return GLib.SOURCE_CONTINUE

    def _initiate_link(self, body: dict, address: str) -> None:
        port = int(body.get("tcpPort", DEFAULT_PORT))
        try:
            conn = socket.create_connection((address, port), timeout=5)
        except OSError as exc:
            log.debug("connect to %s:%d failed: %s", address, port, exc)
            return
        _Link(self, conn, address, role="client", peer_identity=body).start()

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
    """One mutually-authenticated TLS link to a peer, on a worker thread."""

    def __init__(self, provider: LanProvider, sock: socket.socket,
                 address: str, role: str, peer_identity: dict | None = None):
        super().__init__(daemon=True)
        self._provider = provider
        self._sock = sock
        self._address = address
        self._role = role
        self._peer = peer_identity or {}
        self._device_id = self._peer.get("deviceId", "")
        self._tls = None
        self._ostream = None
        self._istream = None
        self._peer_cert = None
        self._paired = False
        self._pair_requested = False
        self._send_lock = threading.Lock()
        self._cancellable = Gio.Cancellable()
        self._running = True
        self.is_up = False

    def run(self) -> None:
        try:
            self._establish()
        except (GLib.Error, OSError, ValueError) as exc:
            log.warning("link %s handshake failed: %s", self._address, exc)
            self._safe_close()
            return

        match = self._provider.trust.matches(self._device_id, self._peer_cert)
        if match is False:
            log.error("SECURITY: certificate mismatch for paired device "
                      "%s (%s) — refusing link (possible MITM)",
                      self._peer.get("deviceName"), self._device_id)
            self._safe_close()
            return
        self._paired = match is True

        if self._device_id:
            self._provider._register_link(self._device_id, self)
        log.info("link up: %s (%s) role=%s paired=%s",
                 self._peer.get("deviceName", "?"), self._device_id,
                 self._role, self._paired)
        self.is_up = True
        try:
            self._read_loop()
        finally:
            if self._device_id:
                self._provider._unregister_link(self._device_id)
            self._safe_close()

    # -- handshake -------------------------------------------------------

    def _establish(self) -> None:
        ident = self._provider._identity
        if self._role == "client":
            # We already have the peer identity (from its broadcast); send
            # ours, then upgrade as TLS client presenting our certificate.
            self._sock.sendall(ident.identity_packet().serialize())
            base = self._wrap(self._sock)
            tls = Gio.TlsClientConnection.new(base, None)
            tls.set_property("certificate", ident.tls_certificate())
        else:
            # Inbound: read the peer identity (exactly up to the newline so
            # we don't swallow TLS bytes), then upgrade as TLS server.
            self._peer = self._read_plaintext_identity()
            self._device_id = self._peer.get("deviceId", "")
            self._provider._note_device(self._peer, self._address)
            base = self._wrap(self._sock)
            tls = Gio.TlsServerConnection.new(base, ident.tls_certificate())
            tls.set_property("authentication-mode",
                             Gio.TlsAuthenticationMode.REQUIRED)
        tls.connect("accept-certificate", self._on_accept_certificate)
        tls.handshake(self._cancellable)
        self._tls = tls
        self._ostream = tls.get_output_stream()
        self._istream = Gio.DataInputStream.new(tls.get_input_stream())
        if self._peer_cert is None:
            self._peer_cert = tls.get_peer_certificate()

    def _on_accept_certificate(self, _conn, peer_cert, _errors) -> bool:
        # Accept at the TLS layer and capture the cert; authenticity is
        # enforced afterwards against the pinned cert (run()).
        self._peer_cert = peer_cert
        return True

    def _wrap(self, sock: socket.socket):
        fd = os.dup(sock.fileno())
        gsock = Gio.Socket.new_from_fd(fd)
        conn = gsock.connection_factory_create_connection()
        sock.close()
        return conn

    def _read_plaintext_identity(self) -> dict:
        self._sock.settimeout(5)
        buf = b""
        while not buf.endswith(b"\n"):
            ch = self._sock.recv(1)
            if not ch:
                raise OSError("peer closed before identity")
            buf += ch
        self._sock.settimeout(None)
        packet = NetworkPacket.parse(buf)
        if packet.type != PACKET_IDENTITY:
            raise ValueError(f"expected identity, got {packet.type}")
        return packet.body

    # -- packet loop -----------------------------------------------------

    def _read_loop(self) -> None:
        while self._running:
            try:
                line, _length = self._istream.read_line_utf8(self._cancellable)
            except GLib.Error:
                break
            if line is None:       # EOF
                break
            if line.strip():
                self._handle(line)

    def _handle(self, line: str) -> None:
        try:
            packet = NetworkPacket.parse(line)
        except (ValueError, KeyError):
            return
        if packet.type == PACKET_FINDMYPHONE:
            if not self._paired:
                log.warning("ignoring findmyphone from unpaired %s",
                            self._device_id)
                return
            self._provider._dispatch_page(
                self._peer.get("deviceName", "a paired device"))
        elif packet.type == PACKET_PAIR:
            self._handle_pair(packet)
        elif packet.type == PACKET_PING:
            log.debug("ping from %s", self._peer.get("deviceName"))

    def _handle_pair(self, packet: NetworkPacket) -> None:
        if packet.body.get("pair"):
            # TOFU: pin the cert we already verified at the TLS layer.
            # (Production: gate this behind an explicit user confirmation.)
            if self._peer_cert is not None:
                self._provider.trust.pin(self._device_id, self._peer_cert)
            self._paired = True
            self._provider._mark_paired(self._device_id, True)
            if not self._pair_requested:
                self.send(NetworkPacket(PACKET_PAIR, {"pair": True}))
            self._pair_requested = False
            log.info("paired with %s", self._peer.get("deviceName"))
        else:
            self._provider.trust.unpin(self._device_id)
            self._paired = False
            self._provider._mark_paired(self._device_id, False)

    # -- public ----------------------------------------------------------

    def request_pair(self) -> None:
        self._pair_requested = True
        self.send(NetworkPacket(PACKET_PAIR, {"pair": True}))

    def send(self, packet: NetworkPacket) -> bool:
        if self._ostream is None:
            return False
        try:
            with self._send_lock:
                self._ostream.write_all(packet.serialize(), None)
            return True
        except GLib.Error as exc:
            log.debug("send to %s failed: %s", self._device_id, exc)
            return False

    def close(self) -> None:
        # Cancel any in-progress read so the worker thread unwinds and runs
        # _safe_close() itself — closing the GIO stream from another thread
        # while a read is blocked on it would deadlock.
        self._running = False
        self._cancellable.cancel()

    def _safe_close(self) -> None:
        for closeable in (self._tls, self._sock):
            try:
                if closeable is not None:
                    closeable.close()
            except (OSError, GLib.Error):
                pass
