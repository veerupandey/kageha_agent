# Usage

Day-to-day use of Kageha after [setup](SETUP.md).

## After setup

If you have not run the wizard yet:

```bash
uv run kageha setup
```

That writes project `.env`, configures a model, optional packs, and prints start commands.

## Run

```bash
uv run kageha chat                 # interactive REPL
uv run kageha run "your task"      # one-shot
uv run kageha webui --open         # browser UI (build frontend first)
```

Trusted workspace only:

```bash
uv run kageha run "…" --auto-approve
uv run kageha chat --sandbox docker   # optional Docker sandbox for shell
```

## First session

In `kageha chat` or the WebUI composer:

| You type | What happens |
|----------|----------------|
| A normal question | Model answers; may use tools |
| `/plan` then a task | Clarify if needed → research → `plan.md` → wait for `/build` |
| `/goal` then a task | Execute now toward a verifiable outcome; HITL on risk |
| `/normal` | Default conversational depth |
| `/build` | Execute the pending plan |
| `/help` | List commands |

Switch models:

```bash
uv run kageha models list
# In chat: /model azure-mini
```

## Modes

Modes change policy (clarify, plan approval, verify) — not the codebase.

| Mode | Enter | Behavior |
|------|-------|----------|
| `normal` | default | Everyday Q&A and small edits |
| `plan` | `/plan` | Clarify → research → editable `plan.md` → `/build` |
| `goal` | `/goal` | Execute now; living goal card; Approve / Deny / Suggest on risk |

Example:

```text
/plan
Build a CLI that converts markdown folders to a static site.
```

Edit `plan.md` or reply with changes, then `/build` when ready.

On approval prompts you can **Approve**, **Deny**, or **Suggest** steering text.

## WebUI

Build once (or after frontend changes):

```bash
cd src/kageha/webui/frontend
npm install
npm run build
cd -
uv run kageha webui --open
```

In the browser:

- Composer + **Enter** to send (Shift+Enter = newline)
- `/` slash commands, `@` project files / artifacts
- **Cmd/Ctrl+K** command palette
- Mode chips and Ask/Auto match the CLI
- Approval banner: Approve / Deny / Suggest

Sessions live under `~/.kageha/sessions/`.

## Messaging channels

See [CHANNELS.md](CHANNELS.md) for Telegram polling and experimental WhatsApp
QR setup, allowlists, media behavior, and security limitations.

Hot reload (two terminals):

```bash
uv run kageha webui --port 8788
cd src/kageha/webui/frontend && npm run dev
```

## Tool packs

Default: files, shell, web search, forge, skills, MCP, memory, subagents, research.

```bash
export KAGEHA_TOOL_PACKS=browser,computer,media
```

| Pack | When |
|------|------|
| `browser` | Interactive browser / Comet |
| `computer` | macOS desktop (cua-driver) |
| `media` | Fal video / optional Fal stills (`FAL_KEY`). Prefer core `nano_banana_generate` / `nano_banana_edit` (`GEMINI_API_KEY`) for stills. |

Prefer **skills** or **MCP** for domain work.

## Skills

```bash
uv run kageha skills list
uv run kageha skills browse
uv run kageha skills add ./my-skill
uv run kageha skills install owner/repo
uv run kageha skills new my_skill
uv run kageha skills validate my_skill
```

In-agent: `skill_list` → `skill_load` → `skill_run`.  
Explicit: `/skill <name>` or `$<name>`.

Bundled: `getting_started`, `web_research`, `web_browse`, `memory`, `computer_use`.

## Project Brain

Project Brain gives the agent persistent, per-project context. Drop a few
markdown files in your repo root and Kageha loads them into the system prompt
on **every turn** — so the model always knows your conventions, stack, and
guardrails without you repeating them.

### What it looks for

| Location | Behavior | Example |
|----------|----------|---------|
| `AGENTS.md` | Root instructions (first match wins) | Project overview, coding standards |
| `KAGEHA.md` | Root instructions (fallback) | Same as above |
| `CLAUDE.md` | Root instructions (fallback) | Cursor/Claude compatibility |
| `.cursorrules` | Root instructions (fallback) | Cursor compatibility |
| `.cursor/rules/` | Root instructions (merged, fallback) | Multiple Cursor rule files |
| `.kageha/rules/*.md` | **Stacking** rules (up to 24 files) | Scoped conventions |
| `.kageha/commands/*.md` | Slash-command recipes | `/project:review` |

### Root instructions (first match only)

Create one file at the repo root. Kageha checks candidates in order and uses
the **first** one found — they do not stack:

```bash
# AGENTS.md — preferred
cat > AGENTS.md <<'EOF'
# Project conventions
- Python 3.11+, fully typed
- Use uv for all dependency management
- Prefer functional, stateless components
- Never commit .env or secrets
EOF
```

### Rules (all stack)

Files under `.kageha/rules/` are **additive** — every matching file loads.
Each can carry an optional frontmatter `globs` filter so a rule only applies
when relevant files are touched:

```bash
mkdir -p .kageha/rules

# .kageha/rules/python-style.md
cat > .kageha/rules/python-style.md <<'EOF'
---
globs: ["**/*.py"]
---
- Use type hints on all function signatures
- Prefer pathlib over os.path
- Line length: 100
EOF

# .kageha/rules/frontend.md  (no globs → always on)
cat > .kageha/rules/frontend.md <<'EOF'
---
globs: ["src/**/*.tsx", "src/**/*.ts"]
---
- Functional components with hooks
- IBM Plex Sans / Mono font stack
- Tailwind utility classes only
EOF
```

A rule with **no globs** is always included. A rule **with globs** is included
on the first turn and whenever the agent edits files matching the pattern
(keeping context lean on unrelated turns).

### Commands (slash recipes)

Files under `.kageha/commands/` become project-scoped slash commands:

```bash
mkdir -p .kageha/commands

# .kageha/commands/review.md
cat > .kageha/commands/review.md <<'EOF'
Review the current diff for:
1. Correctness bugs
2. Security issues (injection, auth, data exposure)
3. Missing error handling
Report findings sorted by severity.
EOF
```

Type `/project:review` in chat to expand it into your message.

### How it reaches the model

1. On each turn, Kageha reads the brain files from the project root.
2. `render_project_brain()` assembles them into a `## Project instructions`
   block (root file first, then matching rules, then command names).
3. That block is appended to the **system prompt** and sent to the LLM —
   it is not searched at query time, it is preloaded every turn.

Limits: 12 000 chars per root file, 4 000 per rule, 24 000 total (truncated
if exceeded).

### Inspecting the brain

```bash
# CLI — see what the agent sees (prints JSON summary + rendered brain text)
uv run kageha project brain
```

In the **WebUI**, click **Project Brain** in the sidebar to view the loaded
root file, rules (with glob scopes), commands, and a collapsible rendered
preview — exactly what gets injected into the system prompt.

## MCP

```bash
uv run kageha mcp init
uv run kageha mcp add fs -c npx -a -y -a @modelcontextprotocol/server-filesystem -a "$PWD"
uv run kageha mcp list
uv run kageha mcp test fs
uv run kageha mcp serve
```

Config: `~/.kageha/mcp.yaml`. Tools appear as `mcp_<server>_<tool>`.

## Memory

```bash
uv run kageha memory status
uv run kageha memory remember "Prefer uv over pip in this repo."
uv run kageha memory recall "package manager"
uv run kageha memory import-rules .
uv run kageha memory consolidate --force
```

Authority: `~/.kageha/memory/memory.db`. Off: `KAGEHA_MEMORY_ENABLED=0`.

## Research and browser

```bash
uv run kageha research "What changed in Python 3.13?" --depth flash
# In chat: /research flash …

export KAGEHA_TOOL_PACKS=browser
uv run kageha browser status
uv run kageha browser use lightpanda
# In chat: /browser, /browser comet start
```

Agent tools: `research_run`, `parallel_web_fetch`, `headless_fetch`, plus `browser_*` when the pack is on.

## Computer use (macOS)

```bash
./scripts/install_computer_driver.sh
cua-driver permissions grant
uv sync --extra computer
uv run kageha webui
# Opt out: KAGEHA_COMPUTER=0
```

## Common recipes

**Plan then build**

```text
/plan
Research X and draft a one-page brief in artifacts/brief.md
```

Then edit the plan if needed and `/build` in the **same** chat/session.

Background jobs:

```bash
uv run kageha jobs run "/plan Research X and draft artifacts/brief.md"
# status → awaiting_plan_approval  (note the session id)
uv run kageha jobs run --resume SESSION --build
# or: uv run kageha chat --resume SESSION  → type /build
```

Do not run `jobs run "/build …"` as a new job — that starts another Plan.

**Goal execute-now**

```text
/goal
Create artifacts/hello.txt containing exactly: hi
```

**MCP + chat**

```bash
uv run kageha mcp add fs -c npx -a -y -a @modelcontextprotocol/server-filesystem -a "$PWD"
uv run kageha chat
```

## Budgets and safety

| Variable | Meaning |
|----------|---------|
| `KAGEHA_MAX_STEPS` | Loop step cap |
| `KAGEHA_MAX_USD` | Spend cap |
| `KAGEHA_TOOL_PACKS` | Optional packs |
| `KAGEHA_TOOL_OUTPUT_LIMIT` | Tool result char cap (default 12000) |
| `KAGEHA_MEMORY_ENABLED` | Memory on/off |
| `KAGEHA_COMPUTER=0` | Force-disable computer pack |

Hard risk actions prompt unless you pass `--auto-approve` or use `/permissions auto` (tool approvals only — not Plan Build).

## CLI cheat sheet

| Command | Purpose |
|---------|---------|
| `kageha setup` | Guided setup (API keys or Codex/Antigravity OAuth, packs, default model) |
| `kageha chat` | Interactive REPL |
| `kageha run "…"` | Single task |
| `kageha webui` | Browser UI |
| `kageha models list` | Providers |
| `kageha models setup` | Alias of `kageha setup` |
| `kageha skills …` | Skills |
| `kageha mcp …` | MCP |
| `kageha memory …` | Memory |
| `kageha research "…"` | Blink research |
| `kageha browser …` | Browser backend |
| `kageha runtime …` | Session journal |
| `kageha jobs …` | Background jobs |

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check src tests
cd src/kageha/webui/frontend && npm test && npm run build
```

### Qualification commands

- `scripts/qualify_core.sh` — Core_Qualification_Command: fast,
  representative subset (lint + Python tests). Use this for quick local
  iteration.
- `scripts/qualify.sh [runs]` — Full_Qualification_Command: Python lint,
  Python type checking, the Python test suite, and the frontend test suite
  (lint, build/type-check, `npm test`), run together non-interactively. Pass
  a run count (default 1) to repeat the full sequence, e.g.
  `scripts/qualify.sh 3` for a release-gate stability check.


See `docs/ARCHITECTURE.md` for the current component-status picture (integrated
vs. experimental) and `docs/MIGRATION.md` for upgrading an existing deployment
past the reliability-spine schema migration.


### WebUI: new UI flag

The WebUI ships two layouts side by side:

- **New UI (Canvas)** — sidebar with agents/threads, a centered command-center home
  view, and a split thread view (conversation + artifact canvas with a lightbox). This is
  the default.
- **Old UI** — the original `SessionsRail` + `Stage` layout, kept fully functional as a
  fallback.

The active layout is controlled by `prefs.newUi` (a boolean in the persisted WebUI
preferences, default `true`). Toggle it from the gear icon at the bottom of the sidebar in
the new UI, or by clearing/setting `newUi` directly in the browser's stored preferences.
`App.tsx` branches on this flag; both layouts read from the same Zustand store, so
switching between them does not lose session state.
