---
name: getting_started
description: Orient to Kageha — where skills/tools live, how chat routing works, and which media/MCP knobs matter for a first successful run.
---

# getting_started

## Deliverable fidelity

A short orientation for the agent (and humans). Prefer loading this when the user asks how Kageha works or seems stuck on setup.

## Chat routing (self-depth)

Most messages become an **agent** turn. The model chooses how deep to go (0…N tool
calls). Only a few high-confidence micro-paths skip the loop:

| Path | Examples |
|------|----------|
| `quick_remote` | `pause`, `vol up`, `open youtube` (skill `fast-path`) |
| `quick_where` / `quick_status` | “where did you save it?”, `/status` |
| `cancel` | cancel / stop / nevermind |
| Deeper modes | `/plan`, `/spec`, `/goal`, or `escalate_plan(mode=…)` |

See `docs/ARCHITECTURE.md` (modes / self-depth) and `docs/USAGE.md`.

## Layout

| Location | Purpose |
|----------|---------|
| Package `bundled_skills/` | Core skills shipped with the agent (this pack) |
| Repo `skills/` | Symlink to `bundled_skills/` in checkouts (edit the package path) |
| `~/.kageha/skills/` | User skills (override bundled by name; curator-managed) |
| `.kageha/skills/` | Project-local skills for the current repo |
| `KAGEHA_SKILLS_PATH` | Extra colon-separated skill roots |
| `~/.kageha/tools/` + `KAGEHA_TOOLS_PATH` | Custom Python tool packs (`register(ctx)`) |
| Entry points `kageha.tools` | Third-party / plugin tool packs |
| `~/.kageha/mcp.yaml` | MCP servers (stdio / SSE / streamable HTTP) |

## Progressive disclosure

1. Catalog (L1) — skill names ranked for the current task (intent match)  
2. Auto-load / `skill_load <name>` (L2) — full `SKILL.md` body when score clears the floor  
3. `skill_read` / `skill_run` (L3) — references, scripts, assets  

Matching uses description tokens + optional `triggers:` frontmatter + embeddings.
`KAGEHA_SKILL_AUTOLOAD_MIN` (default `3`) blocks weak one-token hits from injecting
heavy skill bodies. Exclusive families (`computer_use`/`web_*`, creative `make_*`)
load only the winning skill.

**Explicit invocation** (bypasses the score floor and `disable-model-invocation`):
`/make_reel …`, `$make_reel …`, or `/skill make_reel …`.

**Cursor/Codex controls** in frontmatter:
- `paths:` / `globs:` — only auto-match when the task mentions matching files
- `disable-model-invocation: true` (or `allow_implicit_invocation: false`) — manual only

Match a task with `skill_list` / auto-load; do not dump every skill body.

Skills may set `triggers`, `paths`, `disable-model-invocation`, `fast-path`, and `fast-path-when` in frontmatter.

## High-value core skills

- `web_research` / `web_browse` — search + browser loop (Comet for login; needs `KAGEHA_TOOL_PACKS=browser`)  
- `generate_image_gemini` / `generate_media` — images/video via MediaProvider (no media pack required)  
- `pdf_ingest` — extract/summarize PDFs (`uv sync --extra pdf` + `KAGEHA_TOOL_PACKS=pdf`)  
- `memory` — trust / mutate memory safely  
- Device skills (`network_scan`, `sony_bravia`, `android_tv`) — **skill_run scripts only** (no harness tools)  
- `make_diagram` / `make_presentation` / `make_infographic` — structured artifacts  
- Creative (carousel/reel) skills when the user wants social/video output  

## Tool packs (trimmed default)

Default loads **core** packs only (`forge`, `skills`, `mcp`, `memory`, `subagent`).
Enable more with `KAGEHA_TOOL_PACKS=browser,media` or `tools.yaml` `packs: [...]`.  
Device control is **never** a tool pack — always skills. See `docs/ARCHITECTURE.md`.

## Media generation (optional keys)

| Env | Tools |
|-----|--------|
| `GEMINI_API_KEY` | `gemini_generate_image` — Nano Banana Pro (`gemini-3-pro-image`) |
| `FAL_KEY` | `fal_generate_image`, `fal_edit_image`, `fal_image_to_video`, `fal_text_to_video` |
| `SILICONFLOW_API_KEY` | `siliconflow_image` |

Media tools are `network` risk (HITL when the router requires it).

## MCP

```bash
kageha mcp init
kageha mcp add <name> -c <cmd> …     # stdio
kageha mcp add <name> --url <url>    # SSE or streamable HTTP
kageha mcp list | test | serve
```

Remote HTTP needs `uv sync --extra mcp`. Host import (Cursor/Claude Desktop): `KAGEHA_MCP_IMPORT_HOST=1`.

## First-run checklist

1. `kageha models setup` / `kageha models doctor`  
2. Confirm skills: `kageha skills list` (should include `getting_started` + core pack)  
3. For images: set `GEMINI_API_KEY` and/or `FAL_KEY`  
4. For MCP: `kageha mcp init` then add a server  
5. Drop custom skills under `~/.kageha/skills/<name>/SKILL.md` or tools under `~/.kageha/tools/*.py`

## Observations

- (2026-07-27) When default browser mode is set to Comet/CDP (port 9222) and Comet is not active, browser_open will throw ECONNREFUSED 127.0.0.1:9222. Fall back to browser_connect(target='headless') or target='docker' to use standard Playwright browser automation without blocking on CDP.
