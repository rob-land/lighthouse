# Lighthouse — Design Document

A native GTK4/libadwaita "find my device" agent and viewer for GNOME
desktop and Linux Mobile. Makes a Phosh phone findable — ring it, locate
it, see it on a map — and gives Linux phones a native window into the
same ecosystem Android's FMD already has.

**Working name:** Lighthouse
**Suggested app ID:** `land.rob.lighthouse`
**License:** GPL-3.0-or-later
**Status:** Design phase — no scaffold yet

---

## 1. Overview

Linux phones have no "find my device". If you set a Phosh phone down
somewhere in the house, or leave it on a bus, there is no first-party
way to ring it, see where it is, or recover it — the gap is documented
in the linux-mobile-gaps inventory under *"device finding / anti-theft."*
Android has Find Hub and the FOSS world has **FMD** (Nulide's
FindMyDevice, maintained today by the fmd-foss community); iOS has Find
My. Phosh has nothing.

Lighthouse fills that gap, and the central design decision is **not to
build a from-scratch stack**. The hard parts of find-my — a
crowdsourced network, a hardened remote-command server with end-to-end
encrypted location storage, a wide installed base of viewers — already
exist as open protocols. Lighthouse is a **Phosh-native node that speaks
those protocols**, wrapped in an adaptive libadwaita UI and an
aggressive local "I'm here" takeover:

- **LAN, peer-to-peer → KDE Connect.** Implements the
  `kdeconnect.findmyphone` packet so *stock KDE Connect on an Android
  tablet, or another Lighthouse / Valent / Plasma device, rings this
  phone with zero extra software.* Serverless, instant, the
  "it's-somewhere-in-the-house" case.
- **Remote, off-LAN → FMD Server.** Acts as an **FMD Server client**:
  registers the device, uploads end-to-end-encrypted location, and
  accepts `ring` / `locate` commands. One self-hosted FMD Server now
  manages the user's Android *and* Phosh devices from the same place.
- **No internet → SMS.** A ModemManager-watched codeword wakes the
  modem and rings the phone even with Wi-Fi asleep and no data.
- **Dead / crowdsourced → Apple Find My beacon (optional).** Broadcasts
  an OpenHaystack-compatible BLE advertisement for last-seen recovery
  via Apple's network, queried through the user's own haystack server.

Lighthouse has **two roles** in one package:

1. **Agent** — makes *this* device findable across the four channels
   above, and stages the local takeover when paged.
2. **Viewer** — because it is already an FMD Server client, it pulls the
   server's device list and renders *all* the user's devices — Android
   (FMD) and Phosh (Lighthouse) — in a native GTK list. This gives Linux
   mobile something the Android FMD ecosystem itself lacks: a native
   multi-device viewer instead of a browser dashboard.

### Goals

- Native, adaptive GNOME app: FLX1s / PinePhone / Librem 5 portrait and
  docked desktop, same binary.
- Be findable **even when silenced** — ring at full volume, override the
  silent feedback profile, strobe the torch, raise brightness, vibrate.
- Be findable **even when suspended** (best effort) via the SMS tier and
  the cohort's wake-from-suspend plumbing (see `rouse`).
- Interoperate, don't reinvent: ring from *stock KDE Connect*; report to
  and be commanded by a *standard FMD Server*; appear in its dashboard
  alongside Android devices.
- A native multi-device viewer (the FMD device list, rendered in GTK).
- Privacy by construction: peer-pinned TLS on the LAN, end-to-end
  encrypted location to the FMD Server (server stores ciphertext), an
  SMS secret + sender allowlist, and a self-hostable-everything stance.

### Non-goals

- **A new protocol or a new server.** Lighthouse is a client of KDE
  Connect and FMD Server, not a competitor to either.
- **A general device-link app.** Notifications mirroring, clipboard sync,
  file transfer, media control — that is Valent / KDE Connect's job.
  Lighthouse is find-my only.
- **Lock and wipe — in v1.** FMD has them via Android's device-admin
  API; Linux mobile has no clean equivalent (it would mean scripting
  logind lock and reasoning about LUKS state), and a remotely-triggered
  wipe is a sharp tool. Deferred and gated behind explicit opt-in. See
  §6.
- **Carrier / account dependency.** No Google, no Apple ID (the Find My
  beacon path is anonymous BLE, not an account), no telemetry.

---

## 2. Architecture

### Three-process model

Following the cohort pattern (`sentry`, `rouse`, `lifeline`): a headless
agent does the always-on work, a GUI is a thin client, and a tiny
layer-shell surface draws the takeover.

| Process | Role | Lifetime |
|---|---|---|
| `lighthoused` | Agent: KDE Connect endpoint, FMD Server client, SMS watch, GeoClue loop, BlueZ beacon, command dispatch | systemd-user service, `enable-linger` |
| `lighthouse` | GUI: readiness dashboard, native device viewer, settings | launched from app grid, opt-in |
| `lighthouse-beam` | Takeover surface: full-screen "I'm here" via `gtk4-layer-shell`, raised over the lock screen | spawned by `lighthoused` per page, exits on dismiss |

The GUI being closed must never make the device unfindable — all
reachability lives in `lighthoused`. The agent exposes a private session
D-Bus interface (`land.rob.lighthouse.Agent`) the GUI consumes; the
agent raises `lighthouse-beam` directly when paged (the GUI need not be
running).

### Topology

```
                        ┌─────────────────────────────────────┐
   Android tablet ──────┤  KDE Connect  (LAN, mDNS + pinned TLS)│
   (stock KDE Connect)  │  kdeconnect.findmyphone  ⇄ peer ring  │
   other Phosh device ──┤                                       │
   (Lighthouse)         └───────────────┬───────────────────────┘
                                        │
   self-hosted        ┌─────────────────┴──────────┐   browser dashboard
   FMD Server  ───────┤  FMD client: register,     │   (any device)
   (fmd-foss)         │  E2E location upload,       │        ▲
        ▲             │  command channel (ring/     │        │ same devices,
        │ same server │  locate) via UnifiedPush    │        │ Android + Phosh
   Android phone ─────┘  + HTTP long-poll fallback  │────────┘
   (FMD app)           └─────────────────┬──────────┘
                                         │
   any phone w/ signal ─ SMS codeword ───┤ ModemManager Messaging.Added
                                         │
   passing iPhones ──── Apple Find My ───┤ BlueZ BLE advertise (optional)
                          (Haystack)     │
                                         ▼
                                   lighthoused
                              (GeoClue2 · feedbackd · logind
                               · spawns lighthouse-beam)
```

### Reach model (the spine)

A device is only "findable" if a page can actually reach it. Four
channels, ordered by how aggressively the phone is asleep:

| Tier | Channel | Reaches when | Mechanism |
|---|---|---|---|
| 1 | KDE Connect (LAN) | awake / charging / same Wi-Fi | `findmyphone` TCP packet |
| 2 | FMD Server (remote) | off-LAN, has data | command via UnifiedPush / long-poll |
| 3 | SMS codeword | suspended, no data, has signal | `Messaging.Added` wakes the SoC |
| 4 | Find My beacon | dead / no link | BLE advert, last-seen via haystack |

Plus continuous **location reporting** underneath all four: GeoClue2 fix
→ encrypted upload to FMD Server (primary sink), with optional Home
Assistant / OwnTracks / MQTT sinks for users already running those.

### FMD Server integration (the remote channel + the viewer)

This is the headline of this revision. Lighthouse implements the **FMD
app's HTTP API** against an FMD Server (`fmd-foss/fmd-server`, the
React/JS rewrite with the responsive multi-device dashboard — preferred
over the classic per-device-login Java server). Two halves:

**Agent side — register, report, obey.**

- **Registration.** On first setup, `lighthoused` registers the device
  with the configured server, producing a Device ID and access password.
  Key material derived from the password encrypts all location data
  *client-side*, so the server stores only ciphertext (the FMD
  zero-knowledge-ish scheme — the web dashboard and our own viewer
  decrypt locally with the same password).
- **Location upload.** The GeoClue loop posts encrypted positions at the
  configured interval (and on demand when a `locate` command arrives).
- **Command channel.** The server queues commands submitted from its
  dashboard. Lighthouse receives them via **UnifiedPush** where a
  distributor (e.g. ntfy) is present — the same mechanism FMD uses on
  de-Googled Android — falling back to HTTP long-poll. Supported
  commands in v1: `locate` (fresh fix → upload) and `ring` (stage the
  takeover, §2 beam). `lock` / `delete` are parsed but refused unless
  explicitly enabled (§6).

**Viewer side — see everything natively.**

Because Lighthouse already holds the server credentials and the
decryption key, the GUI's **Devices** screen pulls the server's device
list and renders every registered device — Android (FMD) and Phosh
(Lighthouse) — in a native `AdwPreferencesGroup` list with last-seen,
battery, a map thumbnail, and `Ring` / `Locate` actions. The server is
OS-agnostic: it only sees protocol clients, so a Phosh device registered
by Lighthouse is indistinguishable from an Android device registered by
FMD. This is the native multi-device viewer Linux mobile lacks.

> The two surfaces stay distinct in the UI: **LAN peers** (KDE Connect,
> serverless, instant) and **FMD devices** (remote, aggregated) are
> shown in separate groups, because their reach and latency differ.

### KDE Connect endpoint (LAN, peer-to-peer)

`lighthoused` is a full KDE Connect peer: Avahi/mDNS discovery
(`_kdeconnect._udp`, UDP 1716 announce → TLS over TCP), pinned-cert
pairing, and the `findmyphone` plugin in **both** directions —
*responder* (receive a ring request → stage the beam) and *requester*
(send a request to a paired peer). Two Phosh devices each running
Lighthouse therefore ring each other with a single pairing; stock KDE
Connect on Android joins the same web. Identity (device name, type)
comes from the KDE Connect identity packet.

### The beam — takeover surface

When paged on any tier, `lighthoused` spawns `lighthouse-beam`, a
`gtk4-layer-shell` overlay on the lock-screen layer (the same technique
`sentry-surface` uses for lock-screen widgets). It is deliberately
impossible to miss:

- **Audio that beats silent.** The "override volume" answer: rather than
  route through feedbackd (whose `silent` profile would suppress it),
  beam plays a **raw PipeWire stream** and ramps it to max; it also
  raises the default sink via `wpctl`, **saving and restoring** the
  prior level. feedbackd's profile never gated the raw stream, so the
  ring pierces silent mode exactly like an alarm. (In a tight Flatpak
  sandbox, "max my own stream" always works; "unmute a globally-muted
  sink" may need a host hole — see §6.)
- **Torch strobe** via `/sys/class/leds` where a flash LED exists
  (feature-detected; tablets without one fall back to a full-screen
  white strobe).
- **Max brightness**, **haptics** via `libfeedback`, full-screen
  "Someone's looking for this phone" with the pager's name/source and a
  full-width **I've got it** stop button.
- **A wake inhibit** (`logind` Inhibit) for the duration so the phone
  cannot re-suspend mid-ring.

### Waking a suspended phone

The genuinely hard problem. Tier 3 (SMS) is the robust path: an incoming
SMS asserts a modem wake IRQ that brings the SoC up even from
suspend-to-RAM, and `lighthoused`'s `Messaging.Added` handler matches
the configured codeword and stages the beam. For the Tier 1/2 channels,
Lighthouse reuses the cohort's wake plumbing from **`rouse`**
(systemd-user timers with `WakeSystem=true` / `rtcwake`) to schedule
periodic wake windows during which the KDE Connect / UnifiedPush
connections can be serviced. Honest limitation: a Wi-Fi-only device
(e.g. a tablet) in deep suspend with no modem cannot be woken by Tier
1/2 — keep it on a charger with suspend-while-charging disabled.

### Location, SMS, beacon

- **Location** — GeoClue2 fix acquisition reuses `beacon`'s patterns;
  sinks are pluggable (FMD Server primary; HA webhook / OwnTracks HTTP /
  MQTT optional). "Share only when paged" mode suppresses continuous
  reporting for privacy.
- **SMS trigger** — `Messaging.Added` watch reuses `lifeline` / `klaxon`
  ModemManager plumbing. A page requires **both** the shared codeword
  **and** a sender on the allowlist, rate-limited, to stop a stranger
  ringing or de-anonymising the phone.
- **Find My beacon** — optional plugin: BlueZ BLE advertisement of an
  OpenHaystack/Macless-Haystack Offline-Finding payload with key
  rotation; the user queries their own haystack + anisette server.

### IPC

`lighthoused` owns `land.rob.lighthouse.Agent` on the session bus:
methods (`PageDevice`, `LocateDevice`, `TestRing`, `ListLanPeers`,
`ListFmdDevices`, `Pair`), properties (per-tier health, location-sharing
state), signals (`Paged`, `PeerChanged`, `LocationUpdated`). The GUI and
`lighthouse-beam` are pure clients.

---

## 3. UI surfaces

Adaptive shell: `AdwApplicationWindow` + `AdwViewStack` with a bottom
`AdwViewSwitcherBar` on mobile / sidebar on wide. Blueprint `.blp`
templates → `.ui` via GResource at `/land/rob/lighthouse/`.

**Home — readiness dashboard.** Answers "*will* I be able to find this
later?": an `AdwStatusPage`-style hero (Findable / Partially / Not), a
**Reach** group with one row per tier (status dot + health detail), a
**Location** row (sink + last-update), and a `Test ring` button.

**Devices — the native viewer.** Two groups: **LAN peers** (KDE Connect,
each with Ring / Locate) and **FMD devices** (pulled from the server,
each with last-seen, battery, map thumbnail, Ring / Locate). A `+`
opens pairing (KDE Connect) or server enrolment (FMD).

**Settings.** *When found* (ring sound, override-silent, strobe torch,
max brightness, vibrate, duration); *FMD Server* (URL, credentials,
upload interval, push distributor); *Cellular trigger* (codeword,
sender allowlist); *Location* (sinks, share-only-when-paged); *Find My
beacon* (enable, key server).

**Beam (`lighthouse-beam`).** The takeover described in §2 — full-screen,
over the lock screen, strobing, one big stop button, "swipe up to
silence."

---

## 4. Phased scope

### P0 — walking skeleton (the thing actually asked for)
- `lighthoused` KDE Connect endpoint: responder **and** requester.
- `lighthouse-beam`: raw-PipeWire ring + override-silent + vibrate +
  full-screen stop. (Torch/brightness/lock-screen-layer in P2.)
- Rings from stock KDE Connect on an Android tablet, and peer-to-peer
  between two Lighthouse devices, over the LAN. Minimal GUI: a Devices
  list of LAN peers + Test ring.

### P1 — FMD integration (this revision's headline)
- FMD Server client: registration, E2E-encrypted location upload,
  command channel (UnifiedPush + long-poll) handling `ring` / `locate`.
- Native **device viewer**: render the FMD server's device list (Android
  + Phosh) with map thumbnails and Ring / Locate.
- Location sinks abstraction (FMD primary; HA/OwnTracks/MQTT optional).

### P2 — reach + polish
- SMS codeword tier (codeword + allowlist, ModemManager).
- Beam upgrades: torch strobe, max brightness, lock-screen layer-shell,
  logind inhibit.
- Wake-from-suspend integration via `rouse` plumbing.
- Readiness dashboard.

### Post-1.0
- Apple Find My / Haystack BLE beacon plugin.
- `lock` (logind session lock) behind explicit opt-in; `wipe` deferred
  pending a credible, safe Linux story.
- Beam sound packs; per-peer custom ring.

---

## 5. Tech stack

- Python 3.10+ + PyGObject; GTK 4 + libadwaita 1.6+.
- Blueprint (`.blp`) → `.ui`, bundled via GResource.
- `gtk4-layer-shell` (beam surface).
- **GeoClue2** (location), **ModemManager** (SMS), **BlueZ** (beacon),
  **systemd-logind** (inhibit + session lock), **libfeedback/feedbackd**
  (haptics + event sounds), **PipeWire / `wpctl`** (ring stream + sink
  override).
- KDE Connect: Avahi (mDNS) + TLS (GnuTLS/OpenSSL); reuse an existing
  protocol implementation if one is packagable, else the minimal
  `identity` + `findmyphone` + `ping` subset.
- FMD: HTTP client + the FMD E2E crypto scheme (AES for payloads, an
  asymmetric wrap for the key); **UnifiedPush** connector for commands.
- Meson + Ninja; packaged as a Flatpak on the GNOME 50 SDK.
- systemd-user units for `lighthoused`; `enable-linger` prompted on
  first run (as `rouse` does).

---

## 6. Risks and open questions

- **Suspend/wake reliability** is the central risk. SMS (Tier 3) is the
  only channel that reliably wakes a deep-suspended phone; Tier 1/2 need
  scheduled wake windows (`rouse` plumbing) and still can't wake a
  Wi-Fi-only device with no modem. Document the failure modes plainly;
  do not imply Find Hub parity for the suspended-tablet case.
- **Flatpak sandbox tension.** `lighthoused` needs system-bus access to
  ModemManager / GeoClue / BlueZ / feedbackd, `gtk4-layer-shell`,
  autostart, and (for full silent-override) global sink unmute. Portals
  don't cover all of this — expect generous `finish-args` or a host-side
  service split, and the usual cohort CI landmines (multi-arch wheels,
  qemu-aarch64 `OCF` binfmt, `cmake` libdir for any C bits).
- **FMD protocol surface.** Standardise on `fmd-foss/fmd-server` (JS) and
  pin to its documented API; verify the command set, the crypto scheme,
  and the registration/account model against a live server before P1 —
  the classic Java server's per-device-login model differs from the JS
  dashboard's aggregated one.
- **Audio edge.** A globally hard-muted sink may be un-overridable inside
  a tight sandbox; "max my own stream" is guaranteed, "unmute the device"
  is not. Decide whether to ship a host helper.
- **Lock/wipe.** No device-admin equivalent on Linux mobile; both are
  out of v1. If added, `wipe` must be a deliberate, well-guarded feature
  (LUKS-aware, confirmation, rate-limited) — or omitted entirely.

### Open questions
- Embed the FMD viewer (native list) **and** still point users at the
  browser dashboard, or make the native viewer authoritative?
- Reuse a third-party KDE Connect library (packaging cost) vs. a minimal
  in-house `findmyphone` implementation (maintenance cost)?
- Should `lighthouse-beam` and `rouse-fire` converge on a shared
  "fire-an-attention-surface-on-wake" helper, given they overlap?

---

## 7. Prior art and cohort siblings

**External.** KDE Connect / GSConnect / Valent (the `findmyphone`
protocol and the LAN ring); FMD — Nulide's FindMyDevice and the
fmd-foss community fork + FMD Server (the remote channel, the E2E
location model, the viewer this app borrows); OpenHaystack /
Macless-Haystack / go-haystack (the Apple Find My beacon path);
UnifiedPush (the push channel).

**Cohort.** Lighthouse is glue over plumbing the collection already has:
- **`rouse`** — wake-from-suspend via systemd `WakeSystem` timers /
  `rtcwake`, and the `rouse-fire` "play tone + vibrate + dismiss
  surface" pattern. Lighthouse's wake windows and beam reuse this.
- **`sentry`** — `gtk4-layer-shell` lock-screen surface + Phosh plugin
  glue. `lighthouse-beam` is the same technique.
- **`lifeline`** / **`beacon`** — ModemManager SMS send/receive and
  GeoClue2 fix acquisition + location-SMS. Lighthouse's SMS trigger and
  location loop reuse these patterns; `lifeline` already demonstrates a
  lock-screen actionable surface.
- **`klaxon`** — `Messaging.Added` filtering for a specific PDU class;
  the SMS-codeword watch is a sibling of its cell-broadcast watch.
