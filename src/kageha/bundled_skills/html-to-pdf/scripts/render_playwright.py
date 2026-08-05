#!/usr/bin/env python3
"""render_playwright.py — FALLBACK: Playwright headless Chromium HTML→PDF.
Try render_chrome.sh FIRST (it's more reliable in the sandbox).
This fails when the sandbox blocks Chrome profile-dir writes.

Usage: python render_playwright.py <input.html> <output.pdf>
"""
import sys
from pathlib import Path

html_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("input.html")
pdf_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("output.pdf")

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
    )
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(f"file://{html_path.resolve()}", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(1500)  # let fonts/CSS settle
    page.pdf(
        path=str(pdf_path),
        format="A4",
        print_background=True,
        margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"},
        scale=0.82,
    )
    browser.close()

size = pdf_path.stat().st_size
print(f"OK: {pdf_path} ({size:,} bytes)")

# KNOWN FAILURE MODES (from real session — 2025-08-04):
# 1. "Executable doesn't exist at .../chrome-mac/headless_shell"
#    → Playwright browser not installed. Run: python -m playwright install chromium
#      (may fail in sandbox — use render_chrome.sh instead)
# 2. "ProcessStartupError" / crash on launch
#    → Sandbox blocking profile dir writes. Use render_chrome.sh (elevated).