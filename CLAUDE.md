# Lighthouse — CLAUDE.md

## What this project is

Lighthouse is the "find my device" agent and viewer Linux phones lack.
It makes a Phosh device findable across four reach tiers — KDE Connect on
the LAN (rings from stock KDE Connect / Valent / Plasma), an FMD Server
client for off-LAN ring/locate with end-to-end-encrypted location, an
SMS codeword that wakes the modem with no data, and an optional
OpenHaystack BLE beacon — and it renders the FMD device list natively so
Android and Phosh devices appear in one GTK viewer. The thesis is to be a
node speaking existing protocols, not a from-scratch stack. Three
processes: `lighthoused` (agent, systemd-user), `lighthouse` (GUI),
`lighthouse-beam` (a `gtk4-layer-shell` takeover that rings past silent
mode). See `DESIGN.md` for the architecture and `TODO.md` for the phased
backlog.

App ID: `land.rob.lighthouse`. License: GPL-3.0-or-later.

## Code quality

A core goal is well-structured, readable code that follows idiomatic Python (PEP 8) and GNOME / libadwaita conventions; the cohort-shared [`STYLE_GUIDE.md`](STYLE_GUIDE.md) layers on top. When existing code doesn't meet that bar, refactor rather than perpetuate the pattern.

## Before making changes

Read [`STYLE_GUIDE.md`](STYLE_GUIDE.md) first when touching any of:

- Meson build files, the Flatpak manifest, or `requirements.txt`
- Anything under `data/ui/` or `data/icons/`
- New top-level Python files, or new modules under `src/<pkg>/`
- Imports — especially `import gi` / `gi.require_version`
- New launcher / `.in` substitution targets
- Toasts, dialogs, headerbars, menus, CSS classes, icon picks — all
  the suite UI conventions in §Suite UI conventions

The cohort under `~/projects/mobile-linux/developed/` (banter,
clicker, coffer, couch, homie, jamjar, keepsake, patch, roam, rota,
shoebox, stacks, tock, tonic, …) is maintained as a single suite,
and the conventions drift easily from intuition. A `Stop` hook in
`.claude/settings.json` runs `~/projects/style-check.py` and will
surface violations back at the end of each turn.

## Tech stack

- **Language**: Python 3.10+
- **UI toolkit**: GTK4 + libadwaita (PyGObject), Blueprint (`.blp`)
  templates compiled to `.ui` at build time and bundled via GResource
- **Build system**: Meson + Ninja
- **Packaging**: Flatpak (manifest:
  `build-aux/flatpak/land.rob.lighthouse.json`), GNOME 50 SDK

## Source layout

```
meson.build                     Root build (APP_ID, conf, py_conf, subdirs)
build-aux/flatpak/
  land.rob.lighthouse.json          Flatpak manifest
build-all.sh                    Multi-arch flatpak driver
fix-flatpak-deps.py             Tarball -> wheel patcher
requirements.txt                Python runtime deps
data/
  meson.build
  land.rob.lighthouse.{desktop,metainfo.xml,gschema.xml}*.in
  icons/hicolor/{scalable,symbolic}/apps/land.rob.lighthouse*.svg
  ui/
    meson.build                 blueprint-compiler + gnome.compile_resources
    land.rob.lighthouse.gresource.xml
    *.blp                       Blueprint UI templates
po/
  LINGUAS, POTFILES.in, meson.build
src/lighthouse/
  meson.build
  lighthouse.in                     Launcher (Meson-substituted)
  const.py.in                   Build-time constants
  __init__.py, __main__.py, main.py, window.py
```

## Key conventions

- See [`STYLE_GUIDE.md`](STYLE_GUIDE.md) for the full cross-project
  convention reference (this is a sibling of banter, clicker, finlit,
  jamjar, and tonic).
- UI lives in `data/ui/*.blp`. Don't introduce inline `Gtk.Builder.new_from_string(...)`
  for new templates — author a `.blp`, register it in
  `data/ui/land.rob.lighthouse.gresource.xml`, and use
  `@Gtk.Template(resource_path="/land/rob/lighthouse/ui/<name>.ui")`.
- `gi.require_version` is declared once in `src/lighthouse/lighthouse.in`;
  sub-modules just `from gi.repository import …`.
