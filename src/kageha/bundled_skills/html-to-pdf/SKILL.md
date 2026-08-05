---
name: html-to-pdf
description: Convert HTML files to beautiful styled PDFs. Tries elevated Chrome headless (--print-to-pdf) first because it preserves all CSS, then falls back through Playwright, PyMuPDF Story, and fpdf2. Use when the user asks to convert HTML to PDF, export a document as PDF, make a PDF from an HTML file, or when a previous HTML→PDF attempt failed with sandbox/permission errors.
license: MIT
---

# HTML → PDF Conversion

Convert a standalone HTML file (with inline `<style>`) into a faithful, styled PDF. Preserves gradients, tables, code blocks, colors, and page layout.

## CRITICAL: The escalation ladder (try in order)

PDF generation in the Kageha sandbox fails in specific, predictable ways. Always escalate through this ladder — do NOT start at the bottom.

```
1. Elevated Chrome headless --print-to-pdf   ← TRY FIRST (preserves ALL CSS)
2. Playwright sync_playwright page.pdf()      ← needs install; may fail on profile dir
3. PyMuPDF (fitz) Story engine                 ← pure Python; good fidelity; can hang
4. fpdf2 write_html()                          ← last resort; loses most styling
5. Plain text dump                             ← absolute fallback
```

**Why elevated Chrome first?** The default sandbox blocks writes to the Chrome user-data-dir / profile directory, which causes Playwright and in-process Chromium to crash on launch. Running Chrome via `bash(elevated=True)` with a `--user-data-dir` pointed at a writable temp folder bypasses this entirely and produces pixel-perfect output matching the screen render.

## Step 1 — PREFERRED: Elevated Chrome headless (run this first)

```bash
CHROME=$(ls /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome 2>/dev/null || which chromium || which google-chrome)
TMPDIR_PDF=$(mktemp -d)
"$CHROME" --headless --disable-gpu --no-sandbox \
  --user-data-dir="$TMPDIR_PDF/profile" \
  --print-to-pdf="$KAGEHA_ARTIFACTS/output.pdf" \
  --print-to-pdf-no-header \
  --no-pdf-header-footer \
  "file://$KAGEHA_ARTIFACTS/input.html"
```

- Run this via `bash(elevated=True)` — it WILL prompt the user for approval because it's a host escape. That's expected and necessary.
- Must pass `--user-data-dir` pointing at a fresh mktemp directory. Without it, Chrome tries to write to the default profile path which the sandbox blocks.
- Must pass `file://` absolute path to the HTML.
- Validates: `head -c 5 output.pdf` should print `%PDF-`.

See `scripts/render_chrome.sh` for a self-contained, reusable version.

## Step 2 — FALLBACK: Playwright (only if Chrome binary unavailable)

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
    page = browser.new_context().new_page()
    page.goto(f"file://{HTML_PATH}", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(1500)
    page.pdf(path=PDF_PATH, format="A4", print_background=True,
             margin={"top":"12mm","bottom":"12mm","left":"10mm","right":"10mm"}, scale=0.82)
    browser.close()
```

**Known failure mode:** Crashes with `Executable doesn't exist at .../chrome-mac/headless_shell` or `ProcessStartupError` when the sandbox blocks profile-dir writes. If you see this, jump to Step 1 (elevated Chrome).

See `scripts/render_playwright.py`.

## Step 3 — FALLBACK: PyMuPDF Story (pure Python, no browser)

```python
import fitz
story = fitz.Story(html=body_html, user_css=SIMPLE_CSS)  # strip <style>/<script> first
writer = fitz.DocumentWriter(str(PDF_OUT))
more = True
while more:
    dev = writer.begin_page(fitz.Rect(0, 0, 595, 842))
    more, _ = story.place(fitz.Rect(28, 28, 567, 814))
    story.draw(dev)
    writer.end_page()
writer.close()
```

**Known failure modes:**
- **HANGS** if the HTML contains complex/nested `<style>` blocks or certain flex/grid CSS. Always strip `<style>` and `<script>` tags and pass a `user_css` string instead. Wrap in `signal.alarm(25)` so it can't hang forever.
- Renders only basic CSS — no gradients, limited table styling, no `@media print`.

See `scripts/render_fitz.py`.

## Step 4 — LAST RESORT: fpdf2

```python
from fpdf import FPDF, HTMLMixin
class P(FPDF, HTMLMixin): pass
pdf = P(format="A4"); pdf.add_page()
pdf.write_html(body_html)  # very basic HTML support
pdf.output(PDF_PATH)
```

Loses almost all styling (colors, gradients, code highlighting). Use only when nothing else works and the user just needs *a* PDF.

See `scripts/render_fpdf2.py`.

## Verification (always run after generating)

```bash
head -c 5 "$KAGEHA_ARTIFACTS/output.pdf"   # must print: %PDF-
python3 -c "import fitz; d=fitz.open('$KAGEHA_ARTIFACTS/output.pdf'); print(f'Pages: {d.page_count}, Page1: {d[0].get_text()[:80]}')"
```

## Failure log

See `references/failures.md` for the documented failure modes encountered in the real session that produced this skill — including exact error messages and root causes. Read this BEFORE debugging a failed PDF render; the answer is likely already there.

## Quick decision table

| Situation | Use |
|:---|:---|
| Chrome installed on host | **Step 1** (elevated Chrome) — always |
| No Chrome, Playwright installed | Step 2 (Playwright) |
| No browser at all, PyMuPDF installed | Step 3 (fitz Story) |
| Nothing else works | Step 4 (fpdf2) |
| `uv pip install` fails with cache errors | Don't retry pip — jump to elevated Chrome (Step 1) |
