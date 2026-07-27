# Kageha

**Thin agent kernel.** Plan, act, verify, huddle — then grow power through MCP and Agent Skills, not a fat default install.

```text
Agent = Model + Trimmed Harness + Plugins(MCP, Skills, optional packs)
```

## Docs

| Doc | Purpose |
|-----|---------|
| **[Usage](docs/USAGE.md)** | Install, run, and day-to-day use |
| [Architecture](docs/ARCHITECTURE.md) | Kernel design |
| [WebUI](docs/WEBUI.md) | Browser chat |
| [Research](docs/RESEARCH_BACKEND.md) | Blink research tools |
| [Browser](docs/BROWSER_ENGINE.md) | Interactive browser pack |

## Quick start

```bash
uv sync
cp .env.example .env   # set GEMINI_API_KEY or OPENAI_API_KEY / ANTHROPIC_API_KEY
uv run kageha doctor
uv run kageha chat
```

One-shot:

```bash
uv run kageha run "What changed in the last commit?"
```

WebUI ([full guide](docs/WEBUI.md)):

```bash
cd src/kageha/webui/frontend && npm install && npm run build
uv run kageha webui --open   # http://127.0.0.1:8788
```

In the browser: composer + Enter to send, `/` slash commands, `@` files, **Cmd/Ctrl+K** palette.

Optional packs when needed:

```bash
export KAGEHA_TOOL_PACKS=browser,media
```

Full walkthrough: **[docs/USAGE.md](docs/USAGE.md)**.

## What you get

- **Modes:** `normal` · `/plan` · `/spec` · `/goal`
- **Core tools:** files, shell, web search, forge, skills, MCP, memory, subagents, research
- **Optional packs:** browser, PDF, computer-use, media, KB, … via `KAGEHA_TOOL_PACKS`
- **Skills-first domains:** media, devices (Bravia / Android TV), carousels
- **Surfaces:** CLI chat, WebUI, Telegram, WhatsApp, Teams
- **Memory:** SQLite authority with provenance
- **Runtime:** journaled sessions under `~/.kageha/`

## Day-1 commands

| Command | Purpose |
|---------|---------|
| `kageha chat` | Interactive REPL |
| `kageha run "…"` | Single task |
| `kageha webui` | Browser UI |
| `kageha doctor` | Config / provider health |
| `kageha models list` | Providers and availability |
| `kageha skills …` | List, install, validate skills |
| `kageha mcp …` | Connect or serve MCP |
| `kageha memory …` | Inspect / remember / recall |
| `kageha research "…"` | Blink research (core) |

## Layout

| Path | Role |
|------|------|
| `src/kageha/loop/` | Modes + plan/act/verify/huddle |
| `src/kageha/harness/` | Tools, packs, sandbox, approvals |
| `src/kageha/runtime/` | Durable execution |
| `src/kageha/memory/` | Memory + skill registry |
| `src/kageha/webui/` | React WebUI + HTTP server |
| `src/kageha/mcp/` | MCP client / stdio serve |
| `src/kageha/devices/` | Skill libraries (not packs) |
| `src/kageha/bundled_skills/` | Shipped skills |

Config home: `~/.kageha` (`models.yaml`, `tools.yaml`, `mcp.yaml`, `memory/`, `sessions/`).

## Principles

1. Default process stays core-only.
2. New domains → skill or MCP before native pack.
3. Devices and carousels are never harness packs.
4. Hard HITL classes stay hard in every mode.
5. No LLM chat classifier — self-depth + explicit escalate.

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check src tests
```

License: MIT
