---
name: android_tv
description: >
  Control Android / Google TV via adb (keys, launch apps). Fallback when Bravia
  IP control is unavailable. Requires Network debugging on the TV.
compatibility: Requires adb on PATH and KAGEHA_ANDROID_TV_HOST (or discover).
---

# android_tv

Skill-owned (no harness `android_tv_*` tools). Prefer Bravia skill when the TV
supports Sony IP control.

## Scripts

```text
skill_run android_tv scripts/discover.py
skill_run android_tv scripts/key.py --key home
skill_run android_tv scripts/launch.py --name netflix
```

Set `KAGEHA_ANDROID_TV_HOST` from discover output.
