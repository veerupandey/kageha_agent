"""Session chat history — load chat.jsonl into model context across turns."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from kageha.harness.sandbox import SessionWorkspace
from kageha.models.base import ChatMessage


def append_chat_log(workspace: SessionWorkspace, role: str, text: str) -> None:
    """Append one user/assistant line to the session ``chat.jsonl``."""
    path = workspace.root / "chat.jsonl"
    rec = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "role": role,
        "text": (text or "")[:8000],
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _load_chat_jsonl(root: Path) -> list[dict[str, str]]:
    path = root / "chat.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, str]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = str(item.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            rows.append({"role": role, "text": text})
    except OSError:
        return []
    return rows


def _load_turn_records(root: Path) -> list[dict[str, str]]:
    """Rebuild chat rows from durable `_turns/*.json` (web/runtime path)."""
    turns_dir = root / "_turns"
    if not turns_dir.is_dir():
        return []
    rows: list[dict[str, str]] = []
    try:
        paths = sorted(
            (p for p in turns_dir.glob("*.json") if p.is_file()),
            key=lambda p: (p.stat().st_mtime_ns, p.name),
        )
    except OSError:
        return []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        request = str(payload.get("request") or "").strip()
        answer = str(payload.get("answer") or "").strip()
        if request:
            rows.append({"role": "user", "text": request})
        if answer:
            rows.append({"role": "assistant", "text": answer})
    return rows


def load_chat_records(
    workspace: SessionWorkspace | Path | None,
    *,
    limit: int = 40,
) -> list[dict[str, str]]:
    """Return recent user/assistant turns (oldest → newest).

    Prefers ``chat.jsonl`` when present. Falls back to ``_turns/*.json`` so
    Web UI / journaled sessions that never wrote the chat log still reopen
    with history.
    """
    if workspace is None:
        return []
    root = workspace.root if isinstance(workspace, SessionWorkspace) else Path(workspace)
    chat_rows = _load_chat_jsonl(root)
    turn_rows = _load_turn_records(root)
    if chat_rows and (not turn_rows or len(chat_rows) >= len(turn_rows)):
        rows = chat_rows
    else:
        rows = turn_rows or chat_rows
    if not rows:
        return []
    return rows[-max(1, limit) :]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def drop_trailing_user(records: list[dict[str, str]], user_text: str | None) -> list[dict[str, str]]:
    """Drop the current user message if it was already appended to chat.jsonl."""
    if not records or not user_text:
        return records
    if records[-1]["role"] == "user" and _norm(records[-1]["text"]) == _norm(user_text):
        return records[:-1]
    return records


def format_chat_block(
    records: list[dict[str, str]],
    *,
    max_chars: int = 5000,
    per_msg: int = 700,
) -> str:
    """Human-readable conversation block for system/working notes."""
    if not records:
        return ""
    lines = ["## Recent conversation in this session"]
    for rec in records:
        role = "User" if rec["role"] == "user" else "Assistant"
        body = re.sub(r"\s+", " ", rec["text"]).strip()
        if len(body) > per_msg:
            body = body[: per_msg - 1].rstrip() + "…"
        lines.append(f"{role}: {body}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[-max_chars:]
        # avoid starting mid-line
        nl = text.find("\n")
        if nl > 0:
            text = text[nl + 1 :]
        text = "…\n" + text
    return text


def chat_as_messages(
    records: list[dict[str, str]],
    *,
    per_msg: int = 900,
    max_messages: int = 16,
) -> list[ChatMessage]:
    """Convert chat records to ChatMessage list for the model history prefix."""
    out: list[ChatMessage] = []
    for rec in records[-max_messages:]:
        body = (rec["text"] or "").strip()
        if len(body) > per_msg:
            body = body[: per_msg - 1].rstrip() + "…"
        out.append(ChatMessage(role=rec["role"], content=body))  # type: ignore[arg-type]
    return out


def session_continuity_extra(
    workspace: SessionWorkspace | None,
    *,
    current_user: str = "",
    limit: int = 20,
) -> str:
    """Block to inject into system_extra so follow-up turns keep conversational memory."""
    records = drop_trailing_user(
        load_chat_records(workspace, limit=limit),
        current_user,
    )
    block = format_chat_block(records)
    if not block or workspace is None:
        return block
    # Also list a few known artifact paths for grounding
    try:
        from kageha.loop.artifacts import classify_artifacts

        arts = classify_artifacts(workspace.list_files())[:8]
        if arts:
            block += "\n\n## Session files (reuse these)\n" + "\n".join(
                f"- {p}" for p in arts
            )
    except Exception:  # noqa: BLE001
        pass
    return block


def prior_history_messages(
    workspace: SessionWorkspace | None,
    *,
    current_user: str = "",
    limit: int = 16,
) -> list[ChatMessage]:
    records = drop_trailing_user(
        load_chat_records(workspace, limit=limit + 4),
        current_user,
    )
    return chat_as_messages(records, max_messages=limit)
