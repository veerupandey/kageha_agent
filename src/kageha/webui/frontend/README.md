# Kageha WebUI (React)

Production React frontend for the Kageha agent WebUI.

**How to start and use the product UI:** see [`docs/WEBUI.md`](../../../../docs/WEBUI.md) (from repo root: `docs/WEBUI.md`).

## Stack

- React 19 + TypeScript + Vite 8
- Zustand store (split under `src/store/`)
- Vitest for unit tests

## Features

- Modes, slash commands, `@` files, Cmd/Ctrl+K
- HITL approvals, Design panel, multitask tabs
- Settings (density, Ask/Auto default, default mode, reduce motion, tool cards)
- Session pin / archive / delete (context menu + buttons)
- Lazy-mounted drawers, error boundary, connection banner
- Stick-to-bottom streaming, skeletons, copy/retry

## Develop

```bash
# From repo root — API
uv run kageha webui --port 8788

# In this directory — HMR
npm run dev
```

## Build / test

```bash
npm run build
npm test
```

Production assets land in `dist/` and are served by `kageha webui` at `/`.
