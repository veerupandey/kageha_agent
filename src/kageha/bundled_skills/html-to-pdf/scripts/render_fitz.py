#!/usr/bin/env python3
"""render_fitz.py — FALLBACK: PyMuPDF (fitz) Story engine HTML→PDF.
Pure Python, no browser launch. Good basic fidelity.
WARNING: can HANG on complex <style> blocks — always strip them first.

Usage: python render_fitz.py <input.html> <output.pdf>
"""
import re
import signal
import sys
import time
from pathlib import Path

html_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("input.html")
pdf_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("output.pdf")

raw = html_path.read_text(encoding="utf-8")

# Extract <body> and strip <style>/<script> — CRITICAL to prevent hangs
body_match = re.search(r"<body[^>]*>(.*?)</body>", raw, re.DOTALL)
body_html = body_match.group(1) if body_match else raw
body_clean = re.sub(r"<style[^>]*>.*?</style>", "", body_html, flags=re.DOTALL)
body_clean = re.sub(r"<script[^>]*>.*?</script>", "", body_clean, flags=re.DOTALL)

# Provide our own simple CSS (Story's engine is limited)
SIMPLE_CSS = """
body { font-family: Helvetica, sans-serif; font-size: 11px; }
h1 { font-size: 22px; color: #2563eb; }
h2 { font-size: 16px; color: #2563eb; border-bottom: 1px solid #ccc; }
h3 { font-size: 13px; color: #1e40af; }
p { margin: 4px 0; line-height: 1.5; }
table { width: 100%; border-collapse: collapse; margin: 8px 0; }
th { background: #2563eb; color: white; padding: 6px; font-size: 10px; }
td { padding: 5px; border: 0.5px solid #ccc; font-size: 10px; }
code { background: #f0f0f0; font-family: monospace; font-size: 10px; }
pre { background: #1a1a2e; color: #e6e6e6; padding: 10px; font-size: 9px; }
blockquote { border-left: 3px solid #2563eb; padding-left: 10px; margin: 8px 0; }
"""

# Alarm so we never hang
def _alarm(sig, frame):
    raise TimeoutError("PyMuPDF Story timed out (>25s)")

import fitz  # noqa: E402  (deliberately late: after signal handler setup)

t0 = time.time()
signal.signal(signal.SIGALRM, _alarm)
signal.alarm(25)

try:
    story = fitz.Story(html=body_clean, user_css=SIMPLE_CSS)
    page_w, page_h = 595, 842  # A4 points
    margin = 30
    content_rect = fitz.Rect(margin, margin, page_w - margin, page_h - margin)

    writer = fitz.DocumentWriter(str(pdf_path))
    more = True
    pages = 0
    while more:
        pages += 1
        dev = writer.begin_page(fitz.Rect(0, 0, page_w, page_h))
        more, _ = story.place(content_rect)
        story.draw(dev)
        writer.end_page()
    writer.close()
    signal.alarm(0)

    elapsed = time.time() - t0
    size = pdf_path.stat().st_size
    print(f"OK (fitz.Story): {pdf_path.name} - {pages} pages, {size:,} bytes, {elapsed:.1f}s")

except (TimeoutError, Exception) as e:
    signal.alarm(0)
    print(f"FAILED (fitz.Story): {type(e).__name__}: {e}", file=sys.stderr)
    print("-> Try render_chrome.sh (elevated) or render_fpdf2.py instead", file=sys.stderr)
    sys.exit(1)

# KNOWN FAILURE MODES (from real session — 2025-08-04):
# 1. HANGS indefinitely on HTML with complex inline <style> (flex/grid/dark themes)
#    -> Fix: strip <style> tags, pass SIMPLE_CSS as user_css (done above)
# 2. Low fidelity -- no gradients, limited table styling, ignores @media print
#    -> This is inherent to fitz.Story; use Chrome for pixel-perfect output