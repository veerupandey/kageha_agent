---
name: pdf_ingest
description: Extract text from a PDF with pdf_extract, summarize claims, optionally ingest into a KB. Deliverable is text/summary — not video or carousel.
allowed-tools: pdf_meta pdf_extract bash
---

# pdf_ingest

## Deliverable fidelity

Produce **`pdf/extract.txt`** + **`pdf/summary.md`**. Do not invent MP4/carousel outputs unless asked.

## Steps

1. Locate the PDF path (session workspace or absolute path).
2. Call `pdf_meta(path)` then `pdf_extract(path)` (optional `max_pages` for huge docs).
   - Requires `uv sync --extra pdf` or host `pdftotext`.
   - Do **not** forge a PDF tool — use the first-class tools.
3. Confirm `pdf/extract.txt` is non-empty.
4. Write a structured summary to `pdf/summary.md`:
   - One-line document purpose
   - Key claims as bullets (with page hints when present)
   - Open questions / gaps
5. Optionally create/attach a KB (`kb` CLI or tools) with engine `zvec` from the extract for later search.

## Verification

- `pdf/extract.txt` is non-empty
- `pdf/summary.md` lists claims
- Final summary leads with those two paths
