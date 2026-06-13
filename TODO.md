# Lighthouse TODO

Phased backlog mapping onto [`DESIGN.md`](DESIGN.md) §4. **P0** is "ring
this phone from stock KDE Connect on another device"; **P1** adds the FMD
Server client + native viewer; **P2** fills out the remaining reach tiers
and hardens the takeover.

The three processes (DESIGN §2): `lighthoused` (agent, systemd-user),
`lighthouse` (GUI), `lighthouse-beam` (layer-shell takeover surface).

---

## P0 — walking skeleton (ring it on the LAN)

> **Status — P0 first draft (2026-06-12).** Implemented and tested:
> agent daemon (`agent.py`); `land.rob.lighthouse.Agent` D-Bus surface
> (all methods respond; TestRing stages the beam); the beam ring
> (`beam.py`: Gst tone + `wpctl` sink save/raise/restore + guarded `Lfb`
> haptics + fullscreen stop); GUI Test Ring + device viewer (`window.py`);
> KDE Connect discovery + a **mutually-authenticated TLS link with
> certificate pinning** (`kdeconnect.py` + `trust.py`); systemd +
> D-Bus-activation units. Single `lighthouse` binary dispatches the
> gui/agent/beam roles (`cli.py`). The link is covered by an integration
> test (`tests/test_link.py`, wired into `meson test`) validating
> pair→pin→ring, unpaired-ring-ignored, and MITM-cert-rejected between two
> instances over loopback — all green.
>
> **Remaining for P0:** (a) validate interop against a real KDE Connect /
> Valent peer (two devices); (b) run the beam on an actual display/Phosh
> session. Both need hardware this was developed without.
>
> 🟢 **Security status.** The original `CERT_NONE`/no-pinning gap is
> **closed** and a **pairing-confirm prompt** now gates which peers get
> pinned: a peer-initiated pair request is held pending (30s timeout),
> surfaced via the `PairRequested` D-Bus signal + a desktop notification,
> and confirmed in the GUI (`RespondPairing`) before any cert is pinned.
> Links use mutual TLS, pin the peer cert on confirmed pairing, refuse a
> mismatched cert on reconnect (MITM-rejected), and honour `findmyphone`
> only from a paired, cert-verified peer. All covered by `meson test`
> (confirm→pair→ring, reject-no-pin, unpaired-ignored, MITM-rejected).

### 1. `lighthoused` — agent daemon skeleton ✅ (first draft)
A GLib main-loop daemon launched by a systemd-user unit. Owns transport
plugins and the command dispatcher. No GUI dependency. Logs via the
cohort `logging_setup.py`.

### 2. `kdeconnect.py` — minimal protocol endpoint
Avahi/mDNS announce + discovery (`_kdeconnect._udp`, UDP 1716), TLS over
TCP with pinned-cert pairing, and the `identity` + `ping` +
`findmyphone` packets only. **Responder** (incoming `findmyphone` →
`dispatch_page()`) and **requester** (`ring_peer(device_id)`). Decide
build-vs-reuse (see DESIGN §6 open question) — start with the minimal
in-house subset.

### 3. `lighthouse-beam` — takeover surface (audio-first)
Separate binary spawned by `lighthoused` per page. P0 scope: full-screen
`AdwWindow` (not yet layer-shell), a **raw PipeWire ring stream** ramped
to max + `wpctl` sink save/raise/restore (the silent-override mechanism),
`libfeedback` haptics, and a full-width **I've got it** stop button.
Torch / brightness / lock-screen layer come in P2.

### 4. Agent D-Bus interface (`land.rob.lighthouse.Agent`)
Methods `PageDevice`, `TestRing`, `ListLanPeers`, `RingPeer`, `Pair`;
signals `Paged`, `PeerChanged`. GUI and beam are pure clients.

### 5. Minimal GUI
`AdwApplicationWindow` with a single **Devices** group listing LAN peers
(name, online dot, Ring action) + a **Test ring** button. Blueprint
`window.blp`.

### 6. systemd-user unit + linger
Ship `land.rob.lighthouse.service`; prompt for `loginctl enable-linger`
on first run if absent (crib from `rouse`).

---

## P1 — FMD integration (the headline)

### 1. `fmd.py` — FMD Server client
Implement the FMD app HTTP API against `fmd-foss/fmd-server`: device
registration (Device ID + access password), enrolment UI, credential
storage in the secret store.

### 2. FMD end-to-end crypto
Client-side encryption of location payloads (server stores ciphertext);
the password-derived key both encrypts our uploads and decrypts other
devices' positions for the viewer. Verify scheme against a live server.

### 3. Command channel
Receive `ring` / `locate` commands via **UnifiedPush** (ntfy distributor)
with an **HTTP long-poll fallback**. Parse but refuse `lock` / `delete`
unless explicitly enabled (DESIGN §6).

### 4. Location sink abstraction
GeoClue2 fix loop (reuse `beacon`'s patterns) → pluggable sinks: FMD
Server (primary), plus optional Home Assistant webhook / OwnTracks HTTP /
MQTT. "Share only when paged" toggle.

### 5. Native device viewer
GUI **Devices** screen pulls the FMD server device list and renders all
devices (Android + Phosh) in a second group with last-seen, battery, map
thumbnail, Ring / Locate — kept visually distinct from the LAN-peers
group.

---

## P2 — reach + polish

### 1. SMS codeword tier
`Messaging.Added` watch (reuse `lifeline` / `klaxon` ModemManager
plumbing). Page requires **both** the codeword **and** an allowlisted
sender; rate-limited.

### 2. Beam hardening
Promote the takeover to `gtk4-layer-shell` over the lock screen (reuse
`sentry-surface` technique); add torch strobe (`/sys/class/leds`,
feature-detected), max brightness, and a `logind` wake inhibit for the
ring duration.

### 3. Wake-from-suspend
Schedule wake windows via `rouse`'s systemd `WakeSystem` / `rtcwake`
plumbing so Tier 1/2 connections get serviced from suspend. Document the
Wi-Fi-only-tablet limitation.

### 4. Readiness dashboard
Home screen: Findable / Partial / Not hero, per-tier health rows,
location-sharing status, Test ring.

---

## Post-1.0
- Apple Find My / Haystack BLE beacon plugin (BlueZ advertise + key
  rotation; user-run haystack + anisette server).
- `lock` (logind session lock) behind explicit opt-in; `wipe` deferred
  pending a safe LUKS-aware story.
- Per-peer custom ring; beam sound packs.
- Evaluate converging `lighthouse-beam` and `rouse-fire` on a shared
  "attention-surface-on-wake" helper.

---

## Housekeeping (carried from the scaffold)
- Fill `data/land.rob.lighthouse.metainfo.xml.in` `<summary>` /
  `<description>` (still the placeholder "A native GNOME application").
- Fill the `.desktop.in` `Comment=`.
- Replace the placeholder app icon with a real Adwaita-style asset.
- Rename note: scaffold output already uses lowercase `land.rob.lighthouse`
  throughout — no casing fix needed (unlike older siblings).
