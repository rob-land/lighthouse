#!/usr/bin/env python3
"""Integration tests for the KDE Connect link layer.

Validates the encrypted-link path end-to-end between two Lighthouse
instances over a loopback TCP connection — no second physical device
needed — covering: mutual TLS, pairing with certificate pinning, a
``findmyphone`` from a paired peer triggering the page callback, and the
trust store refusing a mismatched (MITM) certificate.

Run directly (``PYTHONPATH=src python3 tests/test_link.py``) or via
``meson test``.
"""

import socket
import tempfile
import threading
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from lighthouse.kdeconnect import LanProvider, _Link  # noqa: E402
from lighthouse.protocol import (  # noqa: E402
    PACKET_FINDMYPHONE,
    DeviceIdentity,
    NetworkPacket,
)
from lighthouse.trust import TrustStore  # noqa: E402


def _wait_until(pred, timeout=8.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _loopback_pair():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    server, _ = listener.accept()
    listener.close()
    return server, client


def test_pair_and_ring():
    a_dir, b_dir = tempfile.mkdtemp(), tempfile.mkdtemp()
    pages = []
    loop = GLib.MainLoop()

    def on_page_a(source):
        pages.append(source)
        loop.quit()

    id_a = DeviceIdentity(a_dir)
    id_b = DeviceIdentity(b_dir)
    prov_a = LanProvider(id_a, on_page=on_page_a, trust_dir=a_dir)
    prov_b = LanProvider(id_b, on_page=lambda _s: None, trust_dir=b_dir)

    ssock, csock = _loopback_pair()
    # A broadcast (conceptually) → A is the TLS server; B connects → client.
    link_a = _Link(prov_a, ssock, "127.0.0.1", role="server")
    link_b = _Link(prov_b, csock, "127.0.0.1", role="client",
                   peer_identity=id_a.identity_packet().body)
    link_a.start()
    link_b.start()

    def driver():
        if not _wait_until(lambda: link_a.is_up and link_b.is_up):
            loop.quit()
            return
        link_b.request_pair()
        _wait_until(lambda: prov_a.trust.is_paired(id_b.device_id)
                    and prov_b.trust.is_paired(id_a.device_id))
        link_b.send(NetworkPacket(PACKET_FINDMYPHONE))

    threading.Thread(target=driver, daemon=True).start()
    GLib.timeout_add_seconds(15, loop.quit)
    loop.run()

    assert link_a.is_up and link_b.is_up, "links did not establish"
    assert prov_a.trust.is_paired(id_b.device_id), "A failed to pin B"
    assert prov_b.trust.is_paired(id_a.device_id), "B failed to pin A"
    assert pages == [id_b.device_name], f"page not delivered: {pages!r}"
    link_a.close()
    link_b.close()
    print("OK  pair + pin + ring:  A paged by", pages[0])


def test_unpaired_findmyphone_ignored():
    """A findmyphone before pairing must NOT ring."""
    a_dir, b_dir = tempfile.mkdtemp(), tempfile.mkdtemp()
    pages = []
    loop = GLib.MainLoop()
    id_a = DeviceIdentity(a_dir)
    id_b = DeviceIdentity(b_dir)
    prov_a = LanProvider(id_a, on_page=lambda s: pages.append(s), trust_dir=a_dir)
    prov_b = LanProvider(id_b, on_page=lambda _s: None, trust_dir=b_dir)

    ssock, csock = _loopback_pair()
    link_a = _Link(prov_a, ssock, "127.0.0.1", role="server")
    link_b = _Link(prov_b, csock, "127.0.0.1", role="client",
                   peer_identity=id_a.identity_packet().body)
    link_a.start()
    link_b.start()

    def driver():
        if _wait_until(lambda: link_a.is_up and link_b.is_up):
            link_b.send(NetworkPacket(PACKET_FINDMYPHONE))  # without pairing
        time.sleep(1.0)
        loop.quit()

    threading.Thread(target=driver, daemon=True).start()
    GLib.timeout_add_seconds(10, loop.quit)
    loop.run()
    assert pages == [], f"unpaired findmyphone was honoured: {pages!r}"
    link_a.close()
    link_b.close()
    print("OK  unpaired findmyphone ignored")


def test_cert_mismatch_rejected():
    """The trust store must reject a different cert for a paired device."""
    store_dir = tempfile.mkdtemp()
    real = DeviceIdentity(tempfile.mkdtemp())
    impostor = DeviceIdentity(tempfile.mkdtemp())  # different key/cert
    store = TrustStore(store_dir)

    store.pin("peer", real.tls_certificate())
    assert store.matches("peer", real.tls_certificate()) is True
    assert store.matches("peer", impostor.tls_certificate()) is False
    assert store.matches("unknown-device", real.tls_certificate()) is None
    # persistence round-trip
    assert TrustStore(store_dir).is_paired("peer")
    print("OK  cert pinning rejects mismatch (MITM)")


if __name__ == "__main__":
    test_cert_mismatch_rejected()
    test_unpaired_findmyphone_ignored()
    test_pair_and_ring()
    print("\nALL TESTS PASSED")
