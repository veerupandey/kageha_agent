---
name: make_diagram
description: Flexible diagrams — pick Mermaid, Excalidraw-style, PlantUML/Graphviz, or an image model based on the task; render to PNG/SVG when needed.
triggers:
  - diagram
  - flowchart
  - architecture diagram
  - sequence diagram
  - erd
  - mermaid
---

# make_diagram

Use for architecture, flowcharts, sequences, ERDs, whiteboards, system design, or “make a diagram of …”.

## Pick a mode (important)

Call `choose_diagram_mode(task=...)` first, or decide with this table:

| Need | Mode | Tool |
|------|------|------|
| Precise boxes/arrows, editable source, docs | **structured** | `render_diagram` |
| Hand-drawn / whiteboard / sticky feel | **structured** `kind=excalidraw` | `render_diagram` |
| Sequence / flowchart / mindmap | **structured** `kind=mermaid` | `render_diagram` |
| UML / C4-ish text | **structured** `kind=plantuml` | `render_diagram` |
| Artistic illustration, mood, isometric art | **image_model** | `fal_generate_image` or `siliconflow_image` |
| Infographic with stats layout | Prefer skill `make_infographic` | — |

**Default:** structured Mermaid → PNG. Only use an image model when the user wants art, not accurate topology.

## Structured workflow

1. Draft source in the right language (Mermaid / Excalidraw JSON / PlantUML / DOT).
2. Optionally `write_diagram_source` to save under `diagrams/`.
3. `render_diagram(source=..., kind=auto|mermaid|excalidraw|plantuml|graphviz, format=png|svg)`.
4. Verify output path under `diagrams/` or `artifacts/`. Re-edit source and re-render if layout is messy (simplify nodes; avoid huge labels).

### Mermaid tips

- Prefer `flowchart TD` / `LR`, `sequenceDiagram`, `erDiagram`, `stateDiagram-v2`.
- Keep node labels short; link to a caption in markdown if needed.
- Save source as `diagrams/*.mmd` (auto-saved by `render_diagram`).

### Excalidraw tips

- Pass valid Excalidraw scene JSON (`type: "excalidraw"`, `elements: [...]`) when you have it.
- For “whiteboard feel” without hand-authoring JSON, you may approximate with Mermaid first, or generate a simple Excalidraw element list — then render with `kind=excalidraw`.
- Keep editable `.excalidraw.json` next to the PNG.

### Image-model tips

- Prompt: subject, layout, style, “clean diagram, labeled boxes, high contrast, no watermark”.
- Say “diagram” + style; do **not** invent fake citations or numbers.
- Save under `artifacts/`; still write a short `diagrams/caption.md` describing what it shows.

## Deliverables

- Rendered `diagrams/<name>.png` (or `.svg`) **and** source file when structured
- Optional `diagrams/caption.md` (1–3 sentences + when image_model was used, why)

## Verification

- File exists and is non-empty
- Structure matches the request (entities/edges present)
- Prefer re-render over painting over a wrong graph with an image model
