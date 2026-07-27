# Kageha architecture

Kageha is a **thin agent kernel**. Capabilities grow through **MCP servers**, **Agent Skills**, and **optional tool packs** — not by stuffing every domain into the default process.

To install and run: **[USAGE.md](USAGE.md)**. WebUI: [WEBUI.md](WEBUI.md).

```text
Agent = Model + Trimmed Harness + Plugins(MCP, Skills, optional packs)
```

---

## 1. Design rules

1. **Core stays small.** Default chat loads files, shell, web search, todos, forge, skills, MCP, memory, and subagents.
2. **Domain work is a skill or MCP.** TV remotes, carousels, SaaS connectors, and most creative pipelines do not become harness tool packs.
3. **Loop ≠ harness.** The loop owns plan/act/verify/huddle/stop. The harness owns tools, sandbox, approvals, and model invoke.
4. **One runtime.** Durable execution is journaled and resumable (`runtime`).
5. **Self-depth chat.** No LLM turn classifier. The model chooses 0…N tools; depth rises via `/plan`, `/spec`, `/goal`, or `escalate_plan`.
6. **Deny-by-default risk.** Hard risk classes still need human approval in every mode, including `goal`.

---

## 2. Process shape

```text
Channels / CLI / WebUI / App Server
              │
              ▼
         ModePolicy          normal | plan | spec | goal
              │
              ▼
        LoopController       plan → act → verify → huddle/repair → stop
              │
    ┌─────────┼─────────┬──────────┬──────────┐
    ▼         ▼         ▼          ▼          ▼
 Context   Model     Tool      Skill/MCP   Memory
 Assembler Router   Router     Hub         Service
              │
              ▼
     Session workspace + runtime journal
     Sandbox + activity / approval gates
```

| Layer | Role | Code |
|-------|------|------|
| Channels | User I/O | `channels/`, `chat/`, `webui/`, `cli.py` |
| Mode policy | Depth & clarify rules | `loop/mode_policy.py` |
| Loop | Executive control | `loop/controller.py` |
| Harness | Tools, packs, HITL, sandbox | `harness/` |
| Runtime | Durable journal / resume | `runtime/` |
| Models | Providers + role routing | `models/` |
| Memory | Cross-session authority | `memory/` |
| Skills / MCP | Extensibility | `memory/` skill registry, `mcp/` |
| Subagents | Isolated workers + DAG | `agents/` |
| Devices / creative | Skill libraries only | `devices/`, `creative/` |

---

## 3. Modes

Modes change **policy**, not the codebase. One `LoopController`; mode selects clarify / plan approval / verify / HITL aggressiveness.

| | `normal` | `plan` | `spec` | `goal` |
|---|---|---|---|---|
| Enter | default | `/plan` or escalate | `/spec` | `/goal` |
| Clarify | when blocked | light | required unless complete | when blocked |
| Plan artifact | optional | approve before execute | full plan + skill gaps | living goal card |
| Subagents | rare | optional | DAG by dependency | heavy |
| Verify | sparse | after execute | milestones | continuous |
| Stop | user satisfied | plan done | tasks done | goal SUCCESS |

**Plan** is a gentler **spec**. **Goal** maximizes autonomy toward a falsifiable objective; it never softens hard HITL classes.

Mode resolution order: slash command (`/plan`…) → explicit API/CLI `--mode` → workspace `agent_mode.flag` → `normal`.  
`escalate_plan` may write the flag mid-turn.
Fresh Plan/Spec turns clear a prior `plan_approved.flag` so Build is required again.
---

## 4. Loop

```text
plan → act → verify → { continue | repair | huddle | replan | ask_human | stop }
```

- **Plan** — goal card / milestones / follow-up plan (`loop/planner.py`).
- **Act** — tool calls via harness router; parallel when safe.
- **Verify** — separate from the maker (`loop/verifier.py`).
- **Huddle** — stuck path: diagnose, write forge/skill code, require human confirm before durable side effects (`ControlDecision.HUDDLE`).
- **Stop** — budgets, cancel, deny, no-progress, or goal SUCCESS (`loop/stop_rules.py`).

Chat turns use self-depth: short asks stay shallow (`followup`); plan/spec/goal use the full executive path.

---

## 5. Tool surface

### Always on (builtins + core packs)

**Builtins:** `read_file`, `write_file`, `edit_file`, `list_dir`, `bash`, `todo_*`, `ask_human`, `escalate_plan`, `web_search`, `parallel_web_search`, …

**Core packs** (`harness/tool_packs.py`):

| Pack | Purpose |
|------|---------|
| `forge` | Session-local tool authoring (huddle escape hatch) |
| `skills` | `skill_list` / `load` / `read` / `run` / `manage` / `install` / … |
| `mcp` | Hub + `mcp_<server>_<tool>` |
| `memory` | recall / inspect / remember / correct / forget / explain / fetch / forgotten |
| `subagent` | `spawn_subagent`, `spawn_subagents`, `spawn_task_graph` |
| `research` | Blink `research_run` / `parallel_web_fetch` / `headless_fetch` (see `docs/RESEARCH_BACKEND.md`) |

### Optional packs (opt-in)

`kb`, `browser`, `pdf`, `computer`, `media`, `diagram`, `product_import`, `connections`

Enable with:

```bash
export KAGEHA_TOOL_PACKS=browser,media
# or packs: [browser, media] in tools.yaml
# or KAGEHA_TOOL_PACKS=all
```

Precedence: `KAGEHA_TOOL_PACKS` → `tools.yaml` `packs` → core only.

**Browser / research:** core `web_fetch` + core `research` pack (`research_run` one-shot). Optional `browser` pack for interactive control (`harness/browser/` — AX refs, tabs, lock, CDP). Tier: `research_run(flash)` → headless pool → `browser_*`. See `docs/RESEARCH_BACKEND.md` and `docs/BROWSER_ENGINE.md`.

### Never tool packs

Device control and carousels are **skills + libraries**:

- `kageha.devices` — Bravia, Android TV, LAN scan
- `kageha.creative` — carousel studio helpers
- Bundled skills — `sony_bravia`, `android_tv`, `network_scan`, media/carousel skills via `skill_run`

---

## 6. Skills and MCP

### Agent Skills ([agentskills.io](https://agentskills.io))

Progressive disclosure: list → load body → run scripts → manage/install mid-session.

Search order: bundled → `~/.kageha/skills` → project `.kageha/skills` / `skills/` → `KAGEHA_SKILLS_PATH`.

Procedural learning belongs in **skills**, not silent prompt growth.

### MCP

- **Client:** stdio / SSE / Streamable HTTP from `~/.kageha/mcp.yaml`
- **Serve:** `kageha mcp serve` — **stdio only** (expose Kageha tools to hosts)

Intentionally unsupported today: sampling, elicitation, completions. Rich binary resources are stubbed. Check `mcp_protocol_status`.

---

## 7. Memory

`MemoryService` owns cross-session truth at `~/.kageha/memory/memory.db` (SQLite + FTS). Vectors (zvec) are an optional semantic accelerator, not authority.

- Provenance and scopes (session / channel / project / …)
- Prefer confirmed claims; corrections and forget are first-class
- Compact always-on digest + usage-aware ranking + idle prune
- Turn-start bootstrap (`prepare_turn_memory`): hash-gated rule sync + digest/index into `system_extra`
- Optional LLM extract (union with regex) on verified successful turns
- Consolidate/dream pass: near-dupe collapse, quarantine ageing, `MEMORY.md` index; optional LLM dream (`KAGEHA_MEMORY_LLM_DREAM`, default off)
- Deep-fetch by id + visible forgotten trail
- Import/sync portable project rules (`AGENTS.md` / `CLAUDE.md` / `.cursor/rules`; auto via `KAGEHA_MEMORY_AUTO_SYNC_RULES`)
- Working state for a run lives on the session filesystem (todo, goal, handoff) plus the context window

Kill switch: `KAGEHA_MEMORY_ENABLED=0`. `MEMORY.md` is an offline pointer index — never injected into `SYSTEM_PROMPT`.

---

## 8. Subagents

Workers get **isolated** workspaces (no shared memory). Parents schedule a **dependency DAG** (`spawn_task_graph`); ready nodes run up to a parallel cap. Results are summaries + artifact paths, not giant chat paste. Failed nodes → huddle or replan — never silent goal SUCCESS.

---

## 9. Channels and surfaces

| Surface | Role |
|---------|------|
| `kageha chat` / `run` | Primary CLI |
| WebUI | Browser chat ([docs/WEBUI.md](WEBUI.md)) |
| App Server | JSON-RPC spine for UI/channels |
| Telegram, WhatsApp Cloud, Teams | Default messaging story |
| Discord, Slack, Signal, Matrix, … | Optional channel plugins |
| `kageha gateway` | Optional multi-adapter supervisor |

WhatsApp QR/Baileys is optional; Cloud API is the default path.

---

## 10. Config layout

Home: `KAGEHA_HOME` (default `~/.kageha`).

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

---

## 11. Safety

- Sandbox profiles for shell / computer-use
- Activity gates and approval classes (network, messaging, elevated shell, spendy media, …)
- Auto-approve is an explicit operator choice, never the architecture default for hard risks
- Goal mode may reduce soft confirms; hard classes stay hard

---

## 12. What we do not claim

- Devices or carousels as harness packs
- Optional packs loaded by default
- MCP sampling / elicitation / completions
- MCP HTTP serve (stdio serve only)
- Shared-memory subagents
- LLM chat classifiers

---

## 13. Project brain, hooks, worktrees

Coding-agent parity surfaces (Claude Code / Cursor / Codex patterns):

| Mechanism | Location | Role |
|-----------|----------|------|
| Project instructions | `AGENTS.md` / `KAGEHA.md` / `CLAUDE.md` / `.cursorrules` | Loaded into system context each turn |
| Path rules | `.kageha/rules/*.md` | Always-on or glob-scoped |
| Slash recipes | `.kageha/commands/*.md` | `/project:<name>` / `/cmd <name>` |
| Lifecycle hooks | `.kageha/hooks.json` + `~/.kageha/hooks.json` | `preToolUse` / `postToolUse` / `stop` / … |
| Worktrees | `.kageha/worktrees/` | `isolation=worktree`, `kageha worktree`, `best-of-n` |
| App Server listen | `kageha server --listen` | `stdio://` \| `unix://` \| `ws://127.0.0.1:PORT` |
| Review / babysit | `kageha review`, `kageha babysit` | Diff review + PR check loop |
| Async jobs | `kageha cloud run` | Durable background turns under `~/.kageha/jobs/` |

Code: `src/kageha/project/`.

---

## 14. Growth checklist

Before adding kernel code, it must be one of:

1. Required on **every** turn (loop, safety, memory, skills/MCP meta), or  
2. An **Agent Skill**, or  
3. An **MCP server**, or  
4. An **optional pack** behind `KAGEHA_TOOL_PACKS`.

If (2) or (3) works, do not add a native pack.
