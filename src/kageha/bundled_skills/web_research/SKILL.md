---
name: web_research
description: Blink-speed research via research_run (parallel search+fetch), optional headless enrich, then a sourced answer (chat by default; brief.md only when asked).
triggers:
  - research
  - look up
  - find sources
  - cite sources
  - with citations
  - investigate
  - news about
  - background on
  - research_run
allowed-tools: research_run parallel_web_fetch headless_fetch web_search parallel_web_search web_fetch browser_connect browser_open browser_snapshot browser_click browser_fill browser_screenshot browser_close
---

# web_research

## Deliverable fidelity

**Default:** answer in the assistant message after research. Do **not** write
`research/brief.md`, `research/notes.md`, or other .md files for casual Q&A
("who is…", "what is…", short follow-ups) unless the user asked for a brief,
report, save, export, or file.

**When a file is requested:** produce `research/brief.md` (+ notes). Not
video/carousel unless asked.

## Blink path (preferred)

1. Clarify the question (in working memory — no `question.md` unless asked).
2. **One call:** `research_run(query=…, depth="flash")`
   - `flash` — parallel search + HTTP extract (fastest, no Chromium)
   - `standard` — flash + warm headless JS for thin/SPA pages (Lightpanda or Chromium pool)
   - `deep` — standard + then use `browser_*` for login/interaction
3. Optional: `parallel_web_fetch` / `headless_fetch` for extra URLs.
4. Only if a source needs login/clicks: skill `web_browse` (`browser_connect(comet)` → `browser_open` → snapshot/act). Screenshot only for evidence.
5. **Chat answer** with inline `[n]` citations and a `## Sources` section (title + URL per id from tool results). Never invent URLs.
6. If the user asked for a saved brief: write findings to `research/notes.md`, then synthesize `research/brief.md` with the same citation style.
7. `browser_close` if a browser session was opened.

## When to fan out

For multi-angle topics, spawn subagents each calling `research_run` on a different angle, then merge into the chat answer (or into briefs only when files were requested).

## Verification

- The chat answer (or `research/brief.md` when requested) answers the question
- Prefer page-sourced quotes over search snippets alone
- Inline `[n]` markers + `## Sources` with real URLs when research tools were used
