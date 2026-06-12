# Lighthouse

The "find my device" agent and viewer that Linux phones don't have. A
native GTK4/libadwaita app for GNOME and Phosh that makes a phone
findable — ring it, locate it, see it on a map — and gives Linux mobile
a native window into the same find-my ecosystem Android's FMD already
uses.

Lighthouse doesn't reinvent the stack; it's a node that speaks existing
protocols across four reach tiers:

- **LAN (KDE Connect)** — implements the `kdeconnect.findmyphone` packet,
  so *stock KDE Connect on an Android tablet, or another Lighthouse /
  Valent / Plasma device, rings this phone* with no extra software.
  Serverless, instant — the "it's somewhere in the house" case.
- **Remote (FMD Server)** — registers with a self-hosted FMD Server,
  uploads end-to-end-encrypted location, and accepts ring / locate
  commands. One server then manages your Android *and* Phosh devices,
  and Lighthouse renders that device list natively — the native
  multi-device viewer the Android FMD ecosystem itself lacks.
- **No data (SMS)** — a ModemManager-watched codeword wakes the modem
  and rings the phone even with Wi-Fi asleep and no internet path.
- **Dead / crowdsourced (optional)** — an OpenHaystack-compatible BLE
  beacon for last-seen recovery via Apple's Find My network.

It also rings *loud*: a raw PipeWire stream that pierces the silent
feedback profile like an alarm, a torch strobe, max brightness, and a
full-screen lock-screen takeover.

See [`DESIGN.md`](DESIGN.md) for the architecture and [`TODO.md`](TODO.md)
for the phased backlog.

> **Deployment note:** the always-on agent `lighthoused` runs as a
> systemd-user service and needs `loginctl enable-linger` so it keeps
> listening when the GUI is closed and across logouts. Lighthouse
> prompts on first run if linger isn't enabled (same requirement as
> the sibling `rouse`).

## Tech stack

- Python 3.10+ + PyGObject
- GTK 4 + libadwaita; Blueprint (`.blp`) UI templates compiled to `.ui`
  and bundled via GResource
- `gtk4-layer-shell` (takeover surface), GeoClue2 (location),
  ModemManager (SMS), BlueZ (beacon), systemd-logind (wake inhibit),
  libfeedback/feedbackd + PipeWire (ring), UnifiedPush (FMD commands)
- Meson + Ninja, packaged as a Flatpak on the GNOME 50 SDK

## Running locally (host install)

```sh
meson setup _build --prefix="$PWD/_install"
meson install -C _build

PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHONPATH="$PWD/_install/lib/python$PYVER/site-packages" \
GSETTINGS_SCHEMA_DIR="$PWD/_install/share/glib-2.0/schemas" \
XDG_DATA_DIRS="$PWD/_install/share:${XDG_DATA_DIRS:-/usr/share}" \
"$PWD/_install/bin/lighthouse"
```

## Running as a Flatpak

```sh
flatpak install --user flathub org.gnome.Platform//50 org.gnome.Sdk//50

./build-all.sh                  # both arches
./build-all.sh --arch x86_64    # single arch
./build-all.sh --regen-deps     # regenerate python3-deps.json from requirements.txt
./build-all.sh --install        # also installs the host-arch bundle (--user)
```

The first invocation auto-regenerates `build-aux/flatpak/python3-deps.json`
from `requirements.txt` if the file is missing.

## License

GPL-3.0-or-later. See `COPYING`.
