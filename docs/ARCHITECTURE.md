# Kageha Agent — Architecture

> A durable, multi-model AI agent framework with interactive chat, autonomous job execution, browser control, and a modern web UI.

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           User Interfaces                                │
├──────────────┬──────────────┬──────────────┬────────────────────────────┤
│  CLI (chat)  │   Web UI     │ Telegram bot │  WhatsApp QR / Cloud API   │
│  ─────────── │  (React SPA) │  Bot         │  QR sidecar                │
│  readline /  │  Vite + TS   │              │                            │
│  prompt_toolkit             │              │                            │
└──────┬───────┴──────┬───────┴──────┬───────┴────────────┬───────────────┘
       │              │              │                     │
       ▼              ▼              ▼                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         App Server (HTTP/WS/Unix)                         │
│  JSON-RPC over HTTP · SSE streaming · Session management                 │
│  Thread binding · Turn routing · Approval gateway                        │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Agent Runtime Engine                              │
│                                                                          │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │ Turn Request │  │  Event       │  │  Snapshot    │  │  Run Handle │ │
│  │ Submission   │→ │  Journal     │→ │  Projection  │→ │  (async)    │ │
│  └─────────────┘  │  (SQLite WAL)│  └──────────────┘  └─────────────┘ │
│                    └──────────────┘                                      │
│  Idempotent · Crash-recoverable · Resume from any checkpoint            │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Loop Controller                                   │
│                                                                          │
│  ┌───────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │  Planner  │  │ Executor │  │ Verifier │  │ Monitor  │              │
│  │ (GoalCard │  │ (N tool  │  │ (check   │  │ (stuck / │              │
│  │  + Plan)  │  │  steps)  │  │  goals)  │  │  budget) │              │
│  └───────────┘  └──────────┘  └──────────┘  └──────────┘              │
│                                                                          │
│  Modes: normal (act) · plan (clarify→plan→build) · goal (HITL iterate) │
│  Contract: TaskContract → EvidenceRecord → Verification                  │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          Tool Harness                                     │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ Core Pack          │ Browser Pack    │ Media Pack    │ MCP        │  │
│  │ ─────────────────  │ ──────────────  │ ────────────  │ ────────── │  │
│  │ bash / write_file  │ headless_fetch  │ fal_video     │ External   │  │
│  │ read_file / edit   │ browser_*       │ nano_banana   │ MCP server │  │
│  │ web_search         │ screenshot      │ gemini_tts    │ tools via  │  │
│  │ todo_write/read    │ CDP control     │ download_file │ stdio/SSE  │  │
│  │ skill_run/load     │ Comet/LP/Docker │               │            │  │
│  │ subagent_spawn     │                 │               │            │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  Security: approval gates · seatbelt/bwrap/docker sandbox · risk class  │
│  Policy: HITL for mutations · auto-approve reads · budget ceiling        │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Model Router                                      │
│                                                                          │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐       │
│  │  Gemini    │  │  OpenAI    │  │  Anthropic  │  │ SiliconFlow│       │
│  │  Flash/Pro │  │  GPT-4o    │  │  Claude     │  │  GLM / Qwen│       │
│  └────────────┘  └────────────┘  └────────────┘  └────────────┘       │
│                                                                          │
│  Ladder failover · Retry with backoff · Circuit breaker                  │
│  Provider health tracking · Effort-based model selection                  │
│  Role pins: planner / executor / fast_worker / embedding                 │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                         Memory & Skills                                   │
│                                                                          │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────────────┐   │
│  │ Memory Service   │  │ Skill Registry  │  │ Project Brain        │   │
│  │ ───────────────  │  │ ──────────────  │  │ ──────────────────── │   │
│  │ SQLite FTS5      │  │ YAML skills in  │  │ AGENTS.md / rules /  │   │
│  │ Provenance-aware │  │ ~/.kageha/skills│  │ hooks / worktrees    │   │
│  │ LLM extraction   │  │ Auto-load by    │  │ Context injection    │   │
│  │ Consolidation    │  │ embedding match │  │ per-project config   │   │
│  │ Vector (optional)│  │ Distill from    │  │                      │   │
│  │                  │  │ successful runs │  │                      │   │
│  └─────────────────┘  └─────────────────┘  └──────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                         Persistence Layer                                 │
│                                                                          │
│  ~/.kageha/                                                              │
│  ├── runtime.db         Event journal + snapshots (SQLite WAL)           │
│  ├── memory.db          Memory store (FTS5 + optional vectors)           │
│  ├── sessions/          Workspace dirs per session (artifacts, plans)     │
│  ├── jobs/              Durable async job records (JSON)                  │
│  ├── skills/            User-defined skill YAML + scripts                │
│  ├── tools/             Custom tool pack directories                     │
│  ├── hooks.json         Global lifecycle hooks                           │
│  ├── models.yaml        Model ladder config + role pins                  │
│  └── platforms/         Channel auth state (WhatsApp, OAuth, etc.)       │
│                                                                          │
│  <project>/.kageha/                                                      │
│  ├── hooks.json         Project-scoped hooks                             │
│  ├── rules/             Project rules (auto-injected context)            │
│  └── worktrees/         Agent git worktrees                              │
└─────────────────────────────────────────────────────────────────────────┘
```

## Web UI Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  React SPA (Vite + TypeScript + Tailwind CSS)                │
│                                                              │
│  ┌─────────────┐  ┌─────────────────────┐  ┌────────────┐ │
│  │  AppShell   │  │    ThreadView        │  │  Lightbox  │ │
│  │  ─────────  │  │    ──────────        │  │  ────────  │ │
│  │  Sidebar    │  │  ConversationPanel   │  │  Preview   │ │
│  │  ├ Agents   │  │  ├ MessageList       │  │  Sidebar   │ │
│  │  ├ Threads  │  │  ├ TerminalActivity  │  │  Actions   │ │
│  │  ├ Jobs     │  │  ├ ApprovalBanner    │  └────────────┘ │
│  │  ├ Hooks    │  │  └ MiniComposer      │                  │
│  │  └ Footer   │  │                      │  ┌────────────┐ │
│  └─────────────┘  │  ArtifactPanel (R)   │  │ AgentCanvas│ │
│                    │  ├ File list          │  │ ─────────  │ │
│  ┌─────────────┐  │  ├ Inline preview    │  │ Timeline   │ │
│  │CommandCenter│  │  ├ Code highlighting │  │ TodoBoard  │ │
│  │ ──────────  │  │  └ Live refresh      │  │ Artifacts  │ │
│  │ Hero Input  │  └─────────────────────┘  │ Stats      │ │
│  │ QuickActions│                            └────────────┘ │
│  │ RecentCards │  ┌─────────────────────┐                  │
│  └─────────────┘  │     Composer         │                  │
│                    │  ├ Multiline input   │                  │
│                    │  ├ / slash commands  │                  │
│                    │  ├ @ file mentions   │                  │
│                    │  ├ File attach/paste │                  │
│                    │  ├ Voice input       │                  │
│                    │  └ Mode/Model select │                  │
│                    └─────────────────────┘                  │
│                                                              │
│  State: Zustand store → SSE stream → real-time updates      │
│  Styling: Tailwind CSS 4 + custom theme tokens              │
│  Code: highlight.js + codeEnhancer + jsonRenderer           │
└─────────────────────────────────────────────────────────────┘
```

## Key Flows

### Messaging Channels

Telegram and WhatsApp adapters normalize inbound text and media into a shared
channel message contract. The durable channel queue deduplicates provider
redeliveries, serializes turns per peer, and records outbound delivery attempts.
Telegram currently uses long polling. WhatsApp QR runs in an isolated Node
sidecar; see [CHANNELS.md](CHANNELS.md) for setup and its production limitations.

### Chat Turn (Normal Mode)
```
User message → ensureSession() → classify_turn (deterministic)
  → route: resume | new_run | quick_*
  → build context (memory recall + skill match + system_extra)
  → durable_runtime.submit(TurnRequest)
    → Event journal: ACCEPTED
    → LoopController.run()
      → Model call (with tools)
      → Tool execution (sandboxed)
      → Repeat 0..N steps
      → Verify (early-stop or continue)
    → Event journal: COMPLETED
  → Format reply → Stream to UI
```

### Plan Mode
```
/plan <task> → Clarify phase (ask questions)
  → Plan phase (generate plan.md + GoalCard)
  → User approves (/build)
  → Execute phase (step-by-step with TodoBoard)
  → Verify phase (check all goals pass)
  → Report deliverables
```

### Durable Jobs
```
POST /api/jobs → enqueue_job() → save JSON
  → spawn detached worker process
  → worker: AgentRuntime.submit() in plan mode
  → worker: save progress to job record
  → WebUI: poll /api/jobs or attach via SSE
  → completion: notify channel (webui/telegram/etc.)
```

## Security Model

| Layer | Mechanism |
|-------|-----------|
| Shell isolation | seatbelt (macOS) / bwrap (Linux) / Docker / SSH |
| Tool approval | Risk-class gates: read (auto) → write (HITL) → network (ask) |
| Budget ceiling | Per-session max_usd + max_steps |
| Secrets | .env never committed; redaction in logs/journal |
| Hooks | preToolUse can deny; exit(2) = hard block |
| Sandbox fallback | approval_fallback profile when no OS sandbox |

## Data Contracts

| Entity | Storage | Format |
|--------|---------|--------|
| Sessions | SQLite `sessions` table | id, status, metadata_json |
| Turns | SQLite `turns` table | Immutable event-sourced snapshots |
| Events | SQLite `events` table | Append-only journal (WAL) |
| Tool attempts | SQLite `tool_attempts` | Idempotent; reconcile on crash |
| Memory | SQLite `memory.db` | FTS5 full-text + optional vectors |
| Jobs | `~/.kageha/jobs/*.json` | Flat file per job |
| Hooks | `.kageha/hooks.json` | JSON array of HookSpec |
| Skills | `~/.kageha/skills/*.yaml` | Declarative skill definitions |

## Technology Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Package manager | uv (astral-sh) |
| Web framework | stdlib http.server + asyncio |
| Frontend | React 19 + TypeScript 6 + Vite 8 |
| Styling | Tailwind CSS 4 |
| State | Zustand |
| Database | SQLite (WAL mode, FTS5) |
| Models | Gemini, OpenAI, Anthropic, SiliconFlow, Azure |
| Testing | pytest + vitest |
| Linting | ruff + oxlint + pyright |
| CI | GitHub Actions (matrix: 3 Python × 2 OS + frontend) |
