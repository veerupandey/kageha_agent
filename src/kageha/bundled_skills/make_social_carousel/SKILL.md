---
name: make_social_carousel
description: >
  Instagram / social carousel (6–7 slides typical). Research, import product
  images, recreate reference, write prompts, generate slides with Nano Banana Pro.
  Prefer for general Insta carousels; use make_brand_carousel only for Kageha
  product vocab posts.
triggers:
  - carousel
  - instagram carousel
  - social carousel
  - multi-slide
  - slide deck for instagram
allowed-tools: web_search parallel_web_search import_product_images capture_social_reference browse_logged_in gemini_generate_image skill_run skill_list_resources skill_read
---

# make_social_carousel

## Deliverable fidelity

Produce **`artifacts/carousel/slide_01.jpg` … `slide_N.jpg`** at **1080×1350** (4:5)
unless the user asks another ratio. Also write `carousel/prompts.json`,
`carousel/research.md`, `carousel/brief.md`, `carousel/judge.md`.

Do **not** invent MP4 unless asked.

## Steps (one path)

1. Clarify slide count (default **6**; match reference N when recreating), aspect
   (default **4:5**), and URLs (`product_url`, `brand_url`, `reference_url`).

2. Research when brand/product/topic is named — `web_search` /
   `parallel_web_search`.

3. Product images when a product URL exists:
   ```text
   import_product_images(product_url)  →  artifacts/product/
   ```

4. Reference recreate when recreating a social post:
   ```text
   capture_social_reference(url)  →  artifacts/reference/
   ```
   Fallback: `browser_connect(target="comet")` + screenshots.

5. Prompt writer (skill script — do not hand-wave thin prompts):
   ```text
   skill_run make_social_carousel scripts/write_prompts.py \
     --workspace . \
     --instruction "<brief + research notes>" \
     --slide-count N \
     --aspect 4:5 \
     --product-dir artifacts/product \
     --reference-dir artifacts/reference \
     --product-url … --brand-url … --reference-url …
   ```
   Confirm `carousel/prompts.json` exists.

6. Generate each slide (skill script — product + format gate):
   ```text
   skill_run make_social_carousel scripts/generate_slide.py \
     --workspace . --slide N
   ```
   → `artifacts/carousel/slide_NN.jpg` (Nano Banana Pro). Do **not** call
   raw `gemini_generate_image` for carousel slides.

7. Optional local helpers:
   ```text
   skill_run make_social_carousel scripts/compose_slide.py --workspace . --slide 1 --bg artifacts/carousel/_raw_slide_01.jpg
   skill_run make_social_carousel scripts/qa_carousel.py --workspace . --expect-slides N --product-lock
   ```

8. Write `carousel/brief.md` + `carousel/judge.md`. Lead the final reply with
   slide paths.

## Story lock

| Slide | Role |
|-------|------|
| 1 | Hook |
| 2…N−1 | Value |
| N | CTA |

## Verification

- `artifacts/carousel/slide_*.jpg` count == N
- `carousel/prompts.json` + `brief.md` + `judge.md` present
- Final summary leads with slide paths
