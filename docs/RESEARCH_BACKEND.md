# Research backend (blink-speed)

Goal: research that feels **instant** — search + page reads in **one tool call**, with a warm headless pool when JS is required, and full browser control only when the user must click/login.

Day-to-day commands: [USAGE.md](USAGE.md) §12. Interactive control: [BROWSER_ENGINE.md](BROWSER_ENGINE.md).

## Why multi-turn research is slow

Typical agent loop:

```text
LLM → web_search → LLM → web_fetch → LLM → browser_open → …
```

Each arrow is seconds of model time. The network work is often the smaller cost.

**Fix:** do the fan-out inside `research_run`.

## Tiers

| Depth | What runs | Browser? | Use when |
|-------|-----------|----------|----------|
| **flash** | Parallel search + parallel HTTP extract (`web_fetch` path) | No | Default. Docs, news, APIs, most research |
| **standard** | flash + warm headless CDP extract for thin/SPA pages | Headless pool | JS-rendered content, empty HTTP bodies |
| **deep** | standard + interactive `browser_*` follow-up | Full control | Login, forms, multi-step UI |

## Headless backends

`KAGEHA_HEADLESS_BACKEND`:

| Value | Behavior |
|-------|----------|
| `auto` | Probe `KAGEHA_HEADLESS_CDP`; if up → connect; else warm Chromium |
| `http` | Never start a browser (flash-only enrich) |
| `chromium` | Launch once, keep warm Playwright Chromium |
| `lightpanda` / `cdp` | Connect to external CDP (fastest local option) |

**Lightpanda** (Zig, CDP-compatible, ~10× lighter than Chrome) for blink headless:

```bash
lightpanda serve --host 127.0.0.1 --port 9222
export KAGEHA_HEADLESS_BACKEND=lightpanda
export KAGEHA_HEADLESS_CDP=http://127.0.0.1:9222
```

**Proper browser control** (headed / logged-in) stays on the optional `browser` pack + Comet CDP — unchanged.

## Tools (core pack `research`)

| Tool | Role |
|------|------|
| `research_run` | One-shot parallel research |
| `parallel_web_fetch` | Parallel HTTP extracts (cached) |
| `headless_fetch` | Parallel warm-pool JS extracts |

## Citations

Web/research tools expose compact `sources` (`{id, url, title, snippet?}`).
Search hits are numbered `[1]…`; `research_run` ends with `## Sources`.
Final answers should cite with `[n]` + a Sources section (WebUI renders chips).

## Architecture

```text
research_run(query, depth)
    │
    ├─► parallel web_search (cached)
    ├─► parallel HTTP fetch  (cached)     ← flash
    ├─► warm HeadlessPool extract         ← standard/deep (thin URLs only)
    └─► hint → browser_* for interaction  ← deep
```

```text
src/kageha/research/
  backend.py   # orchestration
  cache.py     # TTL search/fetch cache
  pool.py      # warm Chromium / Lightpanda CDP
harness/tools/research.py
```

## Native slash + CLI

```text
/browser                 # status
/browser list            # backends
/browser use lightpanda  # or comet|chromium|headless|docker|http|cdp
/browser comet start     # logged-in Comet + enable browser pack
/browser cdp http://127.0.0.1:9222
/research flash <query>  # native blink research (no LLM loop)
```

CLI equivalents: `kageha browser status|list|use|research`.

Prefs persist to `~/.kageha/browser.json` and auto-enable the optional `browser` pack for interactive backends.

## Speed checklist

1. Prefer `research_run(..., depth="flash")` — default (agent prompt requires this for research Q&A).
2. Key a search API (Brave/Tavily) — avoids Gemini 60s / fragile DDG.
3. For SPA-heavy topics: `/browser lightpanda` then `depth="standard"`.
4. Screenshots off by default on `browser_open`.
5. Cache TTL via `KAGEHA_RESEARCH_CACHE_TTL` (default 600s).

## Related

- Browser interaction model: `docs/BROWSER_ENGINE.md`
- Skill: `web_research`
