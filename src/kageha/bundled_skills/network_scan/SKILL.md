---
name: network_scan
description: >
  Scan the local Wi‑Fi subnet for devices/services (TV, ADB, SSH, HTTP, printers).
  Read-only discovery — does not control devices.
triggers:
  - network scan
  - scan wifi
  - scan lan
  - devices on
  - discover devices
  - subnet
---

# network_scan

Skill-owned (no harness `network_scan` tools).

```text
skill_run network_scan scripts/scan.py --focus all
skill_run network_scan scripts/scan.py --focus tv
skill_run network_scan scripts/scan.py --focus quick
```

After finding a Bravia, use skill `sony_bravia`. For ADB TVs, use `android_tv`.
