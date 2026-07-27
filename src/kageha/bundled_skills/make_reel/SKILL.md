---
name: make_reel
description: Create a short vertical product reel from a product URL + Instagram/reference URL using REAL packshots (never invent packaging) and modern brand-true motion.
triggers:
  - reel
  - short video
  - vertical video
  - instagram reel
  - i2v
  - image to video
---

# make_reel

## Non-negotiable creative rules

1. **Product lock** — Never invent packaging, labels, or tin colors. Always call `import_product_images` first and use a downloaded file under `artifacts/product/` as the I2V source.
2. **No stereotype filler** — Ban default “traditional Japanese tea ceremony” tropes unless the brand/reference explicitly asks: tatami, geisha, temple, kimono, dark zen shrine, fake calligraphy logos, invented kanji.
3. **Match the reference** — Call `capture_social_reference` (ReelAI shell) or `browse_logged_in` (Comet CDP). Copy mood, pacing, and shot language from those frames (e.g. modern iced latte pour / lifestyle ASMR), not a generic ceremony.
4. **Brand truth** — Keep real product identity (name, tin, colorway). Prefer quiet-luxury / modern BC lifestyle over exoticized Japan pastiche.

## Goal

**Only when the user asked for a reel / video / I2V:** `artifacts/reel.mp4` (~4s vertical) + `reel/brief.md`.

If they asked for a carousel / multi-slide post, use `make_brand_carousel` instead — do **not** substitute an MP4.

## Tools (native harness — not skill scripts)

This skill is procedure-only. Call harness tools directly (`import_product_images`, `capture_social_reference`, `browse_logged_in`, `fal_edit_image`, `fal_image_to_video`, `bash`, `download_media`). Do **not** `skill_run` scripts under `make_reel/scripts/` — that directory does not exist.

## Steps

1. `import_product_images(product_url)` → save gallery under `artifacts/product/`.
2. Pick the best **real** still as hero source (prefer packshot / hero labeled for this SKU). Copy it to `artifacts/hero_source.jpg` via bash/`download_media` is wrong — use `bash` to `cp`.
3. Capture reference:
   - Preferred: `capture_social_reference(instagram_url)` → `artifacts/reference/`
   - If shell down / private: `browse_logged_in(instagram_url)` with Comet CDP
4. Write `reel/product.md`, `reel/reference.md` (cite real frame paths), `reel/storyboard.md` (4 beats that mirror the reference structure).
5. **Do not** call `fal_generate_image` for packaging. Optional: only for abstract B-roll that does not show the tin.
6. **Pour reels (critical):** If the reference is a pour / layered latte, the I2V seed must be a **pre-pour** still — glass with milk + ice only, **no green matcha already layered**. Use `fal_edit_image` (Kontext) on the real lifestyle packshot to remove the green layer while keeping the exact tin. Never animate a finished poured latte and pretend it is a pour.
7. `fal_image_to_video` with:
   - `image_path` = pre-pour hero (`artifacts/hero_source.jpg`) when recreating a pour
   - `prompt` = motion only: vibrant green matcha pouring onto milk and ice, then bleeding/layering. Explicitly say: keep exact product packaging, modern lifestyle, no traditional tea-ceremony set dressing.
   - `duration_seconds=4`, model `wan` or `kling`
8. Write `reel/brief.md` listing source image path, reference paths, prompts, and what was avoided.

## Verification

- `artifacts/product/` has ≥1 real downloaded image
- `artifacts/reel.mp4` non-empty
- Hero/I2V source is product-locked (real tin via import or Kontext edit of a real still — never invented packaging)
- For pour reels: hero glass starts as milk+ice only (no pre-poured green layer)
- Brief states anti-stereotype choices + pre-pour seed path
