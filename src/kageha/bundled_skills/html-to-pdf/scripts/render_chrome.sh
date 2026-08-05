#!/usr/bin/env bash
# render_chrome.sh — PREFERRED HTML→PDF renderer (elevated Chrome headless)
# Usage: bash render_chrome.sh <input.html> <output.pdf>
# Must be run via bash(elevated=True) — it escapes the sandbox.
# Why elevated: the Kageha default sandbox blocks writes to Chrome's profile dir,
# which crashes in-process Chromium/Playwright. A fresh --user-data-dir fixes it.
set -euo pipefail

HTML_IN="${1:-$KAGEHA_ARTIFACTS/input.html}"
PDF_OUT="${2:-$KAGEHA_ARTIFACTS/output.pdf}"

# Locate Chrome
CHROME=""
for c in \
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  "/Applications/Chromium.app/Contents/MacOS/Chromium" \
  "$(command -v google-chrome 2>/dev/null)" \
  "$(command -v chromium 2>/dev/null)"; do
  if [ -x "$c" ]; then CHROME="$c"; break; fi
done
if [ -z "$CHROME" ]; then
  echo "ERROR: No Chrome/Chromium binary found. Try render_playwright.py or render_fitz.py instead." >&2
  exit 1
fi

# Fresh temp profile dir — CRITICAL (default profile path is sandbox-blocked)
TMP_PROFILE="$(mktemp -d)/chrome-profile"
mkdir -p "$TMP_PROFILE"

HTML_ABS="$(cd "$(dirname "$HTML_IN")" && pwd)/$(basename "$HTML_IN")"

echo "Rendering: $HTML_ABS -> $PDF_OUT"
"$CHROME" \
  --headless=new \
  --disable-gpu \
  --no-sandbox \
  --disable-dev-shm-usage \
  --user-data-dir="$TMP_PROFILE" \
  --print-to-pdf="$PDF_OUT" \
  --print-to-pdf-no-header \
  --no-pdf-header-footer \
  --virtual-time-budget=5000 \
  "file://$HTML_ABS"

# Cleanup profile dir
rm -rf "$(dirname "$TMP_PROFILE")"

# Verify valid PDF
if head -c 5 "$PDF_OUT" | grep -q "%PDF"; then
  SIZE=$(stat -f%z "$PDF_OUT" 2>/dev/null || stat -c%s "$PDF_OUT" 2>/dev/null)
  echo "OK: $PDF_OUT ($SIZE bytes)"
else
  echo "ERROR: Output is not a valid PDF" >&2
  exit 1
fi