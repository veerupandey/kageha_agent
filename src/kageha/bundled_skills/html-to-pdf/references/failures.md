# HTML→PDF Failure Log

Documented from a real production session (2025-08-04). Read this BEFORE
debugging a failed PDF render — the answer is likely already here.

## The escalation ladder (what to try, in order)

```
1. Elevated Chrome headless --print-to-pdf   ← WORKS (try first)
2. Playwright sync_playwright page.pdf()      ← FAILED in sandbox
3. PyMuPDF (fitz) Story engine                 ← FAILED (hung / low fidelity)
4. fpdf2 write_html()                          ← untested, last resort
```

## Failure #1 — Playwright crash on launch (sandbox profile-dir block)

**Symptom:**
```
playwright._impl._api_types.Error: Executable doesn't exist at
/Users/.../chrome-mac/headless_shell
```
OR
```
playwright._impl._api_types.Error: ProcessLaunchError: ...
```

**Root cause:** The Kageha default OS sandbox blocks writes to the Chrome
user-data-dir / profile directory. Playwright's bundled Chromium cannot
create or access its profile, so it crashes on launch.

**Fix:** Do NOT try to fix Playwright. Use elevated Chrome instead:
```bash
bash(elevated=True) → render_chrome.sh
```
The elevated Chrome command uses `--user-data-dir` pointing at a fresh
mktemp directory, which bypasses the profile-dir block entirely.

**Also tried (did NOT work):**
- `python -m playwright install chromium` → fails in sandbox (no network
  for the download, or write permission denied to the install path).
- Pointing Playwright at the system Chrome via `channel="chrome"` → same
  profile-dir crash.

---

## Failure #2 — PyMuPDF (fitz) Story hangs / stalls indefinitely

**Symptom:** The script runs forever. No output, no error. Have to kill it.

**Root cause:** `fitz.Story()` attempts to parse and render complex inline
`<style>` blocks (especially CSS with `flex`, `grid`, `@media`, dark-theme
color schemes). The Story HTML/CSS engine is limited and can enter a near-
infinite loop on certain CSS constructs.

**Fix:** ALWAYS strip `<style>` and `<script>` tags from the HTML body
before passing to `fitz.Story()`, then pass a simplified CSS string via
`user_css=`. Also wrap the call in `signal.alarm(25)` so it times out
instead of hanging forever:

```python
body_clean = re.sub(r"<style[^>]*>.*?</style>", "", body_html, flags=re.DOTALL)
story = fitz.Story(html=body_clean, user_css=SIMPLE_CSS)
signal.alarm(25)  # 25-second timeout
```

**Even with the fix, fitz.Story has low fidelity:**
- No CSS gradients (hero banners render flat)
- Limited table styling (borders OK, but `thead` background colors may drop)
- Ignores `@media print` rules
- No web fonts (uses Helvetica only)

→ For pixel-perfect output matching the screen render, use elevated Chrome.

---

## Failure #3 — `uv pip install` / `install_python_packages` cache errors

**Symptom:**
```
error: failed to create directory .../uv/cache
Permission denied
```
OR
```
Collecting weasyprint
  ERROR: Could not install packages due to an EnvironmentError
```

**Root cause:** The sandbox restricts writes to the pip/uv cache directory.
Even `install_python_packages` (which targets `.kageha_pkgs/`) can fail when
underlying build tools (like weasyprint's C extensions for cairo/pango) need
to write to system temp paths.

**Fix:** Do NOT fight pip. Skip Python-based PDF libraries entirely and use
elevated Chrome (`render_chrome.sh`), which needs no Python packages at all —
just the system Chrome binary.

**Packages that specifically FAILED to install in the sandbox:**
- `weasyprint` (needs cairo/pango/gobject C libs + system writes)
- `reportlab` (installed but could not render styled HTML well)
- `fpdf2` (installed, works, but very basic HTML support)

---

## Failure #4 — headless Chrome in default sandbox (non-elevated)

**Symptom:**
```
[0814/xxxxxx.ERROR:file_io.cc(89)] open failed: .../Default/Cookies: Operation not permitted
```
OR Chrome exits silently, producing no PDF.

**Root cause:** Same as Failure #1 — Chrome cannot write its profile to the
default location inside the sandbox.

**Fix:** Two things together fix it:
1. `bash(elevated=True)` — escape the sandbox (prompts user for approval).
2. `--user-data-dir="$(mktemp -d)/chrome-profile"` — fresh writable profile.

Both are required. Without `elevated=True`, the mktemp dir may still be in
a sandbox-restricted path. Without `--user-data-dir`, Chrome uses its
default profile path which is always blocked.

---

## What finally worked (the winning command)

```bash
# Run via bash(elevated=True)
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
TMP="$(mktemp -d)/profile"
"$CHROME" --headless=new --disable-gpu --no-sandbox \
  --user-data-dir="$TMP" \
  --print-to-pdf="$KAGEHA_ARTIFACTS/output.pdf" \
  --print-to-pdf-no-header \
  --no-pdf-header-footer \
  --virtual-time-budget=5000 \
  "file:///abs/path/to/input.html"
```

Result: 8-page, 1.09 MB PDF, pixel-perfect, all gradients/tables/code blocks
preserved. Production-grade output.