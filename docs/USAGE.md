# Kageha usage

How to install, run, and use Kageha day to day.

| Doc | When |
|-----|------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the kernel is shaped |
| [WEBUI.md](WEBUI.md) | Browser UI |
| [RESEARCH_BACKEND.md](RESEARCH_BACKEND.md) | Fast research tools |
| [BROWSER_ENGINE.md](BROWSER_ENGINE.md) | Interactive browser pack |

---

## 1. Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- At least one model API key (`GEMINI_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`)
- Optional: Node.js 20+ (WebUI frontend build, WhatsApp QR bridge)

---

## 2. Install

```bash
cd kageha_agent
uv sync
cp .env.example .env
```

Edit `.env` and set at least one key, for example:

```bash
GEMINI_API_KEY=your_key_here
```

Check health:

```bash
uv run kageha doctor
uv run kageha models list
```

Optional extras (only when you need them):

```bash
uv sync --extra browser      # Playwright
uv sync --extra computer     # macOS computer-use fallback
uv sync --extra mcp
uv sync --extra channels     # messaging adapters
uv sync --extra connections  # Gmail / Calendar / Drive / GitHub
uv sync --extra pdf
```

---

## 3. Run

### CLI chat (primary)

```bash
uv run kageha chat
```

### One-shot task

```bash
uv run kageha run "Summarize the last git commit"
# Skip approval prompts only in trusted workspaces:
uv run kageha run "…" --auto-approve
```

### WebUI

Step-by-step start and use guide: **[WEBUI.md](WEBUI.md)**.

```bash
cd src/kageha/webui/frontend && npm install && npm run build
uv run kageha webui          # http://127.0.0.1:8788
uv run kageha webui --open   # open browser
```

In the UI: type in the composer, `/` for slash commands, `@` for files, **Cmd/Ctrl+K** for the command palette. Modes and Ask/Auto match the CLI.

### Messaging (optional)

```bash
uv run kageha telegram
uv run kageha whatsapp      # Cloud API
uv run kageha teams
uv run kageha gateway --help
```

Set channel tokens in `.env` (see `.env.example`).

---

## 4. First session

In `kageha chat` (or the WebUI composer):

| You type | What happens |
|----------|----------------|
| A normal question | Model answers; may use tools |
| `/plan` | Plan mode — research, approve, then execute |
| `/spec` | Spec mode — clarify → plan → skill gaps → DAG |
| `/goal` | Goal mode — drive a falsifiable objective |
| `/normal` | Back to default conversational depth |
| `/memory status` | Memory health |
| `/memory remember …` | Store a preference or fact |
| `/browser` | Browser backend status (needs pack for interactive) |
| `/research flash …` | One-shot blink research |
| `/computer` | macOS computer-use status (Darwin) |
| `/help` | List slash commands |

Switch models:

```bash
uv run kageha models list
# In chat: /model gemini-flash
```

Interactive setup wizard (optional):

```bash
uv sync --extra setup
uv run kageha setup
```

---

## 5. Modes

Modes change policy (clarify / plan approval / verify), not the codebase.

| Mode | Enter | Use when |
|------|-------|----------|
| `normal` | default | Everyday Q&A and small edits |
| `plan` | `/plan` | Design then do — approve before large work |
| `spec` | `/spec` | Product/engineering: clarify → plan → DAG |
| `goal` | `/goal` | Long objective with a living goal card |

Example:

```text
/spec
Build a CLI that converts markdown folders to a static site.
Prefer existing skills; invent one only if nothing fits.
```

Hard risk actions still need approval in every mode (including `goal`).

---

## 6. Tool packs

Default install loads **core only** (files, shell, web search, forge, skills, MCP, memory, subagents, research).

Enable optional packs when a task needs them:

```bash
export KAGEHA_TOOL_PACKS=browser,media
# or in ~/.kageha/tools.yaml:
# packs: [browser, media]
```

| Pack | When you need it |
|------|------------------|
| `browser` | Interactive Playwright / Comet control |
| `pdf` | `pdf_extract` / `pdf_meta` |
| `computer` | macOS desktop AX/SOM (cua-driver) |
| `media` | Native image/video generation tools |
| `kb` | Attached knowledge bases |
| `diagram` | Diagram helpers |
| `product_import` | Product / social image import |
| `connections` | Native Gmail / Calendar / Drive / GitHub |

Prefer **skills** for media and devices even when a pack exists.

---

## 7. Everyday recipes

**Research then write**

```bash
uv run kageha chat
# /plan
# Research X and draft a one-page brief in ./out/brief.md
```

**Blink research (no browser pack)**

```bash
uv run kageha research "What changed in Python 3.13?" --depth flash
# or in chat: /research flash …
```

**Browser automation**

```bash
uv sync --extra browser && uv run playwright install chromium
export KAGEHA_TOOL_PACKS=browser
uv run kageha chat
# /comet   # logged-in Comet CDP, if installed
```

**Add MCP filesystem + chat**

```bash
uv run kageha mcp add fs -c npx -a -y -a @modelcontextprotocol/server-filesystem -a "$PWD"
uv run kageha chat
```

**Create a skill this session**

```text
Create a skill that wraps our internal lint script, validate it, then run it on src/
```

**WebUI with computer-use (macOS)**

```bash
./scripts/install_computer_driver.sh
cua-driver permissions grant
uv sync --extra computer
# Pack auto-enables when cua-driver is present; opt out with KAGEHA_COMPUTER=0
uv run kageha webui
```

---

## 8. Skills

```bash
uv run kageha skills list
uv run kageha skills browse
uv run kageha skills add ./my-skill
uv run kageha skills install owner/repo
uv run kageha skills new my_skill
uv run kageha skills validate my_skill
```

In-agent: `skill_list` → `skill_load` → `skill_run`.

Bundled starters include `getting_started`, `web_research`, `memory`, `generate_image_gemini`, `generate_media`, `computer_use`, `web_browse`, `sony_bravia`, `android_tv`, `network_scan`, and creative carousel/reel skills.

---

## 9. MCP

```bash
uv run kageha mcp init
uv run kageha mcp add filesystem -c npx -a -y -a @modelcontextprotocol/server-filesystem -a /tmp
uv run kageha mcp list
uv run kageha mcp test filesystem
uv run kageha mcp serve          # stdio server for hosts
```

Config: `~/.kageha/mcp.yaml`. Tools appear as `mcp_<server>_<tool>`.

---

## 10. Memory

```bash
uv run kageha memory status
uv run kageha memory list project
uv run kageha memory remember "Prefer uv over pip in this repo."
uv run kageha memory recall "package manager"
uv run kageha memory import-rules .       # AGENTS.md / CLAUDE.md / .cursor/rules
uv run kageha memory consolidate --force
uv run kageha memory export ./memory-export.md
```

Authority: `~/.kageha/memory/memory.db`. Kill switch: `KAGEHA_MEMORY_ENABLED=0`.

---

## 11. Models

```bash
uv run kageha models list
uv run kageha models doctor
uv run kageha models auth --help   # subscription OAuth helpers
```

Providers live in `~/.kageha/models.yaml` or project `.kageha/models.yaml`.  
Project ships a starter [`models.yaml`](../models.yaml).

---

## 12. Research & browser (deeper)

Same slash commands in CLI chat and WebUI:

```text
/browser                 # status
/browser list
/browser use lightpanda  # persist ~/.kageha/browser.json
/browser comet start
/research flash <query>
```

CLI:

```bash
uv run kageha browser status
uv run kageha browser use lightpanda
uv run kageha research "…" --depth flash
```

Agent tools: `research_run`, `parallel_web_fetch`, `headless_fetch`.  
Full detail: [RESEARCH_BACKEND.md](RESEARCH_BACKEND.md), [BROWSER_ENGINE.md](BROWSER_ENGINE.md).

---

## 13. Media & devices

**Media** — prefer skills:

```text
skill_run generate_image_gemini …
skill_run generate_media …
```

Or enable `KAGEHA_TOOL_PACKS=media` with `GEMINI_API_KEY` / `FAL_KEY` / `SILICONFLOW_API_KEY`.

**Devices** — skills + libraries (not harness packs):

```bash
uv run kageha bravia discover
uv run kageha bravia pair
uv run kageha bravia status
# In agent: skill_run sony_bravia / android_tv / network_scan
```

Env: `KAGEHA_BRAVIA_HOST`, `KAGEHA_ANDROID_TV_HOST`, … (see `.env.example`).

---

## 14. Knowledge bases & connections (optional)

```bash
uv sync --extra zvec
export KAGEHA_TOOL_PACKS=kb
uv run kageha kb create docs --engine zvec --source ./docs
uv run kageha kb attach docs
uv run kageha run "What does the architecture say about modes?" --kb docs
```

SaaS: prefer MCP when available. Native Google/GitHub needs `connections` pack + `kageha connect setup …`.

---

## 15. Config home & ops

Everything durable lives under `KAGEHA_HOME` (default `~/.kageha`):

| Path | Purpose |
|------|---------|
| `.env` / project `.env` | API keys, packs, budgets |
| `models.yaml` | Providers and role ladders |
| `tools.yaml` | Pack allow/deny, risk policy |
| `mcp.yaml` | MCP servers |
| `skills/` | User skills |
| `memory/memory.db` | Memory authority |
| `runtime/runtime.db` | Durable event journal |
| `sessions/` | Per-run workspaces and artifacts |

```bash
uv run kageha doctor
uv run kageha runtime --help
uv run kageha daemon --help
```

---

## 16. Budgets and safety

| Variable | Meaning |
|----------|---------|
| `KAGEHA_MAX_STEPS` | Loop step cap |
| `KAGEHA_MAX_USD` | Spend cap |
| `KAGEHA_TOOL_PACKS` | Optional packs |
| `KAGEHA_TOOL_OUTPUT_LIMIT` | Tool result char cap (default 12000) |
| `KAGEHA_MEMORY_ENABLED` | Memory on/off |
| `KAGEHA_COMPUTER=0` | Force-disable computer pack |

Hard risk actions prompt for approval unless you pass `--auto-approve` (CLI) or set an explicit auto-approve policy for that surface.

---

## 17. Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check src tests
```

WebUI frontend:

```bash
cd src/kageha/webui/frontend
npm test
npm run build
```
