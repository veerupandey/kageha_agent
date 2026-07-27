# Kageha

Thin agent kernel: chat, plan, goal, tools — grown through MCP and Agent Skills.

## Docs

| Doc | Purpose |
|-----|---------|
| **[Setup](docs/SETUP.md)** | Install and first run |
| **[Usage](docs/USAGE.md)** | Modes, CLI, WebUI, packs, skills, MCP |

## Quick start

```bash
uv sync
cp .env.example .env   # set a model API key
uv run kageha models list
uv run kageha chat
```

One-shot:

```bash
uv run kageha run "What changed in the last commit?"
```

WebUI:

```bash
cd src/kageha/webui/frontend && npm install && npm run build
uv run kageha webui --open
```

Optional packs:

```bash
export KAGEHA_TOOL_PACKS=browser,computer
```

## What you get

- **Modes:** `normal` · `/plan` · `/goal`
- **Core tools:** files, shell, web search, forge, skills, MCP, memory, subagents, research
- **Optional packs:** `browser`, `computer`
- **Surfaces:** CLI chat / run, WebUI
- **Memory:** SQLite under `~/.kageha/`
- **Runtime:** journaled sessions

## Day-1 commands

| Command | Purpose |
|---------|---------|
| `kageha chat` | Interactive REPL |
| `kageha run "…"` | Single task |
| `kageha webui` | Browser UI |
| `kageha doctor` | Health check |
| `kageha models list` | Providers |
| `kageha skills …` | Skills |
| `kageha mcp …` | MCP |
| `kageha memory …` | Memory |
| `kageha research "…"` | Research |

Full walkthrough: [docs/SETUP.md](docs/SETUP.md) → [docs/USAGE.md](docs/USAGE.md).

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check src tests
```

License: MIT
