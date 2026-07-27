---
name: make_brand_carousel
description: >
  Kageha-product Instagram vocab/tool carousel only (cream cards + line art).
  For general Instagram carousels, use make_social_carousel.
triggers:
  - brand carousel
  - kageha carousel
  - vocab carousel
  - product vocab
allowed-tools: import_product_images capture_social_reference gemini_generate_image skill_run skill_load skill_list_resources web_search
---

# make_brand_carousel

## Deliverable fidelity

Produce **`artifacts/carousel/slide_01.jpg` … `slide_N.jpg`** at **1080×1350**.
Do **not** invent MP4 unless asked. Lead the final summary with JPG paths.

## Non-negotiable brand rules (Kageha)

1. Brand wordmark is exactly `Kageha` (never Kau / invented kanji).
2. Product truth — `import_product_images` first; never invent tin artwork.
3. No geisha/temple/kimono/fake calligraphy.
4. Match reference structure when recreating (cream/navy/forest, centered).
5. Same canvas, margins, type scale across slides.

## Steps (one path — shared social-carousel scripts)

1. `import_product_images(product_url)` → `artifacts/product/`
2. `capture_social_reference(instagram_url)` → `artifacts/reference/` when recreating
3. Prompt writer via **make_social_carousel** scripts:
   ```text
   skill_run make_social_carousel scripts/write_prompts.py \
     --workspace . --instruction "<Kageha brief>" --slide-count N \
     --aspect 4:5 --product-dir artifacts/product \
     --reference-dir artifacts/reference \
     --product-url … --reference-url …
   ```
4. Generate plates:
   ```text
   skill_run make_social_carousel scripts/generate_slide.py --workspace . --slide N
   ```
5. Compose final brand typography with Pillow/HTML if AI type is messy
   (`skill_run make_social_carousel scripts/compose_slide.py …` or local code).
   Cover: dry matcha powder full-bleed + white vocab type; tool cards: line-art
   on cream with exact `Kageha` header.
6. Optional QA: `skill_run make_social_carousel scripts/qa_carousel.py --workspace . --product-lock`
7. `carousel/brief.md` + `carousel/judge.md`

## Verification

- N JPGs at 1080×1350; brand reads `Kageha`; no unsolicited MP4
