---
name: web_browse
description: Fast human-like web browsing — tiered web_fetch then Playwright AX snapshot/click/type. browser_connect(target=auto) prefers Comet/CDP when reachable, else headless.
triggers:
  - open the site
  - open website
  - log in
  - login
  - fill the form
  - browse to
  - navigate to
  - browser_connect
  - use comet
  - interact with the page
  - click through
allowed-tools: web_fetch browser_connect browser_open browser_snapshot browser_click browser_type browser_fill browser_press browser_scroll browser_wait browser_screenshot browser_evaluate browser_cdp browser_tabs browser_lock browser_close browse_logged_in
---

# web_browse

## When to use

Interactive websites, docs, dashboards, forms. For search-only answers use `web_research` (which may call these tools). For native macOS apps use `computer_use`.

## Tiered speed (important)

1. **`web_search` / `parallel_web_search`** — find URLs.
2. **`web_fetch(url)`** — public/static pages (docs, blogs). No Chromium; fastest.
3. **`browser_*`** — only for JS apps, logins, clicks, SPAs, or when fetch returns empty/blocked content.

## Deliverable fidelity

Produce what was asked (notes, brief, extracted facts, screenshots). Do **not** invent MP4/carousel unless requested.

When the user asks to open/browse a specific site, **do the navigation** — do not list Kageha capabilities instead.

## Loop

1. Prefer `web_fetch` first when the URL is public.
2. Else `browser_lock(lock)` then `browser_open(url)` — reuses one Chromium session on an **agent-owned tab** (Comet/CDP opens a new tab; never navigates the user's focused tab); returns AX snapshot refs.
3. Read the snapshot. Act with **one** of:
   - `browser_click(target)` — `e0` ref, CSS, `text=…`, or `role=button:Name`
   - `browser_fill(target, text)` / `browser_type(target, text)`
   - `browser_press(key)` / `browser_scroll` / `browser_wait`
   - `browser_evaluate(expression)` when refs are not enough
4. After each action, use the returned snapshot (or `browser_snapshot`) — do not guess selectors blindly.
5. `browser_screenshot` only when you need vision evidence — not every step.
6. Keep waits short (`browser_wait` 200–1500ms). Do not relaunch the browser between steps.
7. `browser_lock(unlock)` + `browser_close` when done.

## Login / cookies / Comet

1. Call `browser_connect(target="auto")` once — prefers Comet/CDP when reachable,
   otherwise launches headless Chromium. **Do not stop to ask for `/comet`**
   unless the site truly needs login cookies and the tool reported headless fallback.
2. Immediately `browser_open(url=…)` — do **not** stop after connect.
3. Use `browser_click` / `browser_fill` / `browser_scroll`.
4. `browser_screenshot` for evidence; `browser_close` disconnects only.

If login cookies are required and connect fell back to headless, tell the user
to run `/comet` (or `open -na Comet --args --remote-debugging-port=9222`) and retry.

Fallback one-shot screenshot: `browse_logged_in(url)`.

Or set `KAGEHA_BROWSER_MODE=auto` (default) / `comet` / `headless`.

## Verification

- At least one screenshot under `artifacts/` when the user needs visual proof
- Notes/brief cite the final URL(s)
- Browser closed or left intentional for follow-up

## Observations

- (2026-07-27) Default mode is `auto`: Comet CDP when up, else headless — no ECONNREFUSED block.

## Refinements

### 2026-07-29

- Instagram and other login-gated sites: if `web_fetch` redirects to a login
  page, try `browser_connect(target='auto')` + `browser_open(url)` to access
  the public profile in a browser session. If login is still required, do not
  block with `ask_human`; proceed with available public-site findings and note
  the limitation.
- (2026-07-29) Pitfall: ask_human can time out when the task is blocked by login-gated Instagram access. Prefer using browser_connect/browser_open to inspect the public Instagram page directly, and if login is still required, avoid asking the human in the same turn; instead continue with the public site findings and note the limitation succinctly.
