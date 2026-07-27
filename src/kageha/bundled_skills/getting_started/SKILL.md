---
name: getting_started
description: Orient to Kageha — where skills/tools live, how chat routing works, and which knobs matter for a first successful run.
---

# getting_started

## Deliverable fidelity

A short orientation for the agent (and humans). Prefer loading this when the user asks how Kageha works or seems stuck on setup.

## Chat routing (self-depth)

Most messages become an **agent** turn. The model chooses how deep to go (0…N tool
calls). Only a few high-confidence micro-paths skip the loop:

| Path | Examples |
|------|----------|
| `quick_where` / `quick_status` | “where did you save it?”, `/status` |
| `cancel` | cancel / stop / nevermind |
| Deeper modes | `/plan`, `/goal`, or `escalate_plan(mode=…)` |

See `docs/SETUP.md` and `docs/USAGE.md`.

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
heavy skill bodies. Exclusive families (`computer_use`/`web_*`) load only the winning skill.

**Explicit invocation** (bypasses the score floor and `disable-model-invocation`):
`/skill <name> …` or `$<name> …`.

**Skill frontmatter controls:**
- `paths:` / `globs:` — only auto-match when the task mentions matching files
- `disable-model-invocation: true` (or `allow_implicit_invocation: false`) — manual only

Match a task with `skill_list` / auto-load; do not dump every skill body.

Skills may set `triggers`, `paths`, `disable-model-invocation`, `fast-path`, and `fast-path-when` in frontmatter.

## Bundled skills

- `getting_started` — this orientation  
- `web_research` / `web_browse` — search + browser loop (Comet for login; needs `KAGEHA_TOOL_PACKS=browser`)  
- `memory` — trust / mutate memory safely  
- `computer_use` — macOS desktop control (needs `KAGEHA_TOOL_PACKS=computer` / cua-driver)

Domain work beyond the core goes through **MCP** or user/project skills.

## Tool packs (trimmed default)

Default loads **core** packs only (`forge`, `skills`, `mcp`, `memory`, `subagent`, `research`).
Optional packs: `browser`, `computer`, `media` via `KAGEHA_TOOL_PACKS=browser,computer,media` or `tools.yaml` `packs: [...]`.  
See `docs/USAGE.md`.

## MCP

```bash
kageha mcp init
kageha mcp add <name> -c <cmd> …     # stdio
kageha mcp add <name> --url <url>    # SSE or streamable HTTP
kageha mcp list | test | serve
```

Remote HTTP needs `uv sync --extra mcp`. Host editor MCP import: `KAGEHA_MCP_IMPORT_HOST=1`.

## First-run checklist

1. `kageha models setup` / `kageha models list`  
2. Confirm skills: `kageha skills list` (should include `getting_started` + core pack)  
3. For MCP: `kageha mcp init` then add a server  
4. Drop custom skills under `~/.kageha/skills/<name>/SKILL.md` or tools under `~/.kageha/tools/*.py`

## Observations

- (2026-07-27) When default browser mode is set to Comet/CDP (port 9222) and Comet is not active, browser_open will throw ECONNREFUSED 127.0.0.1:9222. Fall back to browser_connect(target='headless') or target='docker' to use standard Playwright browser automation without blocking on CDP.
