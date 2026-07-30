---
name: nano_banana
description: Generate and edit still images with Gemini Nano Banana (first-class Kageha tools). Prefer over Fal/curl/pip for carousels, ads, and product composites.
triggers:
  - nano banana
  - generate image
  - generate images
  - create carousel
  - Instagram carousel
  - product image
  - image generation
  - edit this image
  - banana2
  - gemini image
allowed-tools: nano_banana_generate nano_banana_edit
---

# nano_banana

## When to use

Any still-image deliverable: Instagram/TikTok carousels, ads, product hero shots,
brand-consistent composites, infographics with readable text.

## Do this (not that)

1. Call `nano_banana_generate` or `nano_banana_edit` immediately.
2. Save under `artifacts/` (default filenames already do).
3. Pass product/reference shots with `reference_images` / `image_paths`.
4. **Do not** `pip install`, curl Gemini by hand, or invent a Python SDK script.
5. Prefer Nano Banana over `fal_generate_image` for stills. Use Fal for **video**.
6. Product reference shots: `download_file(url, path='artifacts/product.png')` then
   pass that path as `reference_images` — never curl into the sandbox.

## Models

| Alias | API id | When |
| --- | --- | --- |
| `banana2` (default) | `gemini-3.1-flash-image` | General / carousel workhorse |
| `lite` | `gemini-3.1-flash-lite-image` | Fast/cheap drafts |
| `pro` | `gemini-3-pro-image` | Highest fidelity / complex text |
| `legacy` | `gemini-2.5-flash-image` | Fallback |

Override default with `KAGEHA_NANO_BANANA_MODEL`.

## Carousel recipe

1. Download / extract product references → `artifacts/product_*.png`
2. For each slide: `nano_banana_generate(prompt=…, reference_images=…, aspect_ratio="4:5", filename="artifacts/slide_N.jpg")`
3. Keep brand palette, typography, and product fidelity consistent across slides
4. Report the `artifacts/slide_*.jpg` paths

## Important: file format

**Always use `.jpg` filenames** (not `.png`). The image provider only supports
JPEG output. If you pass a `.png` filename the call will fail with HTTP 400.

If a generation fails with "image/png is not supported", retry with `.jpg`.

## Aspect ratios

- Feed / carousel: `1:1` or `4:5`
- Story / Reel cover: `9:16`
- Landscape ad: `16:9`

## Carousel cohesion

When generating multi-slide carousels:
- Generate slide 1 first, then reference its style in subsequent prompts
- Include consistent brand elements in every prompt (palette, typography, product)
- Use `reference_images` with the real product photo for all slides
- After generating all slides, visually verify they share the same look

## Requirements

`GEMINI_API_KEY` (paid Gemini API key). Tools return a clear error if missing.

## Observations

- (2026-07-30) Pitfall: bash commands that use Python network libraries may fail because requests is not installed in the sandbox. Prefer first-class tools (web_fetch/browser) or use curl/python stdlib if shell access is needed.

## Refinements

### 2026-07-30

OLD: Use bash/python network libraries to inspect source pages.
NEW: For source-page inspection, prefer first-class web_fetch/browser tools. If shell access is needed, avoid Python requests; use curl or Python stdlib urllib only. This prevents failures when requests is unavailable in the sandbox.
