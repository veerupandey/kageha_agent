<div align="center">

# ✦ Kageha

**Durable AI Agent Framework**

Chat, plan, execute — with tools, memory, and multi-model intelligence.

[![CI](https://github.com/veerupandey/kageha_agent/actions/workflows/ci.yml/badge.svg)](https://github.com/veerupandey/kageha_agent/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776ab.svg?logo=python&logoColor=white)](https://python.org)
[![TypeScript](https://img.shields.io/badge/frontend-TypeScript-3178c6.svg?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![uv](https://img.shields.io/badge/pkg-uv-blueviolet.svg)](https://docs.astral.sh/uv/)

</div>

---

## Overview

Kageha is an autonomous agent kernel that combines interactive chat with durable task execution. It plans, writes code, browses the web, generates media, and remembers context across sessions — all orchestrated through a crash-recoverable runtime with multi-model failover.

```
User → Chat / WebUI / Telegram / WhatsApp QR
         │
         ▼
┌─────────────────────────────────────────────────┐
│              Agent Runtime (durable)              │
│  Event journal · Crash recovery · Resume         │
├─────────────────────────────────────────────────┤
│         Loop Controller                          │
│  Plan → Execute → Verify → Report               │
├──────────┬──────────┬──────────┬────────────────┤
│  Tools   │  Models  │  Memory  │  Skills + MCP  │
│  bash    │  Gemini  │  FTS5    │  YAML skills   │
│  files   │  OpenAI  │  vectors │  MCP servers   │
│  browser │  Claude  │  learn   │  auto-distill  │
│  media   │  GLM     │  recall  │  tool packs    │
└──────────┴──────────┴──────────┴────────────────┘
```

## Features

| Category | What you get |
|----------|-------------|
| **Modes** | `normal` (act immediately) · `/plan` (clarify → plan → build) · `/goal` (iterate with HITL) |
| **Tools** | Shell, file I/O, web search, browser, media generation, code execution |
| **Models** | Gemini, OpenAI, Anthropic, SiliconFlow, Azure — with ladder failover and retry |
| **Memory** | Provenance-aware SQLite FTS5; learns from successful runs; optional vectors |
| **Runtime** | Event-sourced journal, crash recovery, resume from checkpoint, budget ceiling |
| **Security** | OS sandbox (seatbelt/bwrap/docker), tool approval gates, risk-class policies |
| **Web UI** | React SPA with live streaming, artifact preview, syntax highlighting, jobs panel |
| **Channels** | CLI, WebUI, Telegram (polling), WhatsApp QR (experimental), WhatsApp Cloud API (planned) |
| **Skills** | Declarative YAML; auto-loaded by embedding match; distilled from successful runs |
| **MCP** | Connect external MCP tool servers via stdio/SSE |
| **Jobs** | Background durable execution; spawn, cancel, attach from UI or CLI |
| **Hooks** | Lifecycle automation: preToolUse, postCommit, sessionStart, and more |

## Quick Start

```bash
# Install
uv sync

# First-time setup (API keys, model selection, .env)
uv run kageha setup

# Interactive chat
uv run kageha chat

# Single task
uv run kageha run "Explain the architecture of this repo"

# Web UI
uv run kageha webui
```

### Messaging channels

Telegram uses Bot API long polling. WhatsApp QR is an experimental local
companion-device integration and requires Node.js plus the sidecar dependencies.
See [docs/CHANNELS.md](docs/CHANNELS.md) for setup, allowlists, media support,
background startup, lifecycle commands, and security limitations.

```bash
# Telegram
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_ALLOWED_USERS="123456789"
uv run kageha channels run --telegram

# WhatsApp QR (experimental)
cd integrations/whatsapp-qr && npm install && cd ../..
export WHATSAPP_QR_ENABLED=1
export WHATSAPP_QR_ALLOWED_USERS="15551234567"
uv run kageha webui  # starts the configured channel in the background
```

Use `uv run kageha channels status` and `uv run kageha channels stop` for a
supervised listener. A one-shot `kageha run` never starts messaging channels.

## Architecture

> Full details: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

```
┌────────────────────────────────────────────────────────────────┐
│  Interfaces: CLI · WebUI · Telegram · WhatsApp QR · API         │
└───────────────────────────┬────────────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────────────┐
│  App Server (HTTP + SSE + Unix socket)                          │
│  Session management · Thread binding · Approval gateway        │
└───────────────────────────┬────────────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────────────┐
│  Agent Runtime Engine                                           │
│  SQLite WAL journal · Idempotent events · Crash recovery       │
└───────────────────────────┬────────────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────────────┐
│  Loop Controller                                                │
│  Planner · Executor (0..N tool steps) · Verifier · Monitor     │
└──────┬─────────────┬──────────────┬────────────────────────────┘
       ▼             ▼              ▼
┌────────────┐ ┌──────────┐ ┌─────────────────────────────────┐
│ Tool Packs │ │  Models   │ │ Memory · Skills · Project Brain │
│ core/browser│ │ multi-LLM│ │ FTS5 · YAML · AGENTS.md         │
│ media/MCP  │ │ failover │ │ hooks · worktrees               │
└────────────┘ └──────────┘ └─────────────────────────────────┘
```

## Modes

| Mode | Behavior | Use for |
|------|----------|---------|
| **Normal** | Immediate action, 0–N tool steps | Quick tasks, Q&A, file edits |
| **Plan** | Clarify → generate plan.md → approve → execute | Complex multi-step work |
| **Goal** | HITL iteration with verified goal completion | Open-ended objectives |

## CLI Commands

| Command | Purpose |
|---------|---------|
| `kageha setup` | First-run wizard |
| `kageha chat` | Interactive multi-turn REPL |
| `kageha run "…"` | One-shot task execution |
| `kageha webui` | Launch browser UI |
| `kageha server` | App server (for WebUI backend) |
| `kageha channels status` | Show channel listener ownership |
| `kageha channels stop` | Stop a supervised channel listener |
| `kageha models list` | Available model providers |
| `kageha skills list` | Installed skills |
| `kageha memory status` | Memory store info |
| `kageha jobs list` | Background job status |
| `kageha mcp list` | Connected MCP servers |
| `kageha doctor` | System health check |

## Web UI

The WebUI is a React SPA with:
- Real-time streaming via SSE
- Syntax-highlighted code artifacts with inline preview
- Collapsible sidebars, Jobs panel, Hooks panel
- Live TodoBoard during plan execution
- File browser with drag & drop
- Dark/light theme, keyboard shortcuts (⌘K, ⌘N, ⌘B)

```bash
# Development
cd src/kageha/webui/frontend
npm install
npm run dev          # Vite on :5173 (proxies API to :8788)

# Production build
npm run build        # outputs to dist/
```

## Development

```bash
# Install all dependencies
uv sync --all-extras --dev

# Run tests
uv run pytest -q

# Lint + format
uv run ruff check .
uv run ruff format .

# Type check
uv run pyright

# Frontend tests
cd src/kageha/webui/frontend && npm run test
```

## Documentation

| Doc | Purpose |
|-----|---------|
| [docs/SETUP.md](docs/SETUP.md) | Installation and first run |
| [docs/USAGE.md](docs/USAGE.md) | Modes, CLI, WebUI, packs, skills, MCP |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Full system design and data flows |

## License

[MIT](LICENSE) © Rakesh Pandey
