---
name: sony_bravia
description: >
  Control a Sony Bravia TV on the same Wi‑Fi — PIN pairing, remote keys, volume,
  launch Sony LIV / Netflix / apps. Use for TV, Bravia, SonyLiv, remote, volume up/down.
compatibility: Requires LAN reachability; pair once via skill scripts or KAGEHA_BRAVIA_PSK.
fast-path-when:
  - tv_control
  - network_tvs
  - bravia
  - sony liv
  - sonyliv
  - remote
fast-path:
  pause: key:Pause
  play: key:Play
  start: key:Play
  unpause: key:Play
  resume playback: key:Play
  stop: key:Stop
  mute: key:Mute
  unmute: key:Mute
  volume up: key:VolumeUp
  vol up: key:VolumeUp
  louder: key:VolumeUp
  volume down: key:VolumeDown
  vol down: key:VolumeDown
  quieter: key:VolumeDown
  home: key:Home
  back: key:Back
  ok: key:Confirm
  enter: key:Confirm
  up: key:Up
  down: key:Down
  left: key:Left
  right: key:Right
  power off: key:PowerOff
  turn off: key:PowerOff
  power on: key:TvPower
  turn on: key:TvPower
  youtube: launch:youtube
  open youtube: launch:youtube
  netflix: launch:netflix
  open netflix: launch:netflix
  sony liv: launch:sonyliv
  sonyliv: launch:sonyliv
  open sony liv: launch:sonyliv
  prime: launch:prime
  open prime: launch:prime
  tv status: status
  bravia status: status
  what's the volume: status
  whats the volume: status
---

# sony_bravia

Skill-owned device control (no harness `bravia_*` tools). Prefer `skill_run`.

## Micro phrases

Chat fast-path handles pause / vol up / open youtube without the full loop.

## Setup

```bash
uv run kageha bravia discover
uv run kageha bravia pair
```

Or:

```text
skill_run sony_bravia scripts/pair_start.py
skill_run sony_bravia scripts/pair_finish.py --pin XXXX
```

## Scripts

| Action | Command |
|--------|---------|
| Status | `skill_run sony_bravia scripts/status.py` |
| Key | `skill_run sony_bravia scripts/key.py --key VolumeUp` |
| Launch | `skill_run sony_bravia scripts/launch.py --name sonyliv` |
| Pair start | `skill_run sony_bravia scripts/pair_start.py` |
| Pair finish | `skill_run sony_bravia scripts/pair_finish.py --pin 1234` |

LAN discovery: `skill_run network_scan scripts/scan.py --focus tv`.
ADB fallback: skill `android_tv`.
