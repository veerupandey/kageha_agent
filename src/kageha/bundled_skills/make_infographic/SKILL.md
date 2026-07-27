---
name: make_infographic
description: Create a polished, source-grounded infographic image and verify its dimensions, hierarchy, and legibility.
triggers:
  - infographic
  - visual summary
  - one-page visual
  - social explainer
---

# make_infographic

Use for infographic, visual summary, one-page visual, or social explainer requests.

## Workflow

1. Reduce the research to one central takeaway and at most five supporting points.
2. Cite quantitative claims in a sidecar sources file; never invent numbers.
3. Create the image at the requested dimensions using an available image-generation
   tool or deterministic HTML/canvas/Pillow composition.
4. Use a clear reading order, strong contrast, generous margins, and mobile-readable type.
5. Open or inspect the exported image. Check exact dimensions, clipping, contrast,
   spelling, and whether every claim matches the research notes.
6. Revise until the checks pass.

## Required evidence

- Final PNG or JPG at the requested dimensions
- Sources mapping
- QA note with dimensions and inspection result
