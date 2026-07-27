# Browser engine

Kageha’s browsing stack is designed to be **faster and more agent-efficient** than typical “always launch Chromium” tools (and competitive with Cursor / Claude Code / Codex browser loops).

Enable the pack and slash commands: [USAGE.md](USAGE.md) §6 / §12. Research-first path: [RESEARCH_BACKEND.md](RESEARCH_BACKEND.md).

## What we steal from the best

| Source | Idea | Kageha |
|--------|------|--------|
| **Cursor** | AX snapshot + opaque refs; lock; CDP escape hatch; screenshot ≠ structure | `browser_snapshot`, `browser_lock`, `browser_cdp`, separate `browser_screenshot` |
| **Claude Code / Codex** | open → snapshot → act → re-snapshot CLI loop | `web_browse` skill + sticky `BrowserEngine` session |
| **Perplexity** | Search-first; browser only when needed | `web_search` → `web_fetch` → `browser_*` tiering |

## Why not Rust (yet)

Playwright already speaks CDP efficiently; cold start and **token-bloated screenshots** dominate latency, not Python. Wins come from:

1. **Tiered I/O** — `web_fetch` (httpx + stdlib HTML extract) skips Chromium for static pages.
2. **Warm session** — one `BrowserEngine` per harness registry; no relaunch per click.
3. **Cheap act loop** — AX refs without mandatory screenshots every step.
4. **Multi-tab + lock** — parallel research tabs; exclusive multi-step flows.

A Rust CDP sidecar can later implement the same `BrowserEngine` surface if protocol parsing becomes the bottleneck. Do not rewrite the pack until a bench proves it.

## Layout

```text
harness/browser/
  engine.py      # session, tabs, lock, navigate, act, evaluate, cdp
  snapshot.py    # Accessibility.getFullAXTree → eN refs (+ DOM fallback)
  fetch.py       # web_fetch (core tool, no Playwright)
harness/tools/browser.py   # @tool pack wired to BrowserEngine
harness/browser_sandbox.py # Docker Chromium + noVNC
```

## Agent loop

Prefer the research backend for read-heavy work (`docs/RESEARCH_BACKEND.md`):

```text
research_run(flash|standard) → synthesize
```

Interactive control:

```text
web_search → web_fetch(url)? → else browser_lock → browser_open
  → browser_snapshot (e0…) → click/fill/press → re-snapshot
  → browser_screenshot (evidence only) → unlock → close
```

Comet/CDP: `browser_connect(target=comet)` opens an **agent-owned tab** and never steals the user’s focused tab.
