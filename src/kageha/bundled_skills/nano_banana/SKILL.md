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
allowed-tools: nano_banana_generate nano_banana_edit download_file web_search web_fetch research_run
---

# nano_banana

## When to use

Any still-image deliverable: Instagram/TikTok carousels, ads, product hero shots,
brand-consistent composites, infographics with readable text.

## CRITICAL RULES (never violate)

1. **NEVER use PIL/Pillow/ImageDraw to create final deliverable slides.** PIL is only acceptable for inspecting image dimensions. All image creation MUST go through `nano_banana_generate` or `nano_banana_edit`.
2. **NEVER invent or hallucinate logos.** If you don't have the actual brand logo file downloaded and verified, omit it entirely.
3. **ALWAYS research the brand first** when a specific brand/account is referenced. Use `research_run` or `web_fetch` on their website/Instagram before generating.
4. **ALWAYS download real product images** when the task says "use product image X". Call `download_file(url, path='artifacts/carousel/product.jpg')` THEN pass that path as `reference_images`.
5. **NEVER claim brand research is done** without actual web_fetch/research_run tool evidence showing you visited the brand's pages.

## Carousel workflow (follow this exactly)

### Step 1: Research the brand
```
research_run(query="<brand website/instagram> visual identity colors typography")
web_fetch(url="<brand product page>")   # extract product image URLs
```

### Step 2: Download the real product image
```
download_file(url="<product image CDN URL>", path="artifacts/carousel/product.jpg")
```
Verify the file exists and is a valid image (>10KB).

### Step 3: Generate each slide with nano_banana_generate
```
nano_banana_generate(
    prompt="<detailed slide description including brand colors, typography style, layout>",
    reference_images="artifacts/carousel/product.jpg",
    aspect_ratio="4:5",
    filename="artifacts/carousel/slide_1.png",
    image_size="1K"
)
```
- For slides WITHOUT product (e.g. text-only lifestyle): omit reference_images
- For slides WITH product: ALWAYS include reference_images pointing to the downloaded product photo
- Use aspect_ratio="4:5" for Instagram carousel (1080x1350)

### Step 4: Verify outputs
Check that all slide files exist and are >50KB (real images, not placeholders).

## Models

| Alias | API id | When |
| --- | --- | --- |
| `banana2` (default) | `gemini-3.1-flash-image` | General / carousel workhorse |
| `lite` | `gemini-3.1-flash-lite-image` | Fast/cheap drafts |
| `pro` | `gemini-3-pro-image` | Highest fidelity / complex text |
| `legacy` | `gemini-2.5-flash-image` | Fallback |

Override default with `KAGEHA_NANO_BANANA_MODEL`.

## Aspect ratios

- Feed / carousel: `1:1` or `4:5`
- Story / Reel cover: `9:16`
- Landscape ad: `16:9`

## Common mistakes to avoid

- ❌ Writing Python PIL scripts to draw rectangles and paste images
- ❌ Using `bash` with `python -c "from PIL import Image..."` for slide creation
- ❌ Inventing brand logos that don't exist
- ❌ Skipping brand research and generating generic imagery
- ❌ Claiming brand palette without actually fetching the brand's pages
- ❌ Using `reference_images` with a path that doesn't exist yet

- ✅ Call nano_banana_generate directly with detailed prompts
- ✅ Download product images FIRST, then reference them
- ✅ Include brand colors/style in the prompt text
- ✅ Leave logo off if you don't have the verified asset
- ✅ Use parallel nano_banana_generate calls for multiple slides

## Requirements

`GEMINI_API_KEY` (paid Gemini API key). Tools return a clear error if missing.
