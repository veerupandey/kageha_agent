# Usage

Day-to-day use of Kageha after [setup](SETUP.md).

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
| `media` | Fal image/video (`FAL_KEY`) |

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

Then edit the plan if needed and `/build`.

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
| `kageha chat` | Interactive REPL |
| `kageha run "…"` | Single task |
| `kageha webui` | Browser UI |
| `kageha doctor` | Runtime / sandbox health |
| `kageha models doctor` | Models / keys / packs |
| `kageha models list` | Providers |
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
