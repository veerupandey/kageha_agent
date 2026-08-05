"""HTTP Web UI over Kageha AppServer + memory/runtime stores.

Serves the React (Vite) SPA from ``frontend/dist`` plus thin REST wrappers
around existing AppServer JSON-RPC methods. Does not invent a parallel memory
store.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import re
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urlparse

from kageha.app_server import AppServer
from kageha.memory.models import MemoryKind, MemoryScope, MemoryState

# Vite React build (`npm run build` in frontend/). Required for `/`.
REACT_DIST_DIR = Path(__file__).resolve().parent / "frontend" / "dist"
EPISODIC_KIND = "episodic"

# UI-facing kind catalog: product MemoryKind values + episodic (EpisodeRecord).
MEMORY_KINDS = [item.value for item in MemoryKind] + [EPISODIC_KIND]
MEMORY_STATES = [item.value for item in MemoryState]
MEMORY_SCOPES = [item.value for item in MemoryScope]

# Uploads / session file serving
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".avif", ".ico", ".tif", ".tiff", ".heic", ".heif"}
_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v"}
_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".aac", ".flac"}
_MARKDOWN_EXTS = {".md", ".markdown"}
_TEXT_DOC_EXTS = {".txt", ".text", ".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".yaml", ".yml", ".toml", ".xml", ".ini", ".cfg", ".conf", ".log", ".tex", ".srt", ".vtt"}
_PDF_EXTS = {".pdf"}
_OFFICE_EXTS = {".ppt", ".pptx", ".pps", ".ppsx", ".odp", ".key", ".doc", ".docx", ".rtf", ".odt", ".pages", ".xls", ".xlsx", ".xlsm", ".ods", ".numbers"}
_ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}
_MEDIA_EXTS = _IMAGE_EXTS | _VIDEO_EXTS | _AUDIO_EXTS
_DESIGN_ARTIFACT_NAMES = frozenset(
    {"plan.md", "requirements.md", "skill_gaps.md", "explore_notes.md"}
)
# User-editable plan design files (session workspace only; not project code).
_DESIGN_EDITABLE_NAMES = frozenset({"plan.md", "explore_notes.md"})
_MAX_DESIGN_FILE_CHARS = 80_000

_ARTIFACT_SKIP_DIRS = frozenset(
    {
        "_memory",
        "_turns",
        "checkpoints",
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".turbo",
        ".next",
        "dist",
        "build",
        "target",
        ".kageha-tmp",
    }
)
_ARTIFACT_SKIP_NAMES = frozenset(
    {
        "session.json",
        "chat.jsonl",
        "plan.json",
        "goal_card.json",
        "task_state.json",
        "events.jsonl",
        "result.md",
        "todo.md",
        ".DS_Store",
    }
)
_ARTIFACT_PREFERRED_PREFIXES = (
    "artifacts/",
    "outputs/",
    "diagrams/",
    "research/",
    "slides/",
    "inputs/",
    "carousel/",
)
# When agents write under project_root, WebUI still needs these in the session.
_DELIVERABLE_EXTS = _OFFICE_EXTS | _PDF_EXTS | _ARCHIVE_EXTS | _MEDIA_EXTS | {
    ".html",
    ".htm",
}
_DELIVERABLE_NAME_RE = re.compile(
    r"(?:^|[\s`\"'(])((?:artifacts|outputs|diagrams|research|slides|carousel)/"
    r"[A-Za-z0-9._\-/]+\.[A-Za-z0-9]+|"
    r"[A-Za-z0-9][A-Za-z0-9._-]*\.(?:pptx?|pdf|docx?|xlsx?|zip|png|jpe?g|webp|gif|"
    r"mp4|webm|mov|wav|mp3|m4a|ogg|html?))"
    r"(?:$|[\s`\"'),.\]])",
    re.I | re.M,
)


class RpcError(RuntimeError):
    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.detail = detail or {}


def react_dist_root() -> Path | None:
    """Return the Vite build dir when a production bundle is present."""
    if (REACT_DIST_DIR / "index.html").is_file():
        return REACT_DIST_DIR.resolve()
    return None


def _json_bytes(payload: Any, *, status: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(payload, default=str).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


def _error(message: str, *, status: int = 400, **extra: Any) -> tuple[int, bytes, str]:
    payload = {"error": message, **extra}
    return _json_bytes(payload, status=status)


def _react_missing() -> tuple[int, bytes, str]:
    return _error(
        "React UI not built. Run: cd src/kageha/webui/frontend && npm run build "
        "(or use `npm run dev` on :5173 during development).",
        status=503,
    )


def _safe_filename(name: str) -> str:
    base = Path(name or "upload.bin").name
    base = re.sub(r"[^\w.\-]+", "_", base).strip("._")
    if not base:
        base = "upload.bin"
    return base[:180]


def _sse_bytes(event: str, data: dict[str, Any]) -> bytes:
    """Encode one Server-Sent Event frame."""
    payload = json.dumps(data, default=str, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _mode_slash_description(mode: str) -> str:
    try:
        from kageha.loop.mode_policy import MODE_CHIP_DESCRIPTIONS

        return MODE_CHIP_DESCRIPTIONS.get(mode, f"{mode.capitalize()} mode")
    except Exception:  # noqa: BLE001
        fallback = {
            "plan": "Plan — clarify, research, then Build",
            "goal": "Goal — execute now with HITL when needed",
            "normal": "Normal mode — standard chat",
        }
        return fallback.get(mode, f"{mode.capitalize()} mode")


# WebUI-honor slash entries (never CLI-only stubs that 404).
_WEBUI_SLASH_BASE: tuple[dict[str, str], ...] = (
    {
        "id": "plan",
        "label": "/plan",
        "description": _mode_slash_description("plan"),
        "kind": "mode",
        "title": "Plan",
    },
    {
        "id": "goal",
        "label": "/goal",
        "description": _mode_slash_description("goal"),
        "kind": "mode",
        "title": "Goal",
    },
    {
        "id": "normal",
        "label": "/normal",
        "description": _mode_slash_description("normal"),
        "kind": "mode",
        "title": "Normal",
    },
    {
        "id": "multitask",
        "label": "/multitask",
        "description": "Open parallel tab (keeps composer attachments)",
        "kind": "multitask",
        "title": "Multitask",
    },
    {
        "id": "new",
        "label": "/new",
        "description": "Multitask · next send opens parallel tab",
        "kind": "multitask",
        "title": "Multitask",
    },
    {
        "id": "task",
        "label": "/task",
        "description": "Multitask · same as /new",
        "kind": "multitask",
        "title": "Multitask",
    },
    {
        "id": "tabs",
        "label": "/tabs",
        "description": "Focus parallel task tabs",
        "kind": "multitask",
        "title": "Tabs",
    },
    {
        "id": "ask",
        "label": "/ask",
        "description": "Ask before risky tools",
        "kind": "prefs",
    },
    {
        "id": "auto",
        "label": "/auto",
        "description": "Auto-approve risky tools",
        "kind": "prefs",
    },
    # CLI /permissions maps to WebUI ask/auto/full.
    {
        "id": "permissions",
        "label": "/permissions",
        "description": "Show ask/auto/full tool approval mode",
        "kind": "prefs",
    },
    {
        "id": "permissions-ask",
        "label": "/permissions ask",
        "description": "Ask before risky tools",
        "kind": "prefs",
    },
    {
        "id": "permissions-auto",
        "label": "/permissions auto",
        "description": "Auto-approve risky tools",
        "kind": "prefs",
    },
    {
        "id": "permissions-full",
        "label": "/permissions full",
        "description": "Auto-approve + sandbox network",
        "kind": "prefs",
    },
    {
        "id": "attach",
        "label": "/attach",
        "description": "Attach files from disk (or drop / paste in composer)",
        "kind": "prefs",
    },
    {
        "id": "files",
        "label": "/files",
        "description": "Same as /attach — pick files for this message",
        "kind": "prefs",
    },
    {
        "id": "artifacts",
        "label": "/artifacts",
        "description": "Open canvas for images, video, PDFs, and files",
        "kind": "prefs",
    },
    {
        "id": "model",
        "label": "/model",
        "description": "Focus model override (list via /api/models)",
        "kind": "prefs",
    },
)

_WEBUI_SLASH_BROWSER: tuple[dict[str, str], ...] = (
    {
        "id": "browser",
        "label": "/browser",
        "description": "Browser backend status / select",
        "kind": "browser",
    },
    {
        "id": "browser-list",
        "label": "/browser list",
        "description": "List browser backends",
        "kind": "browser",
    },
    {
        "id": "browser-comet",
        "label": "/browser comet",
        "description": "Use logged-in Comet CDP",
        "kind": "browser",
    },
    {
        "id": "browser-lightpanda",
        "label": "/browser lightpanda",
        "description": "Fast Lightpanda headless CDP",
        "kind": "browser",
    },
    {
        "id": "browser-chromium",
        "label": "/browser chromium",
        "description": "Warm Chromium headless pool",
        "kind": "browser",
    },
    {
        "id": "browser-headless",
        "label": "/browser headless",
        "description": "Interactive headless Chromium",
        "kind": "browser",
    },
    {
        "id": "research",
        "label": "/research",
        "description": "Blink research (native, no LLM loop)",
        "kind": "browser",
    },
    {
        "id": "research-flash",
        "label": "/research flash",
        "description": "Research · HTTP flash depth",
        "kind": "browser",
    },
    {
        "id": "research-standard",
        "label": "/research standard",
        "description": "Research · headless JS enrich",
        "kind": "browser",
    },
)

_WEBUI_SLASH_COMPUTER: tuple[dict[str, str], ...] = (
    {
        "id": "computer",
        "label": "/computer",
        "description": "Computer-use skill · type a task after",
        "kind": "skill",
        "title": "computer_use",
    },
    {
        "id": "computer-status",
        "label": "/computer status",
        "description": "Pack + driver + allowlist status",
        "kind": "computer",
    },
    {
        "id": "computer-doctor",
        "label": "/computer doctor",
        "description": "Driver + TCC + tool model probe",
        "kind": "computer",
    },
    {
        "id": "computer-pack-on",
        "label": "/computer pack on",
        "description": "Force-enable computer pack",
        "kind": "computer",
    },
    {
        "id": "computer-pack-off",
        "label": "/computer pack off",
        "description": "Disable computer pack",
        "kind": "computer",
    },
    {
        "id": "computer-pack-auto",
        "label": "/computer pack auto",
        "description": "Auto-enable when cua-driver present",
        "kind": "computer",
    },
    {
        "id": "computer-allowlist",
        "label": "/computer allowlist",
        "description": "List per-app allow decisions",
        "kind": "computer",
    },
)


def _webui_slash_catalog(*, project_root: str | None = None) -> dict[str, Any]:
    """Build WebUI-capable slash catalog (capability-gated; no stub 404s)."""
    commands: list[dict[str, Any]] = [dict(c) for c in _WEBUI_SLASH_BASE]
    # Same process serves these routes — include when available.
    commands.extend(dict(c) for c in _WEBUI_SLASH_BROWSER)
    commands.append(
        {
            "id": "comet",
            "label": "/comet",
            "description": "Logged-in browser · start / status",
            "kind": "browser",
        }
    )
    commands.extend(dict(c) for c in _WEBUI_SLASH_COMPUTER)

    # Explicit skill invocations (/skill-name).
    try:
        from kageha.memory.skills import SkillRegistry

        for skill in sorted(SkillRegistry().skills.values(), key=lambda s: s.name):
            desc = (skill.description or skill.name).strip()
            if len(desc) > 100:
                desc = desc[:99].rstrip() + "…"
            suffix = ""
            if skill.disable_model_invocation:
                suffix = " · manual"
            commands.append(
                {
                    "id": f"skill-{skill.name}",
                    "label": f"/{skill.name}",
                    "description": f"Skill{suffix} · {desc}",
                    "kind": "skill",
                    "title": skill.name,
                }
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[kageha-webui] slash-catalog skills failed: {exc}")

    capabilities = {
        "comet": True,
        "browser": True,
        "computer": True,
        "models": True,
        "permissions": True,  # aliased to ask/auto in WebUI
        "memory": True,
        "skills": True,
    }
    return {
        "ok": True,
        "commands": commands,
        "capabilities": capabilities,
    }


def _clip(text: str, *, limit: int = 140) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


_ACTIVE_TURN_STATUSES = {
    "running",
    "accepted",
    "planning",
    "executing",
    "waiting_approval",
    "verifying",
    "repairing",
}
_TERMINAL_TURN_STATUSES = {
    "success",
    "ok",
    "completed",
    "failed",
    "cancelled",
    "blocked",
    "error",
}


def _turn_is_active(status: Any, phase: Any = None) -> bool:
    st = str(status or "").strip().lower()
    ph = str(phase or "").strip().lower()
    if st in _TERMINAL_TURN_STATUSES or ph in {
        "completed",
        "failed",
        "cancelled",
        "blocked",
    }:
        return False
    return st in _ACTIVE_TURN_STATUSES or ph in _ACTIVE_TURN_STATUSES or st == "running"


def _artifact_file_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _AUDIO_EXTS:
        return "audio"
    if ext in _MARKDOWN_EXTS:
        return "markdown"
    if ext in _TEXT_DOC_EXTS:
        return "text"
    if ext in _PDF_EXTS:
        return "pdf"
    if ext in {".ppt", ".pptx"}:
        return "presentation"
    if ext in {".doc", ".docx"}:
        return "document"
    if ext in {".xls", ".xlsx"}:
        return "spreadsheet"
    if ext in _ARCHIVE_EXTS:
        return "archive"
    return "file"


def _sniff_image_mimetype(data: bytes) -> str | None:
    """Detect image MIME from magic bytes (Gemini often writes JPEG as .png)."""
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 6 and data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        # HEIC/HEIF — browsers rarely preview, but label correctly.
        brand = data[8:12]
        if brand in {b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis"}:
            return "image/heic"
    return None


def _session_file_mimetype(path: Path, data: bytes | None = None) -> str:
    # Prefer sniffed type for common mislabeled media (e.g. .png that is JPEG).
    sample = data
    if sample is None:
        try:
            with path.open("rb") as fh:
                sample = fh.read(32)
        except OSError:
            sample = b""
    sniffed = _sniff_image_mimetype(sample or b"")
    if sniffed:
        return sniffed
    ctype, _ = mimetypes.guess_type(str(path))
    if ctype:
        return ctype
    ext = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
        ".m4v": "video/x-m4v",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".aac": "audio/aac",
        ".flac": "audio/flac",
        ".pdf": "application/pdf",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pptx": (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
        ".doc": "application/msword",
        ".docx": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".md": "text/markdown; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
        ".json": "application/json",
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".svg": "image/svg+xml",
        ".zip": "application/zip",
    }.get(ext, "application/octet-stream")


def _artifact_paths_from_result(result: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for item in result.get("artifacts") or []:
        if isinstance(item, dict):
            path = str(item.get("path") or "").strip()
        elif isinstance(item, str):
            path = item.strip()
        else:
            continue
        if path:
            paths.append(path)
    return paths


def _plan_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("plan", "plan_steps", "steps"):
        rows = data.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


# Short pulse labels for computer_* — keep activity strip snappy in WebUI.
_COMPUTER_TOOL_LABELS = {
    "computer_doctor": "Checking desktop…",
    "computer_launch": "Opening app…",
    "computer_wait": "Waiting…",
    "computer_list_apps": "Listing apps…",
    "computer_get_state": "Reading UI…",
    "computer_click": "Clicking…",
    "computer_click_sequence": "Clicking…",
    "computer_set_value": "Typing…",
    "computer_type": "Typing…",
    "computer_key": "Typing…",
    "computer_hotkey": "Typing…",
    "computer_scroll": "Scrolling…",
    "computer_screenshot": "Capturing…",
    "computer_move": "Moving…",
}

# Human-readable labels for common non-computer tools (pulse + Activity).
_TOOL_LABELS = {
    "read_file": "Reading file…",
    "write_file": "Writing file…",
    "edit_file": "Editing file…",
    "list_dir": "Listing directory…",
    "glob": "Finding files…",
    "grep": "Searching code…",
    "search": "Searching…",
    "web_search": "Searching the web…",
    "web_fetch": "Fetching page…",
    "browser": "Using browser…",
    "browser_navigate": "Opening page…",
    "browser_click": "Clicking in browser…",
    "browser_type": "Typing in browser…",
    "browser_snapshot": "Reading page…",
    "shell": "Running shell…",
    "bash": "Running shell…",
    "run_terminal_cmd": "Running shell…",
    "memory_search": "Searching memory…",
    "memory_write": "Saving memory…",
    "memory_read": "Reading memory…",
    "todo_write": "Updating todos…",
    "todo_read": "Reading todos…",
    "ask_human": "Waiting for your answer…",
    "request_approval": "Requesting approval…",
    "research": "Researching…",
    "deep_research": "Deep research…",
}

# Mutations / launches show in Activity so users can see desktop work happened.
# High-frequency observe tools (get_state, wait, list) stay pulse-only.
_COMPUTER_ACTIVITY_TOOLS = frozenset(
    {
        "computer_launch",
        "computer_click",
        "computer_click_sequence",
        "computer_set_value",
        "computer_type",
        "computer_key",
        "computer_hotkey",
        "computer_scroll",
        "computer_screenshot",
        "computer_move",
    }
)

# Kinds that should not fan out a second SSE `status` frame (pulse already updated).
_STATUS_SKIP_KINDS = frozenset(
    {
        "tool_completed",
        "checkpoint",
        "task_state",
        "context",
        "tool_result",
    }
)

# Keep only UI-useful keys on the wire (full journal stays in runtime store).
_SSE_PAYLOAD_KEEP: dict[str, frozenset[str]] = {
    "tool_started": frozenset(
        {
            "tool",
            "tool_name",
            "side_effect",
            "attempt_id",
            "args_preview",
            "status",
            "tool_card",
        }
    ),
    "tool_completed": frozenset(
        {
            "tool",
            "tool_name",
            "state",
            "attempt_id",
            "args_preview",
            "status",
            "duration_ms",
            "artifact_refs",
            "tool_card",
            "computer_frame",
        }
    ),
    "computer_frame": frozenset(
        {"path", "thumb_path", "app", "action", "thumb_url"}
    ),
    "approval_required": frozenset(
        {"approval_id", "action", "detail", "risk_class", "question"}
    ),
    "approval_resolved": frozenset({"approval_id", "approved"}),
    "todo_board": frozenset({"label", "done", "total", "items"}),
}

_SSE_DROP_KEYS = frozenset(
    {
        "snapshot",
        "tree_markdown",
        "elements",
        "readings",
        "base64",
        "image",
        "screenshot_b64",
        "screenshot_png",
        "arguments",
        "arguments_json",
        "result",
        "content",
        "preview",
    }
)

# Live computer_frame fan-out throttle (≤5 fps). Journal still keeps all milestones.
_COMPUTER_FRAME_MIN_INTERVAL_S = 0.2


def _tool_card_from_payload(
    kind: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    """Normalize tool_card for WS3 (tolerant of flat or nested journal fields)."""
    if kind not in {"tool_started", "tool_completed", "tool_result"}:
        return None
    existing = data.get("tool_card") if isinstance(data.get("tool_card"), dict) else {}
    name = str(
        existing.get("name")
        or data.get("tool")
        or data.get("tool_name")
        or data.get("name")
        or ""
    ).strip()
    if not name:
        return None
    status = str(
        existing.get("status")
        or data.get("status")
        or ("running" if kind == "tool_started" else data.get("state") or "ok")
    ).strip() or ("running" if kind == "tool_started" else "ok")
    args_preview = _clip(
        str(existing.get("args_preview") or data.get("args_preview") or ""),
        limit=120,
    )
    duration = existing.get("duration_ms", data.get("duration_ms"))
    if duration is not None:
        try:
            duration = round(float(duration), 1)
        except (TypeError, ValueError):
            duration = None
    refs_raw = existing.get("artifact_refs", data.get("artifact_refs"))
    refs: list[str] = []
    if isinstance(refs_raw, list):
        for item in refs_raw[:8]:
            rel = str(item or "").replace("\\", "/").strip()
            if rel:
                refs.append(rel)
    attempt_id = str(
        existing.get("attempt_id") or data.get("attempt_id") or ""
    ).strip()
    card = {
        "name": name,
        "args_preview": args_preview,
        "status": status,
        "duration_ms": duration,
        "artifact_refs": refs,
    }
    if attempt_id:
        card["attempt_id"] = attempt_id
    return card


def _computer_frame_from_payload(
    kind: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    """Normalize computer_frame path refs (never ship base64 / AX)."""
    if kind == "computer_frame":
        frame = data
    else:
        frame = data.get("computer_frame")
    if not isinstance(frame, dict):
        return None
    path = str(frame.get("path") or frame.get("thumb_path") or "").replace(
        "\\", "/"
    ).strip()
    thumb = str(frame.get("thumb_path") or frame.get("thumb") or "").replace(
        "\\", "/"
    ).strip()
    if not path and not thumb:
        return None
    out: dict[str, Any] = {
        "path": path or thumb,
        "thumb_path": thumb,
        "app": _clip(str(frame.get("app") or ""), limit=80),
        "action": _clip(str(frame.get("action") or data.get("tool") or ""), limit=80),
    }
    # Optional prebuilt URL for session file serving (WS3 may also build it).
    thumb_url = str(frame.get("thumb_url") or "").strip()
    if thumb_url:
        out["thumb_url"] = thumb_url
    return out


def _attach_computer_thumb_url(
    frame: dict[str, Any], session_id: str
) -> dict[str, Any]:
    """Ensure WS3 can fetch the strip image via session files API."""
    if not isinstance(frame, dict):
        return frame
    if str(frame.get("thumb_url") or "").strip():
        return frame
    sid = str(session_id or "").strip()
    if not sid or not _SAFE_SESSION_ID.fullmatch(sid):
        return frame
    rel = str(frame.get("thumb_path") or frame.get("path") or "").replace(
        "\\", "/"
    ).strip().lstrip("/")
    if not rel:
        return frame
    encoded = "/".join(quote(part, safe="") for part in rel.split("/") if part)
    out = dict(frame)
    out["thumb_url"] = f"/api/sessions/{sid}/files/{encoded}"
    return out


def _computer_tool_label(tool: str) -> str | None:
    name = str(tool or "").strip()
    if not name:
        return None
    if name in _COMPUTER_TOOL_LABELS:
        return _COMPUTER_TOOL_LABELS[name]
    if name.startswith("computer_"):
        return "Using desktop…"
    return None


def _tool_human_label(tool: str) -> str:
    """Friendly running-label for any tool name."""
    name = str(tool or "").strip() or "tool"
    computer = _computer_tool_label(name)
    if computer:
        return computer
    if name in _TOOL_LABELS:
        return _TOOL_LABELS[name]
    if name.startswith("browser_"):
        return "Using browser…"
    if name.startswith("memory_"):
        return "Using memory…"
    pretty = name.replace("_", " ").strip()
    return f"Running {pretty}…"


def _sse_payload_view(kind: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Trim event payloads for SSE/WebUI — never ship AX dumps or base64."""
    data = payload if isinstance(payload, dict) else {}
    keep = _SSE_PAYLOAD_KEEP.get(kind)
    if keep is not None:
        return {key: data[key] for key in keep if key in data}
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key in _SSE_DROP_KEYS:
            continue
        if isinstance(value, str) and len(value) > 400:
            out[key] = _clip(value, limit=240)
            continue
        if isinstance(value, (list, dict)) and len(json.dumps(value, default=str)) > 800:
            continue
        out[key] = value
    return out


def _stream_event_view(
    kind: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Map runtime events to a compact label + expandable detail lines."""
    data = payload or {}
    details: list[str] = []

    if kind == "accepted":
        objective = _clip(str(data.get("objective") or ""), limit=160)
        if objective:
            details.append(f"Task: {objective}")
        max_steps = data.get("max_steps")
        if max_steps:
            details.append(f"Budget: up to {max_steps} steps")
        max_usd = data.get("max_usd")
        if max_usd not in (None, "", 0, 0.0):
            details.append(f"Spend cap: ${max_usd}")
        label = "Accepted" if objective else "Turn accepted"
        return {"label": label, "detail": details, "interesting": True}

    if kind == "planning_started":
        task = _clip(
            str(data.get("turn_task") or data.get("task") or ""), limit=160
        )
        if task:
            details.append(f"Task: {task}")
        agent_mode = str(data.get("agent_mode") or "").strip()
        loop_mode = str(data.get("loop_mode") or "").strip()
        if agent_mode:
            details.append(f"Agent: {agent_mode}")
        if loop_mode:
            details.append(f"Loop: {loop_mode}")
        effort = str(data.get("effort") or "").strip()
        if effort:
            details.append(f"Effort: {effort}")
        if data.get("fresh_turn"):
            details.append("Fresh turn")
        label = (
            f"Planning ({agent_mode})…"
            if agent_mode and agent_mode != "normal"
            else "Planning…"
        )
        return {"label": label, "detail": details, "interesting": True}

    if kind == "planned":
        plan_rows = _plan_rows(data)
        goals = data.get("goals") if isinstance(data.get("goals"), list) else []
        source = str(data.get("source") or "").strip()
        stage = str(data.get("current_stage") or "").strip()
        agent_mode = str(data.get("agent_mode") or "").strip()
        if agent_mode:
            details.append(f"Agent: {agent_mode}")
        if source:
            details.append(f"Planner: {source}")
        if stage:
            details.append(f"Stage: {stage}")
        for idx, row in enumerate(plan_rows[:6], start=1):
            title = _clip(
                str(
                    row.get("description")
                    or row.get("title")
                    or row.get("id")
                    or f"step {idx}"
                ),
                limit=120,
            )
            tools = row.get("tools") if isinstance(row.get("tools"), list) else []
            tool_bits = ", ".join(str(t) for t in tools[:4] if t)
            line = f"{idx}. {title}"
            if tool_bits:
                line += f" · tools: {tool_bits}"
            details.append(line)
        for goal in goals[:4]:
            if not isinstance(goal, dict):
                continue
            desc = _clip(
                str(goal.get("description") or goal.get("id") or ""),
                limit=100,
            )
            if desc:
                details.append(f"Goal: {desc}")
        plan_md = str(data.get("plan_md") or "").strip()
        if plan_md:
            details.append("--- plan.md ---")
            for line in plan_md.splitlines()[:24]:
                details.append(line)
            if plan_md.count("\n") >= 24:
                details.append("…")
        mode_bit = f" · {agent_mode}" if agent_mode and agent_mode != "normal" else ""
        label = (
            f"Plan ready{mode_bit} · {len(plan_rows)} step"
            f"{'' if len(plan_rows) == 1 else 's'}"
            if plan_rows
            else f"Plan ready{mode_bit}…"
        )
        return {"label": label, "detail": details, "interesting": True}

    if kind == "tool_started":
        tool = str(data.get("tool") or data.get("tool_name") or "tool")
        computer_label = _computer_tool_label(tool)
        card = _tool_card_from_payload(kind, data)
        preview = ""
        if card is not None:
            preview = str(card.get("args_preview") or "")
        elif data.get("args_preview"):
            preview = _clip(str(data.get("args_preview") or ""), limit=120)
        if preview and not computer_label:
            details.append(preview)
        side = str(data.get("side_effect") or "").strip()
        if side and not computer_label:
            details.append(f"Side-effect class: {side}")
        grant = str(data.get("policy_grant") or "").strip()
        if grant and not computer_label:
            details.append(f"Policy: {grant}")
        if computer_label:
            name = str(tool or "")
            show = name in _COMPUTER_ACTIVITY_TOOLS
            out = {
                "label": computer_label,
                "detail": details,
                # Observe-only tools stay pulse; mutations show in Activity.
                "interesting": show,
            }
            if show and card is not None:
                out["tool_card"] = card
            return out
        out = {
            "label": _tool_human_label(tool),
            "detail": details,
            "interesting": True,
        }
        if card is not None:
            out["tool_card"] = card
        return out

    if kind == "tool_completed":
        tool = str(data.get("tool") or data.get("tool_name") or "tool")
        state = str(data.get("state") or "").strip()
        card = _tool_card_from_payload(kind, data)
        frame = _computer_frame_from_payload(kind, data)
        if state:
            details.append(f"Result: {state}")
        if card and card.get("args_preview") and not _computer_tool_label(tool):
            details.append(str(card["args_preview"]))
        duration = card.get("duration_ms") if card else data.get("duration_ms")
        if duration not in (None, "", 0, 0.0) and not _computer_tool_label(tool):
            details.append(f"Duration: {duration} ms")
        refs = (card or {}).get("artifact_refs") or data.get("artifact_refs") or []
        if isinstance(refs, list) and refs and not _computer_tool_label(tool):
            details.append(f"Artifacts: {', '.join(str(r) for r in refs[:3])}")
        computer_label = _computer_tool_label(tool)
        if computer_label:
            name = str(tool or "")
            show = name in _COMPUTER_ACTIVITY_TOOLS
            # Don't flash "Finished …" over the live pulse between rapid clicks.
            out = {
                "label": computer_label,
                "detail": details,
                "interesting": show,
            }
            # Cards on mutations and screenshot-bearing milestones.
            if show or frame is not None:
                if card is not None:
                    out["tool_card"] = card
            if frame is not None:
                out["computer_frame"] = frame
            return out
        label = (
            f"Finished {tool} ({state})"
            if state and state.lower() not in {"success", "ok", "completed"}
            else f"Finished {tool}"
        )
        out = {"label": label, "detail": details, "interesting": False}
        if card is not None:
            out["tool_card"] = card
        if frame is not None:
            out["computer_frame"] = frame
        return out

    if kind == "computer_frame":
        frame = _computer_frame_from_payload(kind, data)
        app = (frame or {}).get("app") or ""
        action = (frame or {}).get("action") or "computer"
        if app:
            details.append(f"App: {app}")
        if action:
            details.append(f"Action: {action}")
        path = (frame or {}).get("thumb_path") or (frame or {}).get("path") or ""
        if path:
            details.append(f"Frame: {path}")
        out = {
            "label": f"Desktop · {app}" if app else "Desktop frame",
            "detail": details,
            "interesting": False,
        }
        if frame is not None:
            out["computer_frame"] = frame
        return out

    if kind == "verification_started":
        source = str(data.get("source") or "").strip()
        if source:
            details.append(f"Checker: {source}")
        return {
            "label": "Checking the result…",
            "detail": details,
            "interesting": True,
        }

    if kind == "verification":
        status = str(
            data.get("status") or data.get("validation") or data.get("semantic_status") or ""
        ).strip()
        reason = _clip(str(data.get("reason") or ""), limit=140)
        if status:
            details.append(f"Status: {status}")
        if reason:
            details.append(reason)
        defects = data.get("defects")
        if isinstance(defects, list) and defects:
            details.append(f"Defects: {len(defects)}")
        elif isinstance(defects, int) and defects:
            details.append(f"Defects: {defects}")
        checks = data.get("checks")
        if isinstance(checks, list) and checks:
            passed = sum(1 for c in checks if isinstance(c, dict) and c.get("passed"))
            details.append(f"Checks: {passed}/{len(checks)} passed")
        label = f"Checked · {status}" if status else "Checking the result…"
        return {"label": label, "detail": details, "interesting": True}

    if kind == "repair":
        return {"label": "Repairing…", "detail": details, "interesting": True}

    if kind == "approval_required":
        action = _clip(
            str(data.get("action") or data.get("question") or ""), limit=140
        )
        detail = _clip(str(data.get("detail") or ""), limit=160)
        if action:
            details.append(action)
        if detail and detail != action:
            details.append(detail)
        risk = str(data.get("risk_class") or "").strip()
        if risk:
            details.append(f"Risk: {risk}")
        return {
            "label": "Waiting for approval…",
            "detail": details,
            "interesting": True,
        }

    if kind == "approval_resolved":
        return {"label": "Continuing…", "detail": details, "interesting": True}

    if kind == "checkpoint":
        return {"label": "Checkpoint…", "detail": details, "interesting": False}

    if kind == "todo_board":
        done = data.get("done")
        total = data.get("total")
        try:
            done_n = int(done) if done is not None else 0
            total_n = int(total) if total is not None else 0
        except (TypeError, ValueError):
            done_n, total_n = 0, 0
        items = data.get("items") if isinstance(data.get("items"), list) else []
        for it in items[:8]:
            if not isinstance(it, dict):
                continue
            mark = "x" if it.get("done") else " "
            item_id = str(it.get("id") or "").strip()
            text = _clip(str(it.get("text") or "").strip(), limit=100)
            body = f"{item_id}: {text}" if item_id and text else (text or item_id)
            if body:
                details.append(f"[{mark}] {body}")
        label = f"Todos {done_n}/{total_n}" if total_n else "Todos"
        return {"label": label, "detail": details, "interesting": True}

    if kind == "blocked":
        err = _clip(
            str(data.get("error") or data.get("reason") or ""),
            limit=140,
        )
        if err:
            details.append(err)
        return {"label": "Blocked…", "detail": details, "interesting": True}

    if kind == "cancelled":
        return {"label": "Cancelled", "detail": details, "interesting": True}

    if kind == "failed":
        err = _clip(str(data.get("error") or ""), limit=160)
        if err:
            details.append(err)
        return {"label": "Failed", "detail": details, "interesting": True}

    if kind == "completed":
        steps = data.get("steps")
        if steps not in (None, ""):
            details.append(f"Steps used: {steps}")
        arts = data.get("artifacts")
        if isinstance(arts, list) and arts:
            details.append(f"Artifacts: {len(arts)}")
        return {"label": "Done", "detail": details, "interesting": True}

    if kind == "progress":
        raw = (
            data.get("message")
            or data.get("text")
            or data.get("status")
            or data.get("detail")
            or ""
        )
        if raw:
            try:
                from kageha.chat.progress import _friendly_status

                friendly = _friendly_status(str(raw)) or _clip(
                    str(raw), limit=120
                )
            except Exception:  # noqa: BLE001
                friendly = _clip(str(raw), limit=120)
            if friendly:
                details.append(friendly)
                return {
                    "label": friendly,
                    "detail": details,
                    "interesting": True,
                }
        model = str(data.get("model") or "").strip()
        provider = str(data.get("provider") or "").strip()
        if model:
            details.append(f"Model: {provider + '/' if provider else ''}{model}")
            reasoning = _clip(str(data.get("reasoning") or ""), limit=280)
            if reasoning:
                details.append(reasoning)
            return {
                "label": f"Thinking with {model}…",
                "detail": details,
                # Token accounting is pulse-only; thought text (if any) still shows.
                "interesting": bool(reasoning),
            }
        tool_count = data.get("tool_count")
        if tool_count not in (None, ""):
            hist = data.get("history_messages")
            details.append(f"Tools loaded: {tool_count}")
            if hist not in (None, ""):
                details.append(f"History messages: {hist}")
            return {
                "label": f"Preparing context · {tool_count} tools",
                "detail": details,
                # Emitted every step — keep pulse, skip Activity spam.
                "interesting": False,
            }
        stages = data.get("stages")
        goals = data.get("goals")
        if stages not in (None, "") or goals not in (None, ""):
            if stages not in (None, ""):
                details.append(f"Stages: {stages}")
            if goals not in (None, ""):
                details.append(f"Goals: {goals}")
            return {
                "label": "Preparing task state…",
                "detail": details,
                "interesting": True,
            }
        return {"label": "Working…", "detail": details, "interesting": False}

    return {"label": "Working…", "detail": details, "interesting": False}


def _stream_event_label(kind: str, payload: dict[str, Any] | None = None) -> str:
    """Map runtime event kinds to short UI status labels."""
    return str(_stream_event_view(kind, payload).get("label") or "Working…")


def _enrich_sse_payload(
    kind: str, payload: dict[str, Any], view: dict[str, Any]
) -> dict[str, Any]:
    """Slim payload + attach shaped tool_card / computer_frame for WS3."""
    slim = _sse_payload_view(kind, payload)
    card = view.get("tool_card")
    if isinstance(card, dict):
        slim["tool_card"] = card
    else:
        # Drop journal tool_card when view omitted it (computer_* pulse path).
        slim.pop("tool_card", None)
        for key in ("args_preview", "status", "duration_ms", "artifact_refs"):
            if key in slim and kind in {"tool_started", "tool_completed"}:
                # Keep flat fields only when a card is exposed.
                slim.pop(key, None)
    frame = view.get("computer_frame")
    if isinstance(frame, dict):
        slim["computer_frame"] = frame
    else:
        slim.pop("computer_frame", None)
    return slim


def _stream_frame(
    *,
    kind: str,
    payload: dict[str, Any],
    sequence: Any,
    turn_id: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    """Build one WebUI stream/events frame (label + tool_card + computer_frame)."""
    view = _stream_event_view(kind, payload)
    detail = [
        str(item).strip()
        for item in (view.get("detail") or [])
        if str(item).strip()
    ]
    frame: dict[str, Any] = {
        "sequence": sequence,
        "kind": kind,
        "payload": _enrich_sse_payload(kind, payload, view),
        "label": str(view.get("label") or "Working…"),
        "detail": detail,
        "interesting": bool(view.get("interesting")),
    }
    if turn_id:
        frame["turn_id"] = turn_id
    if session_id:
        frame["session_id"] = session_id
    card = view.get("tool_card")
    if isinstance(card, dict):
        frame["tool_card"] = card
    computer_frame = view.get("computer_frame")
    if isinstance(computer_frame, dict):
        if session_id:
            computer_frame = _attach_computer_thumb_url(computer_frame, session_id)
            # Keep payload.computer_frame in sync for tolerant clients.
            payload_view = frame.get("payload")
            if isinstance(payload_view, dict) and isinstance(
                payload_view.get("computer_frame"), dict
            ):
                payload_view["computer_frame"] = computer_frame
        frame["computer_frame"] = computer_frame
    return frame


def _parse_multipart(
    body: bytes, content_type: str
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Parse multipart/form-data into (fields, files).

    Each file: {field, filename, content_type, data}.
    """
    m = re.search(r"boundary=([^;]+)", content_type or "", flags=re.I)
    if not m:
        raise ValueError("multipart boundary missing")
    boundary = m.group(1).strip().strip('"').encode("utf-8")
    if not boundary:
        raise ValueError("multipart boundary empty")
    delimiter = b"--" + boundary
    if body.startswith(delimiter):
        parts = body.split(delimiter)
    else:
        parts = body.split(b"\r\n" + delimiter)
    fields: dict[str, str] = {}
    files: list[dict[str, Any]] = []
    for part in parts:
        if not part or part in (b"--", b"--\r\n", b"\r\n"):
            continue
        chunk = part
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        if chunk.endswith(b"\r\n"):
            chunk = chunk[:-2]
        if chunk == b"--" or chunk.startswith(b"--"):
            continue
        header_blob, sep, data = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        headers: dict[str, str] = {}
        for line in header_blob.split(b"\r\n"):
            if b":" not in line:
                continue
            key, val = line.split(b":", 1)
            headers[key.decode("utf-8", "replace").strip().lower()] = val.decode(
                "utf-8", "replace"
            ).strip()
        disp = headers.get("content-disposition", "")
        name_m = re.search(r'name="([^"]+)"', disp)
        if not name_m:
            continue
        field_name = name_m.group(1)
        filename_m = re.search(r'filename="([^"]*)"', disp)
        if filename_m is not None:
            files.append(
                {
                    "field": field_name,
                    "filename": filename_m.group(1),
                    "content_type": headers.get("content-type", "application/octet-stream"),
                    "data": data,
                }
            )
        else:
            fields[field_name] = data.decode("utf-8", "replace")
    return fields, files


def _emit_text_deltas(
    emit: Callable[[str, dict[str, Any]], None],
    text: str,
    *,
    chunk_min: int = 40,
    chunk_max: int = 64,
    pause_seconds: float = 0.02,
) -> None:
    """Emit progressive ``delta`` SSE frames by splitting *text* on whitespace."""
    if not text:
        return
    i = 0
    n = len(text)
    while i < n:
        if n - i <= chunk_max:
            piece = text[i:]
            i = n
        else:
            window_end = min(i + chunk_max, n)
            min_end = min(i + chunk_min, n)
            cut = window_end
            for j in range(window_end, min_end - 1, -1):
                if text[j - 1].isspace():
                    cut = j
                    break
            else:
                for j in range(window_end, n):
                    if text[j].isspace():
                        cut = j + 1
                        break
                else:
                    cut = n
            piece = text[i:cut]
            i = cut
        if piece:
            emit("delta", {"text": piece})
        if i < n:
            time.sleep(max(0.015, min(0.03, pause_seconds)))


class WebUIApp:
    """Request router that reuses a shared AppServer instance."""

    def __init__(
        self,
        server: AppServer | None = None,
        *,
        project_root: str | None = None,
    ) -> None:
        self.server = server or AppServer()
        self.project_root = str(project_root or Path.cwd())
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop, name="kageha-webui-loop", daemon=True
        )
        self._loop_thread.start()
    def _thread_state(self, thread_id: str) -> dict[str, Any]:
        st = self.server.threads.get(thread_id)
        if not isinstance(st, dict):
            st = {}
            self.server.threads[thread_id] = st
        return st

    def _default_project_root(self, payload: dict[str, Any] | None = None) -> str:
        if payload and payload.get("project_root"):
            return str(payload.get("project_root"))
        return self.project_root

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def close(self) -> None:
        try:
            self.server.close()
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join(timeout=2.0)

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        }
        future = asyncio.run_coroutine_threadsafe(self.server.handle(req), self._loop)
        resp = future.result(timeout=600)
        if "error" in resp:
            err = resp["error"]
            data = err.get("data") if isinstance(err, dict) else None
            raise RpcError(
                str(err.get("message") if isinstance(err, dict) else err),
                data if isinstance(data, dict) else {},
            )
        return resp.get("result")

    def handle(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        body: bytes,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes, str]:
        try:
            return self._handle(method, path, query, body, headers or {})
        except RpcError as exc:
            return _error(str(exc), status=500, detail=exc.detail)
        except KeyError as exc:
            return _error(str(exc), status=404)
        except ValueError as exc:
            return _error(str(exc), status=400)
        except Exception as exc:  # noqa: BLE001
            return _error(f"{type(exc).__name__}: {exc}", status=500)

    def _q(self, query: dict[str, list[str]], key: str, default: str = "") -> str:
        values = query.get(key) or []
        return str(values[0]) if values else default

    def _qi(self, query: dict[str, list[str]], key: str, default: int) -> int:
        raw = self._q(query, key, "")
        if not raw:
            return default
        return int(raw)

    def _json_body(self, body: bytes) -> dict[str, Any]:
        if not body:
            return {}
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _handle(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        body: bytes,
        headers: dict[str, str],
    ) -> tuple[int, bytes, str]:
        # React (Vite) UI only — no legacy vanilla SPA.
        if method == "GET" and path in {"/", "/index.html"}:
            react = self._react_asset("index.html")
            if react is not None:
                return react
            return _react_missing()
        if method == "GET" and path.startswith("/assets/"):
            react = self._react_asset(path.lstrip("/"))
            if react is not None:
                return react
            return _react_missing()
        # Vite hashed assets / favicon from React dist (SPA-safe file serve).
        if method == "GET" and not path.startswith("/api/"):
            react = self._react_asset(path.lstrip("/"))
            if react is not None:
                return react

        if method == "GET" and path == "/api/health":
            return _json_bytes({"ok": True, "pong": self.rpc("ping")})

        if method == "GET" and path == "/api/meta":
            project_root = self._q(query, "project_root") or self.project_root
            project_info: dict[str, Any] | None = None
            try:
                from kageha.project.brain import load_project_brain
                from kageha.project.hooks import load_hook_runner
                from kageha.project.worktree import is_git_repo, list_worktrees

                brain = load_project_brain(project_root)
                hooks = load_hook_runner(project_root)
                project_info = {
                    "git": is_git_repo(project_root),
                    "brain": None
                    if brain is None
                    else {
                        "root_file": brain.root_file,
                        "rules": [r.name for r in brain.rules],
                        "commands": brain.command_names,
                    },
                    "hooks": len(hooks.hooks),
                    "worktrees": list_worktrees(project_root)[:12],
                }
            except Exception as exc:  # noqa: BLE001 — optional Labs enrichment
                print(f"[kageha-webui] meta project enrichment failed: {exc}")
                project_info = {"error": str(exc)}
            return _json_bytes(
                {
                    "brand": "Kageha",
                    "memory_kinds": MEMORY_KINDS,
                    "memory_states": MEMORY_STATES,
                    "memory_scopes": MEMORY_SCOPES,
                    "media_exts": sorted(_MEDIA_EXTS),
                    "project_root": project_root,
                    "features": {
                        "jobs": True,
                        "worktrees": True,
                        "project_brain": True,
                        "hooks": True,
                        "attach": True,
                    },
                    "project": project_info,
                    "hitl": {
                        "mode": "ask_toggle",
                        "note": (
                            "Default auto-approves risky tools. Toggle Ask in the "
                            "composer to pause on approvals (Approve/Deny banner)."
                        ),
                    },
                }
            )

        if method == "GET" and path == "/api/sessions":
            limit = self._qi(query, "limit", 50)
            active_only = str(self._q(query, "active") or "").strip().lower() in {
                "1",
                "true",
                "yes",
            }
            # Over-fetch when filtering active so the page still fills.
            fetch_limit = max(limit * 4, limit) if active_only else limit
            rows = self.rpc("runtime/list", {"limit": fetch_limit})
            enriched: list[dict[str, Any]] = []
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                item = dict(row)
                sid = str(item.get("session_id") or item.get("id") or "").strip()
                if sid and "session_id" not in item:
                    item["session_id"] = sid
                turn_status = item.get("turn_status") or item.get("status")
                turn_phase = item.get("turn_phase")
                item["turn_phase"] = turn_phase
                item["active"] = _turn_is_active(turn_status, turn_phase)
                if active_only and not item["active"]:
                    continue
                stored = self._stored_session_title(sid) if sid else None
                if stored is not None:
                    item["title"] = stored
                flags = self._session_flags(sid) if sid else {}
                if flags:
                    item["pinned"] = bool(flags.get("pinned"))
                    item["archived"] = bool(flags.get("archived"))
                enriched.append(item)
                if len(enriched) >= limit:
                    break
            return _json_bytes({"sessions": enriched, "active": active_only})

        if method == "POST" and path == "/api/sessions":
            payload = self._json_body(body)
            # Allocate a workspace up front so uploads work before the first turn.
            session_id = str(payload.get("session_id") or uuid.uuid4().hex[:12])
            if not _SAFE_SESSION_ID.fullmatch(session_id):
                raise ValueError("invalid session_id")
            # Stable thread binding: reopen uses the same id (or session.json).
            thread_id = str(payload.get("thread_id") or f"web-{session_id}")
            self.rpc("thread/start", {"thread_id": thread_id})
            self._session_workspace(session_id)
            self._persist_thread_binding(session_id, thread_id)
            self.server.threads[thread_id] = {
                "messages": [],
                "run_id": session_id,
            }
            return _json_bytes(
                {
                    "thread_id": thread_id,
                    "session_id": session_id,
                    "messages": [],
                }
            )

        m_artifacts = re.fullmatch(r"/api/sessions/([^/]+)/artifacts", path)
        if method == "GET" and m_artifacts:
            session_id = m_artifacts.group(1)
            if not _SAFE_SESSION_ID.fullmatch(session_id):
                raise ValueError("invalid session_id")
            return _json_bytes(self._session_artifacts_payload(session_id))

        m_design = re.fullmatch(r"/api/sessions/([^/]+)/design", path)
        if method == "GET" and m_design:
            session_id = m_design.group(1)
            if not _SAFE_SESSION_ID.fullmatch(session_id):
                raise ValueError("invalid session_id")
            return _json_bytes(self._session_design_payload(session_id))

        m_share = re.fullmatch(r"/api/sessions/([^/]+)/share", path)
        if method == "GET" and m_share:
            session_id = m_share.group(1)
            if not _SAFE_SESSION_ID.fullmatch(session_id):
                raise ValueError("invalid session_id")
            from kageha.share import generate_share_html
            from kageha.config import sessions_dir

            session_dir = sessions_dir() / session_id
            if not session_dir.is_dir():
                raise KeyError(f"session not found: {session_id}")
            html = generate_share_html(session_id, session_dir)
            return (
                200,
                html.encode("utf-8"),
                "text/html; charset=utf-8",
                {"Content-Disposition": f'inline; filename="kageha-session-{session_id[:8]}.html"'},
            )
        if method in {"PUT", "PATCH"} and m_design:
            session_id = m_design.group(1)
            if not _SAFE_SESSION_ID.fullmatch(session_id):
                raise ValueError("invalid session_id")
            return _json_bytes(
                self._save_session_design(session_id, self._json_body(body))
            )

        m = re.fullmatch(r"/api/sessions/([^/]+)", path)
        if method == "GET" and m:
            session_id = m.group(1)
            if not _SAFE_SESSION_ID.fullmatch(session_id):
                raise ValueError("invalid session_id")
            try:
                inspected = self.rpc(
                    "runtime/inspect", {"session_id": session_id}
                )
            except RpcError as exc:
                # AppServer redacts KeyError text; surface a clean 404 instead.
                if str(exc.detail.get("error_type") or "") == "KeyError":
                    raise KeyError(f"unknown session: {session_id}") from exc
                raise
            messages = self._load_messages(session_id)
            thread_id = str(
                self._q(query, "thread_id")
                or self._load_thread_binding(session_id)
                or f"web-{session_id}"
            )
            self._persist_thread_binding(session_id, thread_id)
            # Merge — do not wipe in-flight turn_id / pending_approval.
            state = self.server.threads.setdefault(thread_id, {})
            state["messages"] = list(messages)
            state["run_id"] = session_id
            session = inspected.get("session") or {}
            turns = inspected.get("turns") or []
            last_turn = turns[-1] if turns else None
            turn_status = last_turn.get("status") if last_turn else None
            turn_phase = last_turn.get("phase") if last_turn else None
            active_turn = None
            if last_turn and _turn_is_active(turn_status, turn_phase):
                tid = last_turn.get("id") or last_turn.get("turn_id")
                active_turn = {
                    "turn_id": tid,
                    "status": turn_status,
                    "phase": turn_phase,
                }
                if tid and not state.get("turn_id"):
                    state["turn_id"] = tid
            pending = state.get("pending_approval")
            meta = self._load_session_meta(session_id)
            # Backfill Cursor-style titles for older weak labels (e.g. "hey").
            try:
                from kageha.session_title import is_weak_title

                if is_weak_title(str(meta.get("title") or "")):
                    self._maybe_set_session_title(session_id, "")
                    meta = self._load_session_meta(session_id)
            except Exception:  # noqa: BLE001
                pass
            return _json_bytes(
                {
                    "thread_id": thread_id,
                    "session_id": session_id,
                    "title": str(meta.get("title") or ""),
                    "pinned": bool(meta.get("pinned")),
                    "archived": bool(meta.get("archived")),
                    "status": turn_status or session.get("status"),
                    "turns": turns,
                    "active_turn": active_turn,
                    "pending_approval": pending if isinstance(pending, dict) else None,
                    "uncertain_tools": inspected.get("uncertain_tools") or [],
                    "messages": messages,
                    "session": session,
                    "todo_board": self._session_todo_board_payload(session_id),
                }
            )

        m_events = re.fullmatch(r"/api/sessions/([^/]+)/events", path)
        if method == "GET" and m_events:
            session_id = m_events.group(1)
            if not _SAFE_SESSION_ID.fullmatch(session_id):
                raise ValueError("invalid session_id")
            thread_id = str(
                self._q(query, "thread_id")
                or self._load_thread_binding(session_id)
                or f"web-{session_id}"
            )
            turn_id = str(self._q(query, "turn_id") or "")
            after = int(self._q(query, "after_sequence") or 0)
            raw_events = self.rpc(
                "thread/events",
                {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "after_sequence": after,
                },
            )
            mapped: list[dict[str, Any]] = []
            for ev in raw_events if isinstance(raw_events, list) else []:
                if isinstance(ev, dict):
                    kind = str(ev.get("kind") or "")
                    payload = (
                        ev.get("payload")
                        if isinstance(ev.get("payload"), dict)
                        else {}
                    )
                    seq = ev.get("sequence")
                else:
                    kind = (
                        ev.kind.value
                        if hasattr(getattr(ev, "kind", None), "value")
                        else str(getattr(ev, "kind", "") or "")
                    )
                    payload = dict(getattr(ev, "payload", None) or {})
                    seq = getattr(ev, "sequence", None)
                mapped.append(
                    _stream_frame(
                        kind=kind,
                        payload=payload,
                        sequence=seq,
                        turn_id=turn_id,
                        session_id=session_id,
                    )
                )
            return _json_bytes(
                {
                    "thread_id": thread_id,
                    "session_id": session_id,
                    "turn_id": turn_id
                    or self._thread_state(thread_id).get("turn_id")
                    or "",
                    "events": mapped,
                    "pending_approval": self._thread_state(thread_id).get("pending_approval"),
                }
            )

        if method == "PATCH" and m:
            session_id = m.group(1)
            if not _SAFE_SESSION_ID.fullmatch(session_id):
                raise ValueError("invalid session_id")
            payload = self._json_body(body)
            has_title = "title" in payload
            has_pinned = "pinned" in payload
            has_archived = "archived" in payload
            if not (has_title or has_pinned or has_archived):
                raise ValueError("title, pinned, or archived is required")
            meta = self._load_session_meta(session_id)
            meta["session_id"] = session_id
            if not str(meta.get("thread_id") or "").strip():
                meta["thread_id"] = f"web-{session_id}"
            if has_title:
                from kageha.session_title import apply_session_title

                meta, _ = apply_session_title(
                    meta,
                    candidate=str(payload.get("title") or ""),
                    force_user=True,
                )
            if has_pinned:
                meta["pinned"] = bool(payload.get("pinned"))
            if has_archived:
                meta["archived"] = bool(payload.get("archived"))
            self._save_session_meta(session_id, meta)
            return _json_bytes(
                {
                    "session_id": session_id,
                    "title": str(meta.get("title") or ""),
                    "pinned": bool(meta.get("pinned")),
                    "archived": bool(meta.get("archived")),
                }
            )

        if method == "DELETE" and m:
            session_id = m.group(1)
            if not _SAFE_SESSION_ID.fullmatch(session_id):
                raise ValueError("invalid session_id")
            deleted = self._delete_session(session_id)
            return _json_bytes(
                {"ok": True, "session_id": session_id, "deleted": deleted}
            )

        m_truncate = re.fullmatch(r"/api/sessions/([^/]+)/truncate", path)
        if method == "POST" and m_truncate:
            session_id = m_truncate.group(1)
            if not _SAFE_SESSION_ID.fullmatch(session_id):
                raise ValueError("invalid session_id")
            payload = self._json_body(body)
            msg_index = int(payload.get("message_index", -1))
            return _json_bytes(self._truncate_session(session_id, msg_index))

        if method == "POST" and path == "/api/memory/delete":
            payload = self._json_body(body)
            memory_id = str(payload.get("id") or payload.get("memory_id") or "")
            content = str(payload.get("content") or "")
            if not memory_id and not content:
                return _error("id or content required", status=400)
            result = self.rpc(
                "memory/mutate",
                {
                    "action": "forget",
                    "target": memory_id or content,
                    "content": content,
                    "session_id": str(payload.get("session_id") or ""),
                    "project_root": str(payload.get("project_root") or self.project_root),
                },
            )
            return _json_bytes({"ok": True, "forgotten": result})

        m_upload = re.fullmatch(r"/api/sessions/([^/]+)/upload", path)
        if method == "POST" and m_upload:
            return self._upload_session_file(m_upload.group(1), body, headers)

        m_stt = re.fullmatch(r"/api/sessions/([^/]+)/stt", path)
        if method == "POST" and m_stt:
            return self._session_stt(m_stt.group(1), body, headers)

        m_tts = re.fullmatch(r"/api/sessions/([^/]+)/tts", path)
        if method == "POST" and m_tts:
            return self._session_tts(m_tts.group(1), body)

        m_file = re.fullmatch(r"/api/sessions/([^/]+)/files/(.+)", path)
        if method == "GET" and m_file:
            force_download = self._q(query, "download").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            return self._serve_session_file(
                m_file.group(1), m_file.group(2), download=force_download
            )

        if method == "POST" and path == "/api/chat/cancel":
            payload = self._json_body(body)
            thread_id = str(payload.get("thread_id") or "").strip()
            if not thread_id:
                raise ValueError("thread_id is required")
            self.rpc("thread/cancel", {"thread_id": thread_id})
            return _json_bytes({"ok": True, "thread_id": thread_id})

        if method == "POST" and path == "/api/approvals":
            payload = self._json_body(body)
            approval_id = str(payload.get("approval_id") or "").strip()
            if not approval_id:
                raise ValueError("approval_id is required")
            scope = str(payload.get("scope") or "once").strip().lower() or "once"
            return _json_bytes(
                self.rpc(
                    "thread/approve",
                    {
                        "approval_id": approval_id,
                        "approved": bool(payload.get("approved", False)),
                        "feedback": str(payload.get("feedback") or "").strip(),
                        "scope": scope,
                    },
                )
            )

        if method == "POST" and path == "/api/permissions":
            payload = self._json_body(body)
            mode = str(payload.get("mode") or payload.get("scope") or "ask").strip().lower()
            if mode == "auto":
                mode = "session"
            if mode not in {"ask", "session", "full"}:
                raise ValueError("mode must be ask|auto|full")
            from kageha.harness.approvals import apply_permission_scope, process_permissions

            grant = apply_permission_scope(mode)
            return _json_bytes({"ok": True, **grant, **process_permissions()})

        if method == "GET" and path == "/api/permissions":
            from kageha.harness.approvals import process_permissions

            return _json_bytes({"ok": True, **process_permissions()})

        if method == "POST" and path == "/api/chat":
            payload = self._json_body(body)
            user_title_source = str(
                payload.get("message") or payload.get("task") or ""
            ).strip()
            # Native slash: /browser · /research · /computer (no agent loop)
            low_msg = user_title_source.lower()
            if (
                low_msg == "/browser"
                or low_msg.startswith("/browser ")
                or low_msg == "/research"
                or low_msg.startswith("/research ")
                or low_msg == "/comet"
                or low_msg.startswith("/comet ")
                or low_msg == "/computer"
                or low_msg.startswith("/computer ")
            ):
                from kageha.chat.browser_commands import handle_browser_or_research
                from kageha.chat.comet import handle_comet_command
                from kageha.chat.computer_commands import handle_computer_command

                if low_msg.startswith("/comet"):
                    future = asyncio.run_coroutine_threadsafe(
                        handle_comet_command(user_title_source), self._loop
                    )
                elif low_msg.startswith("/computer"):
                    future = asyncio.run_coroutine_threadsafe(
                        handle_computer_command(user_title_source), self._loop
                    )
                else:
                    future = asyncio.run_coroutine_threadsafe(
                        handle_browser_or_research(user_title_source), self._loop
                    )
                handled, message = future.result(timeout=180)
                if handled:
                    return _json_bytes(
                        {
                            "thread_id": str(payload.get("thread_id") or "web-default"),
                            "session_id": payload.get("session_id"),
                            "run_id": payload.get("session_id"),
                            "status": "ok",
                            "message": message,
                            "artifacts": [],
                            "attachments": [],
                            "loop_mode": "quick",
                            "quick": True,
                        }
                    )
            params, attachments, loop_mode = self._prepare_chat(payload)
            result = self.rpc("thread/turn", params)
            run_id = str(result.get("run_id") or "").strip()
            if run_id:
                self._maybe_set_session_title(
                    run_id,
                    user_title_source,
                    assistant_message=str(result.get("message") or ""),
                    artifact_paths=_artifact_paths_from_result(result),
                )
            return _json_bytes(
                self._chat_result_payload(
                    params["thread_id"],
                    result,
                    attachments=attachments,
                    loop_mode=loop_mode,
                )
            )

        # Streaming uses the HTTP handler's SSE writer; buffered router rejects it.
        if method == "POST" and path == "/api/chat/stream":
            return _error(
                "use streaming HTTP handler for /api/chat/stream",
                status=405,
            )

        if method == "GET" and path == "/api/memory/status":
            return _json_bytes(self.rpc("memory/status"))

        if method == "GET" and path == "/api/memory/kinds":
            return _json_bytes(
                {
                    "kinds": MEMORY_KINDS,
                    "states": MEMORY_STATES,
                    "scopes": MEMORY_SCOPES,
                }
            )

        if method == "GET" and path == "/api/memory/list":
            return _json_bytes(self._memory_list(query))

        if method == "GET" and path == "/api/worktrees":
            from kageha.project.worktree import list_worktrees

            root = self._q(query, "project_root") or str(Path.cwd())
            return _json_bytes({"project_root": root, "worktrees": list_worktrees(root)})

        if method == "POST" and path == "/api/worktrees":
            payload = self._json_body(body)
            from kageha.project.worktree import create_worktree

            handle = create_worktree(
                str(payload.get("project_root") or Path.cwd()),
                label=str(payload.get("label") or "agent"),
                base_ref=str(payload.get("base") or "HEAD"),
            )
            return _json_bytes(
                {
                    "path": str(handle.path),
                    "branch": handle.branch,
                    "root": str(handle.root),
                }
            )

        if method == "GET" and path == "/api/project":
            from kageha.project.brain import load_project_brain, render_project_brain
            from kageha.project.hooks import load_hook_runner
            from kageha.project.worktree import is_git_repo, list_worktrees

            root = self._q(query, "project_root") or str(Path.cwd())
            brain = load_project_brain(root)
            hooks = load_hook_runner(root)
            return _json_bytes(
                {
                    "project_root": root,
                    "git": is_git_repo(root),
                    "brain": None
                    if brain is None
                    else {
                        "root_file": brain.root_file,
                        "rules": [
                            {"name": r.name, "globs": r.globs} for r in brain.rules
                        ],
                        "commands": brain.command_names,
                        "rendered": render_project_brain(brain)[:8000],
                    },
                    "hooks": [
                        {
                            "event": h.event,
                            "matcher": h.matcher,
                            "command": (h.command or "")[:160],
                            "http": h.http,
                        }
                        for h in hooks.hooks
                    ],
                    "worktrees": list_worktrees(root),
                }
            )

        # ── Hooks CRUD ─────────────────────────────────────────────────
        if method == "GET" and path == "/api/hooks":
            from kageha.project.hooks import load_hook_runner

            root = self._q(query, "project_root") or self.project_root or str(Path.cwd())
            runner = load_hook_runner(root)
            return _json_bytes({
                "project_root": root,
                "hooks": [
                    {
                        "event": h.event,
                        "command": h.command,
                        "http": h.http,
                        "deny_message": h.deny_message,
                        "matcher": h.matcher,
                        "timeout_s": h.timeout_s,
                    }
                    for h in runner.hooks
                ],
            })

        if method == "POST" and path == "/api/hooks":
            payload = self._json_body(body)
            root = str(payload.get("project_root") or self.project_root or Path.cwd())
            hooks_path = Path(root) / ".kageha" / "hooks.json"
            hooks_path.parent.mkdir(parents=True, exist_ok=True)
            existing: list[dict[str, Any]] = []
            if hooks_path.is_file():
                try:
                    raw = json.loads(hooks_path.read_text(encoding="utf-8"))
                    if isinstance(raw, list):
                        existing = raw
                    elif isinstance(raw, dict) and isinstance(raw.get("hooks"), list):
                        existing = raw["hooks"]
                except (OSError, json.JSONDecodeError):
                    pass
            new_hook = {
                "event": str(payload.get("event") or "").strip(),
                "command": str(payload.get("command") or "").strip(),
                "http": str(payload.get("http") or "").strip(),
                "deny_message": str(payload.get("deny_message") or "").strip(),
                "matcher": str(payload.get("matcher") or "").strip(),
                "timeout_s": float(payload.get("timeout_s") or 15),
            }
            if not new_hook["event"]:
                raise ValueError("event is required")
            existing.append(new_hook)
            hooks_path.write_text(
                json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            return _json_bytes({"ok": True, "hook": new_hook, "total": len(existing)})

        if method == "DELETE" and path == "/api/hooks":
            payload = self._json_body(body)
            root = str(payload.get("project_root") or self.project_root or Path.cwd())
            index = int(payload.get("index", -1))
            hooks_path = Path(root) / ".kageha" / "hooks.json"
            if not hooks_path.is_file():
                raise FileNotFoundError("no hooks.json")
            try:
                raw = json.loads(hooks_path.read_text(encoding="utf-8"))
                existing = raw if isinstance(raw, list) else (raw.get("hooks") if isinstance(raw, dict) else [])
                if not isinstance(existing, list):
                    existing = []
            except (OSError, json.JSONDecodeError):
                existing = []
            if index < 0 or index >= len(existing):
                raise IndexError(f"hook index {index} out of range")
            removed = existing.pop(index)
            hooks_path.write_text(
                json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            return _json_bytes({"ok": True, "removed": removed, "total": len(existing)})

        # @ file search. UI wiring lives in the React frontend.
        if method == "GET" and path == "/api/project/files":
            from kageha.project.file_index import get_file_index

            root = (
                self._q(query, "project_root")
                or self.project_root
                or str(Path.cwd())
            )
            q = self._q(query, "q", "")
            limit = self._qi(query, "limit", 40)
            idx = get_file_index(root)
            files = idx.query(q, limit=limit)
            return _json_bytes(
                {
                    "project_root": str(idx.root),
                    "q": q,
                    "limit": max(1, min(int(limit), 500)),
                    "files": files,
                    "total_indexed": idx.size,
                    "truncated": idx.truncated,
                }
            )

        if method == "GET" and path == "/api/jobs":
            result = self.rpc(
                "jobs/list",
                {
                    "limit": self._qi(query, "limit", 40),
                    "status": self._q(query, "status"),
                },
            )
            return _json_bytes(result)

        if method == "POST" and path == "/api/jobs":
            payload = self._json_body(body)
            objective = str(payload.get("objective") or payload.get("message") or "").strip()
            if not objective:
                raise ValueError("objective required")
            result = self.rpc(
                "jobs/run",
                {
                    "objective": objective,
                    "project_root": str(
                        payload.get("project_root") or Path.cwd()
                    ),
                    "agent_mode": str(payload.get("agent_mode") or "plan"),
                    "max_steps": int(payload.get("max_steps") or 40),
                    "notify_channel": str(payload.get("notify_channel") or "webui"),
                },
            )
            return _json_bytes(result)

        m_job_cancel = re.fullmatch(r"/api/jobs/([^/]+)/cancel", path)
        if method == "POST" and m_job_cancel:
            job_id = m_job_cancel.group(1)
            try:
                result = self.rpc("jobs/cancel", {"job_id": job_id})
            except RpcError as exc:
                if str(exc.detail.get("error_type") or "") == "FileNotFoundError":
                    raise KeyError(f"job not found: {job_id}") from exc
                raise
            return _json_bytes(result)

        m_job_attach = re.fullmatch(r"/api/jobs/([^/]+)/attach", path)
        if method == "GET" and m_job_attach:
            job_id = m_job_attach.group(1)
            try:
                result = self.rpc("jobs/attach", {"job_id": job_id})
            except RpcError as exc:
                if str(exc.detail.get("error_type") or "") == "FileNotFoundError":
                    raise KeyError(f"job not found: {job_id}") from exc
                raise
            if not isinstance(result, dict):
                result = {}
            # Enrich turn_id from journal when the worker has started but the
            # job file lagged (WebUI restart / attach mid-run).
            session_id = str(result.get("session_id") or "").strip()
            turn_id = str(result.get("turn_id") or "").strip()
            if session_id and not turn_id:
                try:
                    inspected = self.rpc(
                        "runtime/inspect", {"session_id": session_id}
                    )
                except RpcError:
                    inspected = None
                if isinstance(inspected, dict):
                    turns = inspected.get("turns") or []
                    last = turns[-1] if turns else None
                    if isinstance(last, dict):
                        tid = str(last.get("id") or last.get("turn_id") or "")
                        if tid:
                            result["turn_id"] = tid
                            turn_status = last.get("status")
                            turn_phase = last.get("phase")
                            result["active_turn"] = (
                                {
                                    "turn_id": tid,
                                    "status": turn_status,
                                    "phase": turn_phase,
                                }
                                if _turn_is_active(turn_status, turn_phase)
                                else None
                            )
            return _json_bytes(result)

        m_job = re.fullmatch(r"/api/jobs/([^/]+)", path)
        if method == "GET" and m_job:
            job_id = m_job.group(1)
            try:
                result = self.rpc("jobs/status", {"job_id": job_id})
            except RpcError as exc:
                if str(exc.detail.get("error_type") or "") == "FileNotFoundError":
                    raise KeyError(f"job not found: {job_id}") from exc
                raise
            return _json_bytes(result)

        if method == "POST" and path == "/api/memory/search":
            return _json_bytes(self._memory_recall(self._json_body(body)))

        if method == "GET" and path == "/api/memory/explain":
            params = {
                "trace_id": self._q(query, "trace_id"),
                "session_id": self._q(query, "session_id"),
            }
            return _json_bytes({"trace": self.rpc("memory/explain", params)})

        if method == "GET" and path == "/api/browser":
            from kageha.harness.browser.backends import BACKENDS, format_backend_list
            from kageha.harness.browser.prefs import (
                apply_browser_prefs,
                load_browser_prefs,
                status_text,
            )

            prefs = apply_browser_prefs()
            return _json_bytes(
                {
                    "ok": True,
                    "backend": prefs.backend,
                    "cdp": prefs.cdp,
                    "research_depth": prefs.research_depth,
                    "enable_browser_pack": prefs.enable_browser_pack,
                    "backends": [
                        {
                            "id": b.id,
                            "kind": b.kind,
                            "label": b.label,
                            "description": b.description,
                        }
                        for b in BACKENDS
                    ],
                    "status": status_text(),
                    "list": format_backend_list(current=prefs.backend),
                    "prefs": load_browser_prefs().to_dict(),
                }
            )

        if method == "POST" and path == "/api/browser":
            payload = self._json_body(body)
            command = str(payload.get("command") or payload.get("line") or "").strip()
            if not command:
                backend = str(payload.get("backend") or "").strip()
                if backend:
                    cdp = str(payload.get("cdp") or "").strip()
                    if backend == "cdp" and cdp:
                        command = f"/browser cdp {cdp}"
                    elif cdp:
                        command = f"/browser use {backend} {cdp}"
                    else:
                        command = f"/browser use {backend}"
                else:
                    command = "/browser status"
            if not command.startswith("/"):
                command = "/" + command
            from kageha.chat.browser_commands import handle_browser_or_research

            future = asyncio.run_coroutine_threadsafe(
                handle_browser_or_research(command), self._loop
            )
            handled, message = future.result(timeout=180)
            return _json_bytes(
                {"ok": handled, "message": message, "command": command}
            )

        if method == "GET" and path == "/api/computer":
            from kageha.harness.tools.computer_prefs import (
                apply_computer_prefs,
                load_computer_prefs,
                status_text,
            )
            from kageha.harness.tool_packs import resolve_enabled_packs

            prefs = apply_computer_prefs()
            packs = resolve_enabled_packs()
            return _json_bytes(
                {
                    "ok": True,
                    "pack": prefs.pack,
                    "pack_loaded": "computer" in packs,
                    "status": status_text(),
                    "prefs": load_computer_prefs().to_dict(),
                }
            )

        if method == "POST" and path == "/api/computer":
            payload = self._json_body(body)
            command = str(payload.get("command") or payload.get("line") or "").strip()
            if not command:
                pack = str(payload.get("pack") or "").strip()
                if pack:
                    command = f"/computer pack {pack}"
                else:
                    command = "/computer status"
            if not command.startswith("/"):
                command = "/" + command
            from kageha.chat.computer_commands import handle_computer_command

            future = asyncio.run_coroutine_threadsafe(
                handle_computer_command(command), self._loop
            )
            handled, message = future.result(timeout=180)
            if not handled:
                # /computer <task> belongs in chat (activates computer_use skill).
                return _json_bytes(
                    {
                        "ok": False,
                        "message": (
                            "That looks like a computer-use task. "
                            "Send it in chat as `/computer …` "
                            "(or `/computer_use …`) — not via the status API. "
                            "Admin: /computer status|doctor|pack|allowlist."
                        ),
                        "command": command,
                        "skill": "computer_use",
                    },
                    status=400,
                )
            return _json_bytes(
                {"ok": handled, "message": message, "command": command}
            )

        # Slash catalog for WebUI / and Cmd+K (capability-gated; no stub 404s)
        if method == "GET" and path == "/api/slash-catalog":
            root = (
                self._q(query, "project_root")
                or self.project_root
                or str(Path.cwd())
            )
            return _json_bytes(_webui_slash_catalog(project_root=root))

        # Model catalog (same registry as CLI /model list)
        if method == "GET" and path == "/api/models":
            from kageha.chat.model_commands import format_model_list
            from kageha.models.registry import ModelRegistry

            reg = ModelRegistry.load()
            available = {m.id for m in reg.available_models()}
            models = [
                {
                    "id": m.id,
                    "provider": m.provider,
                    "model": m.model,
                    "roles": list(m.roles or []),
                    "ready": m.id in available,
                    "auth": reg.auth_source(m.id),
                }
                for m in reg.models.values()
            ]
            return _json_bytes(
                {
                    "ok": True,
                    "models": [m for m in models if m["ready"]],
                    "all": models,
                    "text": format_model_list(reg),
                }
            )

        # Comet convenience (same engine as /browser comet) — WebUI + CLI parity
        if method == "GET" and path == "/api/comet":
            from kageha.chat.comet import ensure_comet

            future = asyncio.run_coroutine_threadsafe(
                ensure_comet(launch=False), self._loop
            )
            message = future.result(timeout=30)
            return _json_bytes({"ok": True, "message": message, "action": "status"})

        if method == "POST" and path == "/api/comet":
            payload = self._json_body(body)
            action = str(payload.get("action") or "start").strip().lower()
            if action not in {"start", "status"}:
                return _error("action must be start|status")
            from kageha.chat.comet import ensure_comet

            future = asyncio.run_coroutine_threadsafe(
                ensure_comet(launch=action == "start"), self._loop
            )
            message = future.result(timeout=60)
            return _json_bytes({"ok": True, "message": message, "action": action})

        return _error("not found", status=404)

    def _prepare_chat(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str], str]:
        """Build AppServer thread/turn params from a Web UI chat payload."""
        message = str(payload.get("message") or payload.get("task") or "").strip()
        attachments = [
            str(a).strip().lstrip("./")
            for a in (payload.get("attachments") or [])
            if str(a).strip()
        ]
        if attachments:
            # Keep paths workspace-relative so tools can open them directly.
            block = "Attached files:\n" + "\n".join(f"- `{p}`" for p in attachments)
            message = f"{message}\n\n{block}" if message else block
        if not message:
            raise ValueError("message is required (or attach files)")
        run_id = str(payload.get("session_id") or payload.get("run_id") or "")
        thread_id = str(
            payload.get("thread_id")
            or (self._load_thread_binding(run_id) if run_id else "")
            or (f"web-{run_id}" if run_id else "web-default")
        )
        if run_id:
            self.server.threads.setdefault(thread_id, {})
            self.server.threads[thread_id]["run_id"] = run_id
        elif thread_id not in self.server.threads:
            self.rpc("thread/start", {"thread_id": thread_id})

        # New chat turn: drop the previous turn_id so SSE/poll clients cannot
        # latch leftover journal events into the next bubble. Keep run_id.
        state = self.server.threads.setdefault(thread_id, {})
        prev_turn = str(state.get("turn_id") or "").strip()
        if prev_turn:
            state["_prev_turn_id"] = prev_turn
        state["turn_id"] = ""

        # Default to chat-speed followup. Deep modes (plan/goal) use full loop.
        # Slash prefixes (/plan|/goal) win over payload agent_mode.
        from kageha.loop.mode_policy import (
            loop_mode_for,
            normalize_agent_mode,
            parse_mode_slash,
        )

        requested_loop = str(payload.get("loop_mode") or "").strip().lower()
        slash_mode = parse_mode_slash(message)
        agent_mode = normalize_agent_mode(
            slash_mode
            or str(payload.get("agent_mode") or "")
            or ("plan" if requested_loop == "full" else "normal")
        )
        if agent_mode != "normal":
            # Deep modes always run the full plan→verify loop; ignore a stale
            # followup from older clients that only special-cased /plan.
            loop_mode = loop_mode_for(agent_mode)
        elif requested_loop in {"full", "followup", "act"}:
            loop_mode = "followup" if requested_loop == "act" else requested_loop
        else:
            loop_mode = loop_mode_for(agent_mode)
        max_steps = payload.get("max_steps")
        if max_steps is None:
            max_steps = 24 if loop_mode != "full" else 40
        params: dict[str, Any] = {
            "thread_id": thread_id,
            "message": message,
            "auto_approve": bool(payload.get("auto_approve", True)),
            "auto_build": bool(payload.get("auto_build", False)),
            "project_root": self._default_project_root(payload),
            "user_id": str(payload.get("user_id") or "local"),
            "agent_id": str(payload.get("agent_id") or "main"),
            "channel_key": str(payload.get("channel_key") or "webui"),
            "platform": "webui",
            "loop_mode": loop_mode,
            "agent_mode": agent_mode,
            "max_steps": int(max_steps),
            "defer_human_input": True,
        }
        if payload.get("knowledge_bases"):
            params["knowledge_bases"] = list(payload["knowledge_bases"])
        if payload.get("model"):
            params["model"] = payload["model"]
        return params, attachments, loop_mode

    def _chat_result_payload(
        self,
        thread_id: str,
        result: dict[str, Any],
        *,
        attachments: list[str],
        loop_mode: str,
    ) -> dict[str, Any]:
        quick = bool(result.get("quick"))
        sources = result.get("sources") or []
        if not isinstance(sources, list):
            sources = []
        payload = {
            "thread_id": thread_id,
            "session_id": result.get("run_id"),
            "run_id": result.get("run_id"),
            "status": result.get("status"),
            "message": result.get("message"),
            "artifacts": result.get("artifacts") or [],
            "attachments": attachments,
            "turn_id": result.get("turn_id"),
            "loop_mode": "quick" if quick else loop_mode,
            "quick": quick,
            "sources": sources[:20],
            "steps": result.get("steps"),
            "spent_usd": result.get("spent_usd"),
        }
        run_id = str(result.get("run_id") or "").strip()
        if run_id:
            title = self._stored_session_title(run_id)
            if title is not None:
                payload["title"] = title
        return payload

    def _allow_computer_frame_emit(self) -> bool:
        """Throttle live computer_frame fan-out (journal still stores milestones)."""
        now = time.time()
        if now - float(self._last_computer_frame_at or 0.0) < _COMPUTER_FRAME_MIN_INTERVAL_S:
            return False
        self._last_computer_frame_at = now
        return True

    def _emit_turn_events(
        self,
        emit: Callable[[str, dict[str, Any]], None],
        *,
        turn_id: str,
        after_sequence: int,
        seen_approval_ids: set[str] | None = None,
    ) -> int:
        """Poll runtime events and emit SSE `event` + `status` frames."""
        try:
            events = self.server.runtime.store.events(
                turn_id, after_sequence=after_sequence
            )
        except Exception:  # noqa: BLE001
            return after_sequence
        seq = after_sequence
        for ev in events:
            seq = int(ev.sequence)
            kind = ev.kind.value if hasattr(ev.kind, "value") else str(ev.kind)
            payload = dict(ev.payload or {})
            if seen_approval_ids is not None and kind == "approval_required":
                aid = str(payload.get("approval_id") or "").strip()
                if aid:
                    seen_approval_ids.add(aid)
            frame = _stream_frame(
                kind=kind,
                payload=payload,
                sequence=seq,
                turn_id=turn_id,
                session_id=str(ev.session_id or ""),
            )
            label = str(frame.get("label") or "Working…")
            detail = list(frame.get("detail") or [])
            computer_frame = frame.get("computer_frame")
            if isinstance(computer_frame, dict) and self._allow_computer_frame_emit():
                # Dedicated SSE event for the desktop strip (WS3); ≤5fps live.
                sid = str(ev.session_id or frame.get("session_id") or "")
                emit_frame = _attach_computer_thumb_url(computer_frame, sid)
                emit(
                    "computer_frame",
                    {
                        "turn_id": turn_id,
                        "session_id": ev.session_id,
                        "sequence": seq,
                        **emit_frame,
                    },
                )
            emit("event", frame)
            # Skip redundant status frames for noisy / completed tool events.
            if label and kind not in _STATUS_SKIP_KINDS:
                emit(
                    "status",
                    {
                        "phase": kind,
                        "label": label,
                        "detail": detail[:2],
                        "turn_id": turn_id,
                        "session_id": ev.session_id,
                    },
                )
        return seq

    def stream_chat(
        self,
        body: bytes,
        emit: Callable[[str, dict[str, Any]], None],
        *,
        poll_interval: float = 0.12,
    ) -> None:
        """Start a chat turn and stream progressive SSE frames via ``emit``.

        Event schema (``event:`` name → data object) — WS3 contract:

        - ``status``: ``{phase, label, detail?, turn_id?, session_id?, thread_id?}``
        - ``event``: runtime journal event shaped for the transcript:
            ``{sequence, kind, label, detail[], interesting, turn_id?, session_id?,
               payload, tool_card?, computer_frame?}``
          - ``tool_card`` (on ``tool_started`` / ``tool_completed``):
            ``{name, args_preview, status, duration_ms|null, artifact_refs[], attempt_id?}``
            ``status`` is ``running`` | ``ok`` | ``error`` | ``denied``.
          - ``computer_frame`` (when a computer tool returned an image path):
            ``{path, thumb_path, app, action, thumb_url?}`` — path refs only,
            never AX dumps or base64. ``interesting`` stays false for computer_* pulse.
        - ``computer_frame`` (dedicated SSE event, throttled ≤5fps live):
            ``{turn_id, session_id, sequence, path, thumb_path, app, action}``
            Prefer ``thumb_path`` for the strip; serve via
            ``GET /api/sessions/{id}/files/{path}``.
        - ``delta``: ``{text}`` progressive final-reply chunks (whitespace splits)
        - ``message``: ``{text, partial}`` final reply text after deltas
        - ``done``: same shape as ``POST /api/chat`` JSON result
        - ``error``: ``{error, detail?}``

        Provider live token streaming via ``StreamingChatModel`` is future work;
        the agent loop still returns a full message, which is replayed as deltas.
        """
        try:
            payload = self._json_body(body)
            user_msg = str(
                payload.get("message") or payload.get("task") or ""
            ).strip()
            low_msg = user_msg.lower()
            if (
                low_msg == "/browser"
                or low_msg.startswith("/browser ")
                or low_msg == "/research"
                or low_msg.startswith("/research ")
                or low_msg == "/comet"
                or low_msg.startswith("/comet ")
                or low_msg == "/computer"
                or low_msg.startswith("/computer ")
            ):
                from kageha.chat.browser_commands import handle_browser_or_research
                from kageha.chat.comet import handle_comet_command
                from kageha.chat.computer_commands import handle_computer_command

                if low_msg.startswith("/comet"):
                    future_slash = asyncio.run_coroutine_threadsafe(
                        handle_comet_command(user_msg), self._loop
                    )
                elif low_msg.startswith("/computer"):
                    future_slash = asyncio.run_coroutine_threadsafe(
                        handle_computer_command(user_msg), self._loop
                    )
                else:
                    future_slash = asyncio.run_coroutine_threadsafe(
                        handle_browser_or_research(user_msg), self._loop
                    )
                handled, message = future_slash.result(timeout=180)
                if handled:
                    sid_hint = str(payload.get("session_id") or payload.get("run_id") or "")
                    tid_fallback = (
                        payload.get("thread_id")
                        or (self._load_thread_binding(sid_hint) if sid_hint else "")
                        or (f"web-{sid_hint}" if sid_hint else "web-default")
                    )
                    emit("status", {"phase": "done", "label": "Done"})
                    emit("message", {"text": message, "partial": False})
                    emit(
                        "done",
                        {
                            "thread_id": str(tid_fallback),
                            "session_id": payload.get("session_id"),
                            "run_id": payload.get("session_id"),
                            "status": "ok",
                            "message": message,
                            "artifacts": [],
                            "attachments": [],
                            "loop_mode": "quick",
                            "quick": True,
                        },
                    )
                    return
            # Capture leftover turn_id before _prepare_chat clears it.
            sid_hint = str(payload.get("session_id") or payload.get("run_id") or "")
            _tid_hint = str(
                payload.get("thread_id")
                or (self._load_thread_binding(sid_hint) if sid_hint else "")
                or (f"web-{sid_hint}" if sid_hint else "web-default")
            )
            previous_turn_id = str(
                (self._thread_state(_tid_hint)).get("turn_id") or ""
            ).strip()
            params, attachments, loop_mode = self._prepare_chat(payload)
        except ValueError as exc:
            emit("error", {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            emit("error", {"error": f"{type(exc).__name__}: {exc}"})
            return

        thread_id = str(params["thread_id"])
        # Prefer the pre-clear snapshot; fall back to stash from _prepare_chat.
        if not previous_turn_id:
            previous_turn_id = str(
                (self.server.threads.get(thread_id) or {}).get("_prev_turn_id")
                or ""
            ).strip()
        emit(
            "status",
            {
                "phase": "starting",
                "label": "Connecting…",
                "thread_id": thread_id,
            },
        )

        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread/turn",
            "params": params,
        }
        future = asyncio.run_coroutine_threadsafe(self.server.handle(req), self._loop)
        after_seq = 0
        turn_id = ""
        # Belt-and-suspenders: surface thread pending_approval even if the
        # journal event was missed (Plan Build / request_approval share this).
        emitted_pending_aids: set[str] = set()
        try:
            while not future.done():
                state = self._thread_state(thread_id)
                if not turn_id:
                    candidate = str(state.get("turn_id") or "").strip()
                    # Wait for a *new* turn_id after this stream starts — never
                    # latch a leftover id from the prior turn on this thread.
                    if (
                        candidate
                        and candidate != previous_turn_id
                    ):
                        turn_id = candidate
                        after_seq = 0
                        emit(
                            "status",
                            {
                                "phase": "running",
                                "label": "Working…",
                                "turn_id": turn_id,
                                "session_id": state.get("run_id"),
                                "thread_id": thread_id,
                            },
                        )
                if turn_id:
                    after_seq = self._emit_turn_events(
                        emit,
                        turn_id=turn_id,
                        after_sequence=after_seq,
                        seen_approval_ids=emitted_pending_aids,
                    )
                pending = state.get("pending_approval")
                if isinstance(pending, dict):
                    aid = str(pending.get("approval_id") or "").strip()
                    if aid and aid not in emitted_pending_aids:
                        emitted_pending_aids.add(aid)
                        frame = _stream_frame(
                            kind="approval_required",
                            payload={
                                "approval_id": aid,
                                "action": pending.get("action") or "",
                                "detail": pending.get("detail") or "",
                                "risk_class": pending.get("risk_class") or "",
                            },
                            sequence=None,
                            turn_id=turn_id or "",
                            session_id=str(state.get("run_id") or ""),
                        )
                        emit("event", frame)
                        emit(
                            "status",
                            {
                                "phase": "approval_required",
                                "label": str(
                                    frame.get("label") or "Waiting for approval…"
                                ),
                                "detail": list(frame.get("detail") or [])[:2],
                                "turn_id": turn_id or "",
                                "session_id": state.get("run_id"),
                                "thread_id": thread_id,
                            },
                        )
                time.sleep(max(0.05, float(poll_interval)))

            resp = future.result(timeout=5)
        except Exception as exc:  # noqa: BLE001
            emit("error", {"error": f"{type(exc).__name__}: {exc}"})
            return

        if isinstance(resp, dict) and "error" in resp:
            err = resp["error"]
            detail = err.get("data") if isinstance(err, dict) else None
            emit(
                "error",
                {
                    "error": str(
                        err.get("message") if isinstance(err, dict) else err
                    ),
                    "detail": detail if isinstance(detail, dict) else {},
                },
            )
            return

        result = (resp or {}).get("result") if isinstance(resp, dict) else None
        if not isinstance(result, dict):
            emit("error", {"error": "empty turn result"})
            return

        # Prefer the turn id from the completed result — never a prior leftover.
        result_turn = str(result.get("turn_id") or "").strip()
        if result_turn and result_turn != previous_turn_id:
            if turn_id != result_turn:
                turn_id = result_turn
                after_seq = 0
        elif not turn_id:
            state = self._thread_state(thread_id)
            candidate = str(state.get("turn_id") or "").strip()
            if candidate and candidate != previous_turn_id:
                turn_id = candidate
                after_seq = 0

        if turn_id and turn_id != previous_turn_id:
            after_seq = self._emit_turn_events(
                emit,
                turn_id=turn_id,
                after_sequence=after_seq,
                seen_approval_ids=emitted_pending_aids,
            )

        message = str(result.get("message") or "")
        if message:
            _emit_text_deltas(emit, message)
            emit("message", {"text": message, "partial": False})

        run_id = str(result.get("run_id") or "").strip()
        if run_id:
            user_title_source = str(
                payload.get("message") or payload.get("task") or ""
            ).strip()
            self._maybe_set_session_title(
                run_id,
                user_title_source,
                assistant_message=message,
                artifact_paths=_artifact_paths_from_result(result),
            )

        done = self._chat_result_payload(
            thread_id,
            result,
            attachments=attachments,
            loop_mode=loop_mode,
        )
        emit("done", done)

    def _session_workspace(self, session_id: str):
        from kageha.harness.sandbox import SessionWorkspace

        sid = str(session_id or "").strip()
        if not _SAFE_SESSION_ID.fullmatch(sid):
            raise ValueError("invalid session id")
        return SessionWorkspace.create(sid)

    def _session_meta_path(self, session_id: str) -> Path:
        return self._session_workspace(session_id).root / "session.json"

    def _load_session_meta(self, session_id: str) -> dict[str, Any]:
        path = self._session_meta_path(session_id)
        if not path.is_file():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _save_session_meta(self, session_id: str, data: dict[str, Any]) -> None:
        path = self._session_meta_path(session_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def _stored_session_title(self, session_id: str) -> str | None:
        meta = self._load_session_meta(session_id)
        if "title" not in meta:
            return None
        return str(meta.get("title") or "")

    def _session_flags(self, session_id: str) -> dict[str, bool]:
        meta = self._load_session_meta(session_id)
        return {
            "pinned": bool(meta.get("pinned")),
            "archived": bool(meta.get("archived")),
        }

    def _delete_session(self, session_id: str) -> bool:
        """Remove session workspace directory and runtime store entry."""
        import shutil

        from kageha.config import sessions_dir

        sid = str(session_id or "").strip()
        if not _SAFE_SESSION_ID.fullmatch(sid):
            raise ValueError("invalid session id")
        root = (sessions_dir() / sid).resolve()
        base = sessions_dir().resolve()
        if not str(root).startswith(str(base) + "/") and root != base:
            raise ValueError("invalid session path")
        # Drop in-memory thread bindings for this session.
        thread_id = self._load_thread_binding(sid) or f"web-{sid}"
        self.server.threads.pop(thread_id, None)
        deleted_fs = False
        if root.is_dir():
            shutil.rmtree(root)
            deleted_fs = True
        # Also remove from the durable runtime store (journal / SQLite).
        try:
            from kageha.runtime.store import RuntimeStore
            store = RuntimeStore()
            try:
                with store._lock:
                    store._conn.execute("DELETE FROM events WHERE session_id=?", (sid,))
                    store._conn.execute("DELETE FROM tool_attempts WHERE session_id=?", (sid,))
                    store._conn.execute("DELETE FROM checkpoints WHERE session_id=?", (sid,))
                    store._conn.execute("DELETE FROM artifact_manifest WHERE session_id=?", (sid,))
                    store._conn.execute("DELETE FROM approvals WHERE session_id=?", (sid,))
                    store._conn.execute("DELETE FROM turns WHERE session_id=?", (sid,))
                    store._conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
            finally:
                store.close()
        except Exception:  # noqa: BLE001
            pass
        return deleted_fs

    def _truncate_session(self, session_id: str, message_index: int) -> dict[str, Any]:
        """Truncate session history at the given message index (0-based).

        All messages from ``message_index`` onward are removed from chat.jsonl.
        Also drops corresponding _turns/*.json files.
        """
        from kageha.harness.sandbox import SessionWorkspace

        sid = str(session_id or "").strip()
        try:
            ws = SessionWorkspace.create(sid)
        except (FileNotFoundError, KeyError, OSError):
            return {"ok": False, "error": "session not found"}
        root = ws.root

        # Truncate chat.jsonl
        chat_path = root / "chat.jsonl"
        removed = 0
        if chat_path.is_file():
            lines = chat_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if 0 <= message_index < len(lines):
                kept = lines[:message_index]
                removed = len(lines) - message_index
                chat_path.write_text(
                    "\n".join(kept) + ("\n" if kept else ""),
                    encoding="utf-8",
                )

        # Truncate _turns/*.json (each file = 1 user+assistant pair = 2 messages)
        turns_dir = root / "_turns"
        if turns_dir.is_dir():
            try:
                paths = sorted(
                    (p for p in turns_dir.glob("*.json") if p.is_file()),
                    key=lambda p: (p.stat().st_mtime_ns, p.name),
                )
                turn_index = message_index // 2
                for p in paths[turn_index:]:
                    p.unlink(missing_ok=True)
            except OSError:
                pass

        # Clear in-memory thread state so reattach reloads clean history.
        thread_id = self._load_thread_binding(sid) or f"web-{sid}"
        if thread_id in self.server.threads:
            st = self.server.threads[thread_id]
            if isinstance(st, dict) and "messages" in st:
                st["messages"] = st["messages"][:message_index]

        return {"ok": True, "session_id": sid, "removed": removed}

    def _maybe_set_session_title(
        self,
        session_id: str,
        message: str,
        *,
        assistant_message: str = "",
        artifact_paths: list[str] | None = None,
    ) -> str | None:
        """Set or upgrade an auto title (never overwrites a manual rename)."""
        if not session_id or not _SAFE_SESSION_ID.fullmatch(session_id):
            return None
        from kageha.session_title import apply_session_title, pick_best_title

        paths = list(artifact_paths or [])
        # Peek at on-disk deliverables for weak chats that already produced files.
        try:
            root = self._session_workspace(session_id).root
            arts = root / "artifacts"
            if arts.is_dir():
                for path in arts.rglob("*"):
                    if path.is_file():
                        try:
                            paths.append(path.relative_to(root).as_posix())
                        except ValueError:
                            continue
        except Exception:  # noqa: BLE001
            pass

        candidate = pick_best_title(
            user_message=message,
            assistant_message=assistant_message,
            artifact_paths=paths,
        )
        meta = self._load_session_meta(session_id)
        meta["session_id"] = session_id
        meta.setdefault("thread_id", f"web-{session_id}")
        meta, changed = apply_session_title(meta, candidate=candidate)
        if changed:
            self._save_session_meta(session_id, meta)
        title = str(meta.get("title") or "").strip()
        return title or None

    def _session_todo_board_payload(
        self, session_id: str
    ) -> dict[str, Any] | None:
        """Parsed todo.md checklist for the live Build board (or None).

        Hidden while Design is awaiting Build (plan.md present, not approved)
        so the board only surfaces after Build starts — or whenever todo.md
        appears outside a gated Plan/Spec turn.
        """
        from kageha.loop.todo_board import parse_todo_file

        try:
            ws = self._session_workspace(session_id)
        except ValueError:
            return None
        root = ws.root
        has_design = (root / "plan.md").is_file()
        approved = (root / "plan_approved.flag").is_file()
        if has_design and not approved:
            return None
        return parse_todo_file(root / "todo.md", label="todos")

    def _session_design_payload(self, session_id: str) -> dict[str, Any]:
        """Plan design artifacts for the session (plan.md + explore notes)."""
        ws = self._session_workspace(session_id)
        root = ws.root
        files: dict[str, str] = {}
        for name in ("plan.md", "explore_notes.md"):
            path = root / name
            if path.is_file():
                try:
                    files[name] = path.read_text(encoding="utf-8", errors="replace")[
                        :_MAX_DESIGN_FILE_CHARS
                    ]
                except OSError:
                    continue
        agent_mode = "plan" if "plan.md" in files else "normal"
        approved = (root / "plan_approved.flag").is_file()
        phases = ["explore", "plan", "build"]
        thread_id = str(
            self._load_thread_binding(session_id) or f"web-{session_id}"
        )
        pending = (self.server.threads.get(thread_id) or {}).get(
            "pending_approval"
        )
        # Sticky Build whenever design artifacts exist and are not approved.
        awaiting_build = bool(files) and not approved
        explore_status: dict[str, Any] = {}
        status_path = root / "explore_status.json"
        if status_path.is_file():
            try:
                raw = json.loads(
                    status_path.read_text(encoding="utf-8", errors="replace")
                )
                if isinstance(raw, dict):
                    explore_status = {
                        "status": str(raw.get("status") or ""),
                        "message": str(raw.get("message") or "")[:400],
                        "degraded": bool(
                            raw.get("degraded")
                            or raw.get("status") == "skipped"
                        ),
                        "from": raw.get("from"),
                        "to": raw.get("to"),
                    }
            except (OSError, ValueError, TypeError):
                explore_status = {}
        return {
            "session_id": session_id,
            "agent_mode": agent_mode,
            "phases": phases,
            "files": files,
            "editable": sorted(_DESIGN_EDITABLE_NAMES),
            "approved": approved,
            "awaiting_build": awaiting_build,
            "awaiting_clarify": bool(
                (root / "clarify_pending.json").is_file()
            ),
            "pending_approval": pending if isinstance(pending, dict) else None,
            "explore_status": explore_status or None,
            "explore_degraded": bool(
                explore_status.get("degraded") if explore_status else False
            ),
            "clarify_status": None,
        }

    def _save_session_design(
        self, session_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist allowlisted plan design markdown into the session workspace.

        Writes only session artifacts (plan.md / explore_notes.md).
        Does not touch the project root. Locked after Build (plan_approved.flag).
        """
        ws = self._session_workspace(session_id)
        root = ws.root
        root.mkdir(parents=True, exist_ok=True)
        if (root / "plan_approved.flag").is_file():
            raise ValueError("design is locked after Build")

        updates: dict[str, str] = {}
        raw_files = payload.get("files")
        if isinstance(raw_files, dict) and raw_files:
            for key, value in raw_files.items():
                updates[str(key)] = str(value)
        else:
            name = str(
                payload.get("file") or payload.get("name") or ""
            ).strip()
            if not name:
                raise ValueError("file is required")
            if "content" in payload:
                content = payload.get("content")
            elif "text" in payload:
                content = payload.get("text")
            else:
                raise ValueError("content is required")
            updates[name] = str(content if content is not None else "")

        if not updates:
            raise ValueError("no design files to save")

        saved: list[str] = []
        for name, content in updates.items():
            if name not in _DESIGN_EDITABLE_NAMES:
                raise ValueError(f"file not editable: {name}")
            if len(content) > _MAX_DESIGN_FILE_CHARS:
                raise ValueError(
                    f"{name} exceeds {_MAX_DESIGN_FILE_CHARS} characters"
                )
            # Path safety: basename allowlist only (no directories).
            if Path(name).name != name or "/" in name or "\\" in name:
                raise ValueError(f"invalid design file: {name}")
            path = root / name
            path.write_text(content, encoding="utf-8")
            saved.append(name)

        out = self._session_design_payload(session_id)
        out["saved"] = saved
        return out

    def _mention_texts_for_session(self, session_id: str) -> str:
        """Join result.md + recent chat for deliverable filename discovery."""
        chunks: list[str] = []
        try:
            ws = self._session_workspace(session_id)
        except ValueError:
            return ""
        for name in ("result.md", "todo.md"):
            path = ws.root / name
            if path.is_file():
                try:
                    chunks.append(path.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    pass
        chat = ws.root / "chat.jsonl"
        if chat.is_file():
            try:
                lines = chat.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines = []
            for line in lines[-12:]:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    chunks.append(str(row.get("content") or row.get("text") or ""))
        return "\n".join(chunks)

    def _sync_project_deliverables_into_session(self, session_id: str) -> list[str]:
        """Copy project-root deliverables mentioned in the session into artifacts/.

        Agents often write under ``project_root`` (the repo) while the WebUI only
        serves ``~/.kageha/sessions/{id}``. This bridges that gap for downloads.
        """
        import shutil

        try:
            ws = self._session_workspace(session_id)
        except ValueError:
            return []
        project = Path(self.project_root or Path.cwd()).expanduser()
        try:
            project = project.resolve()
        except OSError:
            return []
        if not project.is_dir() or project == ws.root.resolve():
            return []
        text = self._mention_texts_for_session(session_id)
        names: list[str] = []
        seen: set[str] = set()
        for match in _DELIVERABLE_NAME_RE.finditer(text or ""):
            raw = (match.group(1) or "").replace("\\", "/").lstrip("./")
            if not raw or raw in seen:
                continue
            seen.add(raw)
            names.append(raw)
        # Also pick top-level project deliverables newer than session start when
        # the chat mentioned a matching basename (covers bare `deck.pptx`).
        mirrored: list[str] = []
        for raw in names:
            ext = Path(raw).suffix.lower()
            if ext not in _DELIVERABLE_EXTS:
                continue
            candidates = [
                project / raw,
                project / Path(raw).name,
            ]
            src = next((p for p in candidates if p.is_file()), None)
            if src is None:
                continue
            try:
                if not str(src.resolve()).startswith(str(project)):
                    continue
            except OSError:
                continue
            dest_rel = (
                raw
                if raw.startswith("artifacts/")
                else f"artifacts/{Path(raw).name}"
            )
            try:
                dest = ws.path(dest_rel)
            except ValueError:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                if (
                    not dest.is_file()
                    or dest.stat().st_size != src.stat().st_size
                    or int(dest.stat().st_mtime) != int(src.stat().st_mtime)
                ):
                    shutil.copy2(src, dest)
                mirrored.append(dest_rel)
            except OSError:
                continue
        return mirrored

    def _session_artifacts_payload(self, session_id: str) -> dict[str, Any]:
        # Pull project-root decks/docs into the session before listing.
        try:
            self._sync_project_deliverables_into_session(session_id)
        except Exception:  # noqa: BLE001
            pass
        from kageha.loop.artifacts import is_user_artifact

        ws = self._session_workspace(session_id)
        root = ws.root
        rows: list[tuple[int, str, dict[str, Any]]] = []
        if root.is_dir():
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    rel = path.relative_to(root).as_posix()
                except ValueError:
                    continue
                parts = rel.split("/")
                if any(part in _ARTIFACT_SKIP_DIRS for part in parts):
                    continue
                if path.name in _ARTIFACT_SKIP_NAMES:
                    continue
                # Keep the drawer session-bound to user deliverables — not
                # mirrored dependency trees or agent bookkeeping.
                if not is_user_artifact(rel):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                priority = (
                    0
                    if path.name in _DESIGN_ARTIFACT_NAMES
                    or any(
                        rel.startswith(prefix)
                        for prefix in _ARTIFACT_PREFERRED_PREFIXES
                    )
                    else 1
                )
                name = path.name
                rows.append(
                    (
                        priority,
                        rel,
                        {
                            "path": rel,
                            "name": name,
                            "kind": _artifact_file_kind(path),
                            "size": int(stat.st_size),
                            "mtime": float(stat.st_mtime),
                            "url": (
                                f"/api/sessions/{session_id}/files/"
                                f"{rel}"
                            ),
                        },
                    )
                )
        rows.sort(key=lambda item: (item[0], item[1]))
        return {
            "session_id": session_id,
            "artifacts": [item[2] for item in rows],
        }

    def _persist_thread_binding(self, session_id: str, thread_id: str) -> None:
        """Remember thread_id ↔ session_id so reopen continues the same chat."""
        data = self._load_session_meta(session_id)
        data["session_id"] = session_id
        data["thread_id"] = thread_id
        self._save_session_meta(session_id, data)

    def _load_thread_binding(self, session_id: str) -> str | None:
        path = self._session_meta_path(session_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        thread_id = str(data.get("thread_id") or "").strip()
        return thread_id or None

    def _resolve_session_file(self, session_id: str, relpath: str) -> Path:
        ws = self._session_workspace(session_id)
        rel = unquote(str(relpath or "")).replace("\\", "/").lstrip("/")
        if not rel or rel.startswith("/") or "\x00" in rel:
            raise ValueError("invalid path")
        parts = [p for p in rel.split("/") if p and p != "."]
        if not parts or any(p == ".." for p in parts):
            raise ValueError("path traversal denied")
        # Block access to internal journal / memory dirs.
        if parts[0] in {"_memory", ".git"}:
            raise KeyError("file not found")
        safe_rel = "/".join(parts)
        target = ws.path(safe_rel)
        if target.is_file():
            return target
        # Fallback: agent wrote under project_root — sync then re-resolve.
        name = parts[-1]
        ext = Path(name).suffix.lower()
        if ext in _DELIVERABLE_EXTS:
            try:
                self._sync_project_deliverables_into_session(session_id)
            except Exception:  # noqa: BLE001
                pass
            if target.is_file():
                return target
            # Direct project-root read for bare / artifacts/ basename.
            project = Path(self.project_root or Path.cwd()).expanduser()
            try:
                project = project.resolve()
            except OSError:
                project = Path()
            for candidate in (project / name, project / safe_rel):
                try:
                    resolved = candidate.resolve()
                except OSError:
                    continue
                if (
                    resolved.is_file()
                    and str(resolved).startswith(str(project))
                    and resolved.suffix.lower() in _DELIVERABLE_EXTS
                ):
                    return resolved
        raise KeyError("file not found")

    def _serve_session_file(
        self, session_id: str, relpath: str, *, download: bool = False
    ) -> tuple[int, bytes, str, dict[str, str]]:
        target = self._resolve_session_file(session_id, relpath)
        data = target.read_bytes()
        ctype = _session_file_mimetype(target, data)
        ext = target.suffix.lower()
        extra: dict[str, str] = {}
        filename = target.name
        if download:
            extra["Content-Disposition"] = f'attachment; filename="{filename}"'
        elif ext in _PDF_EXTS:
            extra["Content-Disposition"] = f'inline; filename="{filename}"'
        elif ext in (_OFFICE_EXTS | _ARCHIVE_EXTS):
            extra["Content-Disposition"] = f'attachment; filename="{filename}"'
        return 200, data, ctype, extra

    def _upload_session_file(
        self, session_id: str, body: bytes, headers: dict[str, str]
    ) -> tuple[int, bytes, str]:
        if len(body) > _MAX_UPLOAD_BYTES:
            raise ValueError(f"upload exceeds {_MAX_UPLOAD_BYTES} bytes")
        ctype = ""
        for key, val in headers.items():
            if key.lower() == "content-type":
                ctype = val
                break
        if "multipart/form-data" not in (ctype or "").lower():
            raise ValueError("expected multipart/form-data")
        _fields, files = _parse_multipart(body, ctype)
        if not files:
            raise ValueError("no file in upload")
        uploaded = files[0]
        raw_name = str(uploaded.get("filename") or "upload.bin")
        safe = _safe_filename(raw_name)
        ws = self._session_workspace(session_id)
        inputs = ws.root / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        dest_rel = f"inputs/{safe}"
        dest = ws.path(dest_rel)
        if dest.exists():
            stem = Path(safe).stem
            suffix = Path(safe).suffix
            dest_rel = f"inputs/{stem}_{uuid.uuid4().hex[:6]}{suffix}"
            dest = ws.path(dest_rel)
        dest.write_bytes(uploaded["data"])
        ext = Path(dest_rel).suffix.lower()
        return _json_bytes(
            {
                "session_id": session_id,
                "path": dest_rel,
                "filename": Path(dest_rel).name,
                "bytes": len(uploaded["data"]),
                "content_type": uploaded.get("content_type")
                or mimetypes.guess_type(dest_rel)[0]
                or "application/octet-stream",
                "kind": (
                    "image"
                    if ext in _IMAGE_EXTS
                    else "video"
                    if ext in _VIDEO_EXTS
                    else "audio"
                    if ext in _AUDIO_EXTS
                    else "file"
                ),
            }
        )

    def _session_stt(
        self, session_id: str, body: bytes, headers: dict[str, str]
    ) -> tuple[int, bytes, str]:
        """Transcribe an uploaded mic recording (multipart audio)."""
        from kageha.models.stt import transcribe_audio

        if len(body) > _MAX_UPLOAD_BYTES:
            raise ValueError(f"audio exceeds {_MAX_UPLOAD_BYTES} bytes")
        # Ensure session workspace exists.
        self._session_workspace(session_id)
        ctype = ""
        for key, val in headers.items():
            if key.lower() == "content-type":
                ctype = val
                break
        if "multipart/form-data" not in (ctype or "").lower():
            raise ValueError("expected multipart/form-data with audio file")
        fields, files = _parse_multipart(body, ctype)
        if not files:
            raise ValueError("no audio file in upload")
        uploaded = files[0]
        raw_name = str(uploaded.get("filename") or "voice.webm")
        suffix = Path(_safe_filename(raw_name)).suffix.lower() or ".webm"
        if suffix not in {
            ".webm",
            ".wav",
            ".mp3",
            ".m4a",
            ".ogg",
            ".mpeg",
            ".mp4",
        }:
            suffix = ".webm"
        fd, tmp_name = tempfile.mkstemp(prefix="kageha-stt-", suffix=suffix)
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            tmp.write_bytes(uploaded["data"])
            future = asyncio.run_coroutine_threadsafe(
                transcribe_audio(tmp, language=str(fields.get("language") or "")),
                self._loop,
            )
            try:
                text = future.result(timeout=120)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"transcription failed: {exc}") from exc
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return _json_bytes(
            {
                "session_id": session_id,
                "text": str(text or "").strip(),
            }
        )

    def _session_tts(
        self, session_id: str, body: bytes
    ) -> tuple[int, bytes, str, dict[str, str]]:
        """Synthesize WAV for spoken assistant replies / previews."""
        from kageha.chat.voice_io import synthesize_reply_wav

        # Touch session workspace (validates id).
        self._session_workspace(session_id)
        payload = self._json_body(body)
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValueError("text is required")
        fd, tmp_name = tempfile.mkstemp(prefix="kageha-tts-", suffix=".wav")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            future = asyncio.run_coroutine_threadsafe(
                synthesize_reply_wav(text, tmp), self._loop
            )
            try:
                future.result(timeout=120)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"TTS failed: {exc}") from exc
            data = tmp.read_bytes()
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return (
            200,
            data,
            "audio/wav",
            {"Content-Disposition": 'inline; filename="reply.wav"'},
        )

    def _load_messages(self, session_id: str) -> list[dict[str, str]]:
        from kageha.chat.history import load_chat_records
        from kageha.harness.sandbox import SessionWorkspace

        try:
            ws = SessionWorkspace.create(session_id)
        except (FileNotFoundError, KeyError, OSError):
            return []
        return load_chat_records(ws, limit=80)

    def _memory_list(self, query: dict[str, list[str]]) -> dict[str, Any]:
        kind = self._q(query, "kind").strip().lower()
        state = self._q(query, "state")
        scope = self._q(query, "scope") or self._q(query, "scope_type")
        session_id = self._q(query, "session_id")
        limit = self._qi(query, "limit", 100)
        project_root = self._q(query, "project_root") or str(Path.cwd())

        if kind == EPISODIC_KIND:
            episodes = self.server.memory.store.list_episodes(limit=limit)
            if session_id:
                episodes = [ep for ep in episodes if ep.session_id == session_id]
            return {
                "kind_filter": kind,
                "items": [
                    {
                        "type": "episode",
                        "id": ep.id,
                        "kind": EPISODIC_KIND,
                        "content": ep.summary or ep.task,
                        "task": ep.task,
                        "summary": ep.summary,
                        "state": "episode",
                        "scope_type": "session",
                        "session_id": ep.session_id,
                        "turn_id": ep.turn_id,
                        "status": ep.status,
                        "verified": ep.verified,
                        "provenance": {
                            "source_session_id": ep.session_id,
                            "source_turn_id": ep.turn_id,
                            "verified": ep.verified,
                            "status": ep.status,
                            "project_key": ep.project_key,
                            "channel_key": ep.channel_key,
                            "user_id": ep.user_id,
                            "agent_id": ep.agent_id,
                        },
                        "created_at": ep.created_at,
                        "score": None,
                    }
                    for ep in episodes
                ],
            }

        rows = self.rpc(
            "memory/list",
            {
                "state": state,
                "scope": scope,
                "session_id": session_id,
                "project_root": project_root if scope == "project" else "",
                "limit": limit,
                "user_id": self._q(query, "user_id", "local"),
                "agent_id": self._q(query, "agent_id", "main"),
                "channel_key": self._q(query, "channel_key"),
            },
        )
        items = []
        for row in rows:
            if kind and str(row.get("kind") or "").lower() != kind:
                continue
            items.append(self._semantic_card(row))
        return {"kind_filter": kind or None, "items": items}

    def _memory_recall(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query") or "").strip()
        kind = str(payload.get("kind") or "").strip().lower()
        kinds_raw = payload.get("kinds") or ([] if not kind else [kind])
        kinds = {str(k).strip().lower() for k in kinds_raw if str(k).strip()}
        session_id = str(payload.get("session_id") or "")
        project_root = str(payload.get("project_root") or Path.cwd())
        max_results = int(payload.get("max_results") or 20)
        state = str(payload.get("state") or "")

        want_episodic = (not kinds) or (EPISODIC_KIND in kinds)
        semantic_kinds = {k for k in kinds if k and k != EPISODIC_KIND}

        items: list[dict[str, Any]] = []
        trace_id = ""
        context_text = ""

        if query:
            recalled = self.rpc(
                "memory/recall",
                {
                    "query": query,
                    "project_root": project_root,
                    "session_id": session_id,
                    "user_id": str(payload.get("user_id") or "local"),
                    "agent_id": str(payload.get("agent_id") or "main"),
                    "channel_key": str(payload.get("channel_key") or ""),
                    "max_results": max_results,
                },
            )
            trace_id = str(recalled.get("trace_id") or "")
            context_text = str(recalled.get("context") or "")

            if not semantic_kinds or semantic_kinds & {
                MemoryKind.INSTRUCTION.value,
                MemoryKind.PREFERENCE.value,
                MemoryKind.USER_FACT.value,
            }:
                for row in recalled.get("instructions") or []:
                    card = self._semantic_card(row, source="recall:instructions")
                    if not semantic_kinds or card["kind"] in semantic_kinds:
                        items.append(card)

            if not semantic_kinds or semantic_kinds & {
                MemoryKind.PROJECT_FACT.value,
                MemoryKind.DECISION.value,
                MemoryKind.ARTIFACT_FACT.value,
                MemoryKind.PROCEDURE_CANDIDATE.value,
            }:
                for row in recalled.get("project") or []:
                    card = self._semantic_card(row, source="recall:project")
                    if not semantic_kinds or card["kind"] in semantic_kinds:
                        items.append(card)

            if want_episodic:
                for row in recalled.get("episodes") or []:
                    items.append(self._episode_card(row, source="recall:episodes"))

            # Include candidates / other states via inspect when requested.
            if state and state != MemoryState.CONFIRMED.value:
                listed = self._memory_list(
                    {
                        "kind": [""],
                        "state": [state],
                        "session_id": [session_id],
                        "project_root": [project_root],
                        "limit": [str(max_results)],
                    }
                )
                qlow = query.lower()
                for card in listed["items"]:
                    if qlow in str(card.get("content") or "").lower():
                        if not semantic_kinds or card.get("kind") in semantic_kinds:
                            items.append(card)
        else:
            # Browse mode — list by kind/state without a recall query.
            listed = self._memory_list(
                {
                    "kind": [kind or (next(iter(semantic_kinds)) if len(semantic_kinds) == 1 else "")],
                    "state": [state],
                    "session_id": [session_id],
                    "project_root": [project_root],
                    "limit": [str(max_results)],
                    "scope": [str(payload.get("scope") or "")],
                }
            )
            items.extend(listed["items"])
            if want_episodic and kind != EPISODIC_KIND and EPISODIC_KIND in kinds:
                eps = self._memory_list(
                    {
                        "kind": [EPISODIC_KIND],
                        "session_id": [session_id],
                        "limit": [str(max_results)],
                    }
                )
                items.extend(eps["items"])

        # Deduplicate by id while preserving order.
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for item in items:
            mid = str(item.get("id") or "")
            if mid and mid in seen:
                continue
            if mid:
                seen.add(mid)
            deduped.append(item)

        return {
            "query": query,
            "kinds": sorted(kinds) if kinds else MEMORY_KINDS,
            "trace_id": trace_id,
            "context": context_text,
            "items": deduped[: max(1, max_results * 3)],
        }

    def _semantic_card(
        self, row: dict[str, Any], *, source: str = "inspect"
    ) -> dict[str, Any]:
        return {
            "type": "memory",
            "id": row.get("id"),
            "kind": row.get("kind"),
            "content": row.get("content"),
            "state": row.get("state"),
            "scope_type": row.get("scope_type"),
            "scope_key": row.get("scope_key"),
            "confidence": row.get("confidence"),
            "score": None,
            "source": source,
            "session_id": row.get("source_session_id") or "",
            "provenance": {
                "source_role": row.get("source_role"),
                "source_session_id": row.get("source_session_id"),
                "source_turn_id": row.get("source_turn_id"),
                "source_artifact": row.get("source_artifact"),
                "verification_evidence": row.get("verification_evidence"),
                "sensitivity": row.get("sensitivity"),
                "claim_key": row.get("claim_key"),
                "supersedes_id": row.get("supersedes_id"),
                "project_key": row.get("project_key"),
                "channel_key": row.get("channel_key"),
                "user_id": row.get("user_id"),
                "agent_id": row.get("agent_id"),
            },
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def _episode_card(
        self, row: dict[str, Any], *, source: str = "recall:episodes"
    ) -> dict[str, Any]:
        # recall returns asdict(EpisodeRecord) nested under RecallItem in app_server —
        # app_server uses asdict(item.record), so row is the episode fields.
        return {
            "type": "episode",
            "id": row.get("id"),
            "kind": EPISODIC_KIND,
            "content": row.get("summary") or row.get("task"),
            "task": row.get("task"),
            "summary": row.get("summary"),
            "state": "episode",
            "scope_type": "session",
            "session_id": row.get("session_id"),
            "turn_id": row.get("turn_id"),
            "status": row.get("status"),
            "verified": row.get("verified"),
            "score": None,
            "source": source,
            "provenance": {
                "source_session_id": row.get("session_id"),
                "source_turn_id": row.get("turn_id"),
                "verified": row.get("verified"),
                "status": row.get("status"),
                "project_key": row.get("project_key"),
                "channel_key": row.get("channel_key"),
                "user_id": row.get("user_id"),
                "agent_id": row.get("agent_id"),
            },
            "created_at": row.get("created_at"),
        }

    def _react_asset(self, rel: str) -> tuple[int, bytes, str] | None:
        root = react_dist_root()
        if root is None:
            return None
        target = (root / rel).resolve()
        if not str(target).startswith(str(root)) or not target.is_file():
            return None
        data = target.read_bytes()
        ctype, _ = mimetypes.guess_type(str(target))
        if rel.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        elif rel.endswith(".js"):
            ctype = "text/javascript; charset=utf-8"
        elif rel.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        return 200, data, ctype or "application/octet-stream"


def make_handler(app: WebUIApp):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            # Keep console quiet; CLI prints the listen URL.
            return

        def _read_body(self) -> bytes | None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_UPLOAD_BYTES + 1024:
                self.send_response(413)
                msg = b'{"error":"payload too large"}'
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
                return None
            return self.rfile.read(length) if length > 0 else b""

        def _dispatch(self) -> None:
            parsed = urlparse(self.path)
            body = self._read_body()
            if body is None:
                return
            if self.command == "POST" and parsed.path == "/api/chat/stream":
                self._stream_chat(body)
                return
            hdrs = {k: v for k, v in self.headers.items()}
            result = app.handle(
                self.command,
                parsed.path,
                parse_qs(parsed.query),
                body,
                hdrs,
            )
            resp_headers: dict[str, str] = {}
            if len(result) >= 4:
                status, data, ctype, resp_headers = (
                    result[0],
                    result[1],
                    result[2],
                    result[3] or {},
                )
            else:
                status, data, ctype = result[0], result[1], result[2]
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            for key, val in resp_headers.items():
                self.send_header(key, val)
            self.end_headers()
            self.wfile.write(data)

        def _stream_chat(self, body: bytes) -> None:
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            # Close after the stream: HTTP/1.1 keep-alive without Content-Length
            # leaves clients hanging after the final `done` event.
            self.send_header("Connection", "close")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            closed = False

            def emit(event: str, data: dict[str, Any]) -> None:
                nonlocal closed
                if closed:
                    return
                try:
                    self.wfile.write(_sse_bytes(event, data))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    closed = True

            try:
                app.stream_chat(body, emit)
            except Exception as exc:  # noqa: BLE001
                emit("error", {"error": f"{type(exc).__name__}: {exc}"})

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch()

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch()

        def do_PUT(self) -> None:  # noqa: N802
            self._dispatch()

        def do_PATCH(self) -> None:  # noqa: N802
            self._dispatch()

        def do_DELETE(self) -> None:  # noqa: N802
            self._dispatch()

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header(
                "Access-Control-Allow-Methods",
                "GET, POST, PUT, PATCH, DELETE, OPTIONS",
            )
            self.send_header(
                "Access-Control-Allow-Headers", "Content-Type, Content-Length"
            )
            self.end_headers()

    return Handler


def serve_webui(
    host: str = "127.0.0.1",
    port: int = 8788,
    *,
    open_browser: bool = False,
    attach: str | None = None,
    project_root: str | None = None,
) -> None:
    from kageha.app_server_client import open_app_server, resolve_attach_url

    attach_url = resolve_attach_url(attach)
    server = open_app_server(attach_url)
    app = WebUIApp(server=server, project_root=project_root)
    handler = make_handler(app)
    httpd = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    print(f"Kageha Web UI → {url}")
    react = react_dist_root()
    if react is not None:
        print(f"UI: React (Vite) · {react}")
    else:
        print(
            "UI: not built — run: cd src/kageha/webui/frontend && npm run build"
            " (or npm run dev on :5173)"
        )
    if attach_url:
        print(f"Attached App Server → {attach_url}")
    print(
        "API: /api/sessions  /api/chat  /api/chat/stream"
        "  /api/sessions/{id}/events  /api/approvals"
        "  /api/jobs[/{id}|/{id}/attach|/{id}/cancel]"
    )
    print(
        "Note: Auto-approve by default; toggle Ask in the UI for HITL Approve/Deny."
    )
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Web UI…")
    finally:
        httpd.server_close()
        app.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="kageha webui", description="Kageha Web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--open", action="store_true", help="Open browser")
    parser.add_argument(
        "--attach",
        default=None,
        help="Attach to App Server (unix:// | ws://127.0.0.1:PORT | auto)",
    )
    args = parser.parse_args(argv)
    serve_webui(
        host=args.host,
        port=args.port,
        open_browser=args.open,
        attach=args.attach,
    )


if __name__ == "__main__":
    main()
