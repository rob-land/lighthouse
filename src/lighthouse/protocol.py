"""KDE Connect network packets and this device's persistent identity.

Wire format is newline-delimited JSON, one object per packet::

    {"id": <millis>, "type": "kdeconnect.<plugin>", "body": { ... }}

The *identity* packet additionally carries ``deviceId`` / ``deviceName`` /
``tcpPort`` and the capability lists exchanged during discovery. We keep
this module dependency-light (no GTK) so the headless agent can import it
without pulling the display stack.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import secrets
import socket
import time

from gi.repository import GLib

log = logging.getLogger(__name__)

# Protocol version 7 is the long-stable wire version spoken by current
# KDE Connect and Valent releases.
PROTOCOL_VERSION = 7
DEFAULT_PORT = 1716

# Capabilities Lighthouse speaks today (P0): ring + ping. Grows as plugins
# land (battery, locate, …).
INCOMING_CAPS = ["kdeconnect.findmyphone.request", "kdeconnect.ping"]
OUTGOING_CAPS = ["kdeconnect.findmyphone.request", "kdeconnect.ping"]

PACKET_IDENTITY = "kdeconnect.identity"
PACKET_PAIR = "kdeconnect.pair"
PACKET_FINDMYPHONE = "kdeconnect.findmyphone.request"
PACKET_PING = "kdeconnect.ping"


def _now_ms() -> int:
    return int(time.time() * 1000)


class NetworkPacket:
    """A single KDE Connect packet."""

    __slots__ = ("type", "body", "id")

    def __init__(self, type_: str, body: dict | None = None, id: int | None = None):
        self.type = type_
        self.body = body if body is not None else {}
        self.id = id if id is not None else _now_ms()

    def serialize(self) -> bytes:
        wire = {"id": self.id, "type": self.type, "body": self.body}
        return (json.dumps(wire, separators=(",", ":")) + "\n").encode("utf-8")

    @classmethod
    def parse(cls, raw: str | bytes) -> "NetworkPacket":
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        return cls(data["type"], data.get("body", {}), data.get("id"))

    def __repr__(self) -> str:
        return f"<NetworkPacket {self.type} {self.body!r}>"


def config_dir() -> str:
    path = os.path.join(GLib.get_user_config_dir(), "lighthouse")
    os.makedirs(path, exist_ok=True)
    return path


class DeviceIdentity:
    """This device's stable id, display name, and TLS certificate.

    The certificate is self-signed with the device id as its common name,
    matching the KDE Connect convention, and persisted so the peer's
    trust-on-first-use pin survives restarts.
    """

    def __init__(self) -> None:
        cdir = config_dir()
        self._id_path = os.path.join(cdir, "device-id")
        self.cert_path = os.path.join(cdir, "certificate.pem")
        self.key_path = os.path.join(cdir, "private.pem")
        self.device_id = self._load_or_create_id()
        self.device_name = self._default_name()
        self._ensure_certificate()

    def _load_or_create_id(self) -> str:
        try:
            with open(self._id_path, encoding="ascii") as fh:
                existing = fh.read().strip()
            if existing:
                return existing
        except FileNotFoundError:
            pass
        # KDE Connect device ids are restricted to [A-Za-z0-9_].
        new_id = "lighthouse_" + secrets.token_hex(12)
        with open(self._id_path, "w", encoding="ascii") as fh:
            fh.write(new_id)
        return new_id

    @staticmethod
    def _default_name() -> str:
        try:
            host = socket.gethostname().split(".")[0]
        except OSError:
            host = "phone"
        return f"{host} (Lighthouse)"

    def _ensure_certificate(self) -> None:
        if os.path.exists(self.cert_path) and os.path.exists(self.key_path):
            return
        # Lazy import: cryptography is only needed at first run / cert
        # rotation, and only by the agent role.
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, self.device_id),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "KDE"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Kde connect"),
        ])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .sign(key, hashes.SHA256())
        )
        with open(self.key_path, "wb") as fh:
            fh.write(key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ))
        os.chmod(self.key_path, 0o600)
        with open(self.cert_path, "wb") as fh:
            fh.write(cert.public_bytes(serialization.Encoding.PEM))
        log.info("generated device certificate for %s", self.device_id)

    def identity_packet(self, tcp_port: int = DEFAULT_PORT) -> NetworkPacket:
        return NetworkPacket(PACKET_IDENTITY, {
            "deviceId": self.device_id,
            "deviceName": self.device_name,
            "deviceType": "phone",
            "protocolVersion": PROTOCOL_VERSION,
            "tcpPort": tcp_port,
            "incomingCapabilities": INCOMING_CAPS,
            "outgoingCapabilities": OUTGOING_CAPS,
        })
