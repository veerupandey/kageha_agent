#!/usr/bin/env python3
"""render_fpdf2.py — LAST RESORT: fpdf2 HTML renderer.
Loses almost all styling (colors, gradients, code highlighting).
Use ONLY when Chrome, Playwright, and PyMuPDF all fail.

Usage: python render_fpdf2.py <input.html> <output.pdf>
"""
import html as htmlmod
import re
import sys
from pathlib import Path
from fpdf import FPDF, HTMLMixin

html_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("input.html")
pdf_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("output.pdf")

raw = html_path.read_text(encoding="utf-8")

# Extract <body>
body_match = re.search(r"<body[^>]*>(.*?)</body>", raw, re.DOTALL)
body_html = body_match.group(1) if body_match else raw

# Strip style/script
body_clean = re.sub(r"<style[^>]*>.*?</style>", "", body_html, flags=re.DOTALL)
body_clean = re.sub(r"<script[^>]*>.*?</script>", "", body_clean, flags=re.DOTALL)
# Remove class-based divs
simple_body = re.sub(r'<div[^>]*class="[^"]*"[^>]*>', '<div>', body_clean)


class AgentPDF(FPDF, HTMLMixin):
    def header(self):
        pass

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


pdf = AgentPDF(orientation="P", unit="mm", format="A4")
pdf.set_auto_page_break(auto=True, margin=20)
pdf.add_page()
pdf.set_font("Helvetica", size=10)

try:
    pdf.write_html(simple_body)
except Exception as e:
    print(f"fpdf2 HTML parse failed: {e}", file=sys.stderr)
    # Absolute last resort: dump plain text
    text = re.sub(r"<[^>]+>", " ", body_clean)
    text = htmlmod.unescape(text)
    for line in text.split("\n"):
        line = line.strip()
        if line:
            pdf.multi_cell(0, 5, line)

pdf.output(str(pdf_path))
size = pdf_path.stat().st_size
print(f"OK (fpdf2): {pdf_path.name} ({size:,} bytes)")