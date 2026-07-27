---
name: generate_media
description: Provider-agnostic image/video generation (Gemini, Fal, or custom MediaProvider). Prefer this when the user does not specify a vendor.
---

# generate_media

## When to use

Image or short video generation without loading the optional native `media` pack.

## Providers

| Name | Env | Caps |
|------|-----|------|
| `gemini` | `GEMINI_API_KEY` | image |
| `fal` | `FAL_KEY` | image, t2v (+ more via optional media pack) |

Custom providers: register via `kageha.harness.media.register_provider` in a user tool pack or skill script.

## Procedure

**Image**

```text
skill_run generate_media scripts/generate.py --workspace . \
  --kind image --provider auto --prompt "…" --filename out.png
```

`--provider auto` picks gemini if keyed, else fal.

**Video (text-to-video)**

```text
skill_run generate_media scripts/generate.py --workspace . \
  --kind t2v --provider fal --prompt "…" --filename clip.mp4
```

## Optional native pack

If you need fal edit / i2v tools as first-class harness tools:

```bash
export KAGEHA_TOOL_PACKS=media
# or tools.yaml: packs: [media]
```

Still prefer this skill for default installs.
