"""Persistent trust-on-first-use store for paired peers.

A device counts as *paired* exactly when we have pinned its certificate.
On every subsequent link the peer must present byte-for-byte the same
certificate; a mismatch is treated as an attack and the link is refused.
This is what turns the encrypted-but-anonymous TLS channel into an
authenticated one — closing the MITM gap the first draft had.
"""

from __future__ import annotations

import json
import logging
import os

from lighthouse.protocol import config_dir

log = logging.getLogger(__name__)


class TrustStore:
    def __init__(self, base_dir: str | None = None) -> None:
        self._path = os.path.join(config_dir(base_dir), "trusted_devices.json")
        self._certs: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        try:
            with open(self._path, encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, ValueError):
            return {}

    def _save(self) -> None:
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._certs, fh, indent=2)
        os.replace(tmp, self._path)

    def is_paired(self, device_id: str) -> bool:
        return device_id in self._certs

    def paired_ids(self) -> list[str]:
        return list(self._certs)

    def pin(self, device_id: str, cert) -> None:
        """Pin a peer's Gio.TlsCertificate (stored as PEM)."""
        self._certs[device_id] = cert.props.certificate_pem
        self._save()
        log.info("pinned certificate for %s", device_id)

    def unpin(self, device_id: str) -> None:
        if self._certs.pop(device_id, None) is not None:
            self._save()
            log.info("unpinned %s", device_id)

    def matches(self, device_id: str, presented) -> "bool | None":
        """Compare a presented Gio.TlsCertificate against the pinned one.

        Returns None if the device is not paired, True/False if it is.
        """
        pem = self._certs.get(device_id)
        if pem is None:
            return None
        if presented is None:
            return False
        from gi.repository import Gio
        stored = Gio.TlsCertificate.new_from_pem(pem, -1)
        return stored.is_same(presented)
