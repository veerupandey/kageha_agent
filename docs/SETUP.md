# Setup

Install Kageha and get a working chat or WebUI session.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Optional: Node.js 20+ (only if you use the WebUI)

## Install + guided setup

```bash
cd kageha_agent
uv sync
uv run kageha setup
```

The wizard asks:

1. Where you will use Kageha (Chat CLI, WebUI, or both)
2. Project folder (writes `.env` there)
3. How to connect a model (Gemini, OpenAI, Anthropic, Azure OpenAI, or any OpenAI-compatible endpoint)
4. Optional packs (browser, media/Fal, computer on macOS)
5. Optional smoke test

Then it prints the exact next commands for your choices.

Re-run provider-only later:

```bash
uv run kageha models setup
```

## After setup

```bash
uv run kageha chat
# or, if you chose WebUI:
cd src/kageha/webui/frontend && npm install && npm run build && cd -
uv run kageha webui --open
```

One-shot:

```bash
uv run kageha run "Summarize the last git commit"
```

## Optional extras

Install only what you need (the wizard prints these when relevant):

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

## Optional tool packs

Default install is core only. The wizard can write packs into `.env`:

```bash
# example written by setup
KAGEHA_TOOL_PACKS=browser,media
```

Or in `~/.kageha/tools.yaml`:

```yaml
packs: [browser, computer]
```

| Pack | Purpose |
|------|---------|
| `browser` | Interactive Playwright / Comet control |
| `computer` | macOS desktop automation (cua-driver) |
| `media` | Fal image/video (`FAL_KEY` / `FAL_API_KEY`) |

## Config home

Durable state lives under `KAGEHA_HOME` (default `~/.kageha`):

| Path | Purpose |
|------|---------|
| project `.env` | API keys, packs, budgets |
| `models.yaml` | Providers and role ladders |
| `tools.yaml` | Packs and risk policy |
| `mcp.yaml` | MCP servers |
| `skills/` | User skills |
| `memory/memory.db` | Memory |
| `runtime/runtime.db` | Durable journal |
| `sessions/` | Per-run workspaces and artifacts |

Project starter files: [`models.yaml`](../models.yaml), [`tools.yaml`](../tools.yaml), [`.env.example`](../.env.example).

## Check

```bash
uv run kageha models list
uv run kageha chat
```

Next: [USAGE.md](USAGE.md).
