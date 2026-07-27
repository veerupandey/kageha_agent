# Setup

Install Kageha and get a working chat session.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- At least one model API key (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or Azure OpenAI vars)
- Optional: Node.js 20+ (only if you use the WebUI)

## Install

```bash
cd kageha_agent
uv sync
cp .env.example .env
```

Edit `.env` and set a key, for example:

```bash
GEMINI_API_KEY=your_key_here
```

Confirm models:

```bash
uv run kageha models list
```

## Optional extras

Install only what you need:

```bash
uv sync --extra browser      # Playwright browser control
uv sync --extra computer     # macOS computer-use
uv sync --extra mcp          # extra MCP helpers
uv sync --group dev          # pytest / ruff
```

Browser Chromium (after `--extra browser`):

```bash
uv run playwright install chromium
```

## First run

```bash
uv run kageha chat
```

One-shot:

```bash
uv run kageha run "Summarize the last git commit"
```

WebUI:

```bash
cd src/kageha/webui/frontend && npm install && npm run build && cd -
uv run kageha webui --open
```

## Optional tool packs

Default install is core only. Enable packs when a task needs them:

```bash
export KAGEHA_TOOL_PACKS=browser,computer
```

Or in `~/.kageha/tools.yaml`:

```yaml
packs: [browser, computer]
```

| Pack | Purpose |
|------|---------|
| `browser` | Interactive Playwright / Comet control |
| `computer` | macOS desktop automation (cua-driver) |

## Config home

Durable state lives under `KAGEHA_HOME` (default `~/.kageha`):

| Path | Purpose |
|------|---------|
| `.env` / project `.env` | API keys, packs, budgets |
| `models.yaml` | Providers and role ladders |
| `tools.yaml` | Packs and risk policy |
| `mcp.yaml` | MCP servers |
| `skills/` | User skills |
| `memory/memory.db` | Memory |
| `runtime/runtime.db` | Durable journal |
| `sessions/` | Per-run workspaces and artifacts |

Project starter files: [`models.yaml`](../models.yaml), [`tools.yaml`](../tools.yaml), [`.env.example`](../.env.example).

## Verify

```bash
uv run kageha doctor
uv run kageha models list
uv run kageha chat
```

Next: [USAGE.md](USAGE.md).
