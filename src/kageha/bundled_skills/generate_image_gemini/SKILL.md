---
name: generate_image_gemini
description: Generate still images with Gemini (Nano Banana Pro family). Prefer this over loading the optional media tool pack.
---

# generate_image_gemini

## When to use

User wants an image and `GEMINI_API_KEY` is available. Works without `KAGEHA_TOOL_PACKS=media`.

## Procedure

1. Confirm a short prompt + aspect ratio (default `4:5` for social, else `1:1`).
2. Run:

```text
skill_run generate_image_gemini scripts/generate.py --workspace . \
  --prompt "…" --aspect-ratio 4:5 --filename my.png
```

3. Report the artifact path under `artifacts/`.

## Models

- `nano-banana-pro` (default) → gemini-3-pro-image
- `nano-banana-2` → gemini-3.1-flash-image
- `nano-banana` → gemini-2.5-flash-image

## Notes

- Network spend may require HITL on `skill_run`.
- For provider-agnostic image/video, use skill `generate_media`.
