# Kageha WebUI

How to **start** and **use** the browser chat UI.

Architecture: [ARCHITECTURE.md](ARCHITECTURE.md). Full CLI runbook: [USAGE.md](USAGE.md).

---

## Start (production)

From the repo root, with a model key already in `.env`:

```bash
# 1. Python deps + env (once)
uv sync
cp -n .env.example .env   # then set GEMINI_API_KEY (or OpenAI / Anthropic)

# 2. Build the React SPA (once, or after frontend changes)
cd src/kageha/webui/frontend
npm install
npm run build
cd -

# 3. Serve UI + API
uv run kageha webui
# → http://127.0.0.1:8788
```

Open that URL in your browser. Options:

```bash
uv run kageha webui --port 8788
uv run kageha webui --host 127.0.0.1 --port 8788 --open   # open browser
uv run kageha webui -C /path/to/project                   # project root for @ / Labs
```

`/` only works after `npm run build` (assets live in `frontend/dist`, gitignored).

Check providers first if chat fails:

```bash
uv run kageha doctor
uv run kageha models list
```

---

## Start (frontend hot reload)

Two terminals:

```bash
# Terminal A — API
uv run kageha webui --port 8788

# Terminal B — Vite
cd src/kageha/webui/frontend
npm run dev
```

Use the Vite URL Vite prints (usually proxied to the API on 8788).

---

## First five minutes

1. Open **http://127.0.0.1:8788**
2. Type a message in the composer and press **Enter** (Shift+Enter = newline)
3. Watch the transcript stream; approve risky tools when the banner appears
4. Try a mode: type `/plan` then describe a task
5. Open drawers from the UI chrome or slash commands (`/memory`, `/artifacts`)

Sessions are durable under `~/.kageha/sessions/` — you can refresh or reopen mid-run.

---

## UI map

```text
┌─────────────┬──────────────────────────────┬──────────────┐
│ Sessions    │  Transcript + tool cards     │  Stage /     │
│ rail        │  Approvals                   │  Workbench   │
│ (pin/arch)  │                              │  (plan/BoN)  │
├─────────────┴──────────────────────────────┴──────────────┤
│ Composer: mode · Ask/Auto · model · / slash · @ files     │
└───────────────────────────────────────────────────────────┘
  Drawers: Memory · Artifacts · Jobs · Settings · Labs
```

| Area | What it does |
|------|----------------|
| **Sessions rail** | New chat, switch sessions, pin / archive / delete |
| **Transcript** | Messages, streaming, tool cards, activity pulse |
| **Approval banner** | Approve / deny risky tools (Ask mode) |
| **Composer** | Draft, attachments, mode chips, send / stop |
| **Task tabs** | Parallel multitask runs (`/multitask`, `/new`) |
| **Stage / Workbench** | Plan design files, best-of-N, review |
| **Drawers** | Memory search, artifacts, jobs, settings, Labs |

---

## Keyboard & pickers

| Shortcut | Action |
|----------|--------|
| **Enter** | Send |
| **Shift+Enter** | Newline |
| **Cmd/Ctrl+K** | Command palette |
| **/** at start of draft | Slash command picker |
| **@** | Attach project files / session artifacts |
| **Esc** | Close palette / pickers |
| Drag-drop / paste | Attach images and files to the next message |

---

## Slash commands (WebUI)

Type `/` in the composer. Common ones:

| Command | Effect |
|---------|--------|
| `/plan` `/spec` `/goal` `/normal` | Switch agent mode |
| `/ask` / `/auto` | Ask before risky tools vs auto-approve |
| `/permissions` | Show current Ask/Auto mode |
| `/multitask` `/new` `/task` | Open a parallel task tab |
| `/tabs` | Focus multitask tabs |
| `/memory` | Open memory drawer |
| `/artifacts` | Open artifacts drawer |
| `/attach` `/files` | Pick files for this message |
| `/model` | Focus model override |
| `/labs` | Open Project Labs |
| `/best-of-n` | Best-of-N in the stage workbench |
| `/review` | Diff review in the stage workbench |
| `/research flash …` | Blink research (no full agent loop) |
| `/browser` `/browser comet` | Browser backend status / Comet |
| `/computer` … | macOS computer-use (when available) |
| `/comet` | Shortcut when Comet capability is on |

Only WebUI-capable commands appear; CLI-only stubs are omitted.

---

## Modes & approvals

- **Mode chips** on the composer (or `/plan` …) match CLI modes — see [USAGE.md](USAGE.md) §5.
- **Ask** (default for hard risks): banner asks you to approve network, shell, spendy media, etc.
- **Auto** (`/auto`): skips soft confirms; hard classes may still gate depending on server policy.
- Prefer Ask until you trust the workspace.

---

## Attachments & `@`

- Drop or paste files into the composer, or use `/attach`
- Type `@` to fuzzy-search the **project tree** and **session artifacts**
- Project root defaults to the directory you passed with `-C` / cwd when starting `kageha webui`

---

## Labs: best-of-N & review

1. `/labs` or open the Labs drawer  
2. `/best-of-n` — run N parallel attempts, compare in the stage  
3. `/review` — review a diff / PR-style check in the workbench  

These use the project root from `--project` / `-C`.

---

## Optional packs in WebUI

Same env as CLI. Examples:

```bash
# Interactive browser / Comet
uv sync --extra browser && uv run playwright install chromium
export KAGEHA_TOOL_PACKS=browser
uv run kageha webui

# macOS computer-use
./scripts/install_computer_driver.sh
cua-driver permissions grant
uv sync --extra computer
uv run kageha webui
# Opt out: KAGEHA_COMPUTER=0
```

In the UI: `/browser`, `/comet`, `/computer status`, `/computer doctor`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `/` returns build error / blank | `cd src/kageha/webui/frontend && npm install && npm run build` |
| Chat fails / no models | Set a key in `.env`; run `kageha doctor` |
| Approvals never appear | You may be in `/auto`; switch `/ask` |
| `@` finds nothing useful | Start with `-C /path/to/repo`; wait for index warm |
| Browser slash missing | Enable `KAGEHA_TOOL_PACKS=browser` and restart webui |
| Computer slash weak on macOS | Install cua-driver + grant Accessibility / Screen Recording |

---

## Develop / test frontend

```bash
cd src/kageha/webui/frontend
npm test
npm run build
```

Code layout:

| Path | Role |
|------|------|
| `src/kageha/webui/server.py` | HTTP + SSE; serves `frontend/dist` |
| `src/kageha/webui/frontend/` | React + Vite SPA |
| `src/kageha/app_server.py` | JSON-RPC turns / resume / events |

Turns stream over SSE; sessions resume from the runtime journal. Optional native `@` acceleration: see empty `native-index` extra / `KAGEHA_NATIVE_INDEX` (pure Python works by default).

```bash
uv run python scripts/bench_webui_hotpaths.py
```
