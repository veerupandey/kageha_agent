"""`/memory` commands for the interactive chat."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from kageha.memory.models import MemoryMutation
from kageha.memory.service import MemoryService


@dataclass
class ChatMemorySettings:
    enabled: bool = True
    learning: bool = True


def _record_line(record: object) -> str:
    rid = str(getattr(record, "id", ""))
    state = str(getattr(record, "state", ""))
    scope = str(getattr(record, "scope_type", ""))
    content = str(getattr(record, "content", "")).replace("\n", " ")
    return f"{rid}  [{state}/{scope}]  {content[:180]}"


def handle_memory_command(
    line: str,
    *,
    service: MemoryService,
    settings: ChatMemorySettings,
    session_id: str = "",
    project_root: str = "",
    channel_key: str = "",
) -> tuple[bool, str]:
    """Handle a slash command and return ``(handled, user-facing output)``."""
    text = (line or "").strip()
    if not text.lower().startswith("/memory"):
        return False, ""
    rest = text[len("/memory") :].strip()
    if not rest:
        return True, (
            "Usage: /memory status|list|why|on|off|learn|remember|correct|forget|"
            "fetch|forgotten|prune|consolidate|import-rules|sync-rules"
        )
    command, _, arg = rest.partition(" ")
    command = command.lower()
    arg = arg.strip()
    root = project_root or str(Path.cwd())

    try:
        if command == "status":
            data = service.status()
            data["chat_recall_enabled"] = settings.enabled
            data["chat_learning_enabled"] = settings.learning
            return True, json.dumps(data, indent=2, sort_keys=True)
        if command in {"on", "off"}:
            settings.enabled = command == "on"
            return True, f"Memory recall {'on' if settings.enabled else 'off'} for this chat."
        if command == "learn":
            mode = arg.lower()
            if mode not in {"on", "off"}:
                return True, "Usage: /memory learn on|off"
            settings.learning = mode == "on"
            return True, f"Memory learning {'on' if settings.learning else 'off'} for this chat."
        if command == "list":
            scope = arg.lower() or ""
            if scope and scope not in {"global", "project", "session"}:
                return True, "Usage: /memory list [global|project|session]"
            records = service.inspect(
                scope_type=scope,
                project_root=root if scope == "project" else "",
                session_id=session_id if scope == "session" else "",
                channel_key=channel_key,
                limit=100,
            )
            return True, (
                "\n".join(_record_line(record) for record in records)
                if records
                else "(no matching memories)"
            )
        if command == "why":
            trace = service.latest_trace(session_id=session_id)
            return True, (
                json.dumps(trace.to_dict(), indent=2, sort_keys=True)
                if trace
                else "(no recall trace for this session yet)"
            )
        if command == "remember":
            if not arg:
                return True, "Usage: /memory remember <text>"
            record = service.mutate(
                MemoryMutation(
                    action="remember",
                    content=arg,
                    project_root=root,
                    session_id=session_id,
                    channel_key=channel_key,
                )
            )
            return True, "Remembered: " + _record_line(record)
        if command == "correct":
            target, _, replacement = arg.partition(" ")
            if not target or not replacement.strip():
                return True, "Usage: /memory correct <id> <replacement>"
            record = service.mutate(
                MemoryMutation(
                    action="correct",
                    target=target,
                    content=replacement.strip(),
                    project_root=root,
                    session_id=session_id,
                    channel_key=channel_key,
                )
            )
            return True, "Corrected: " + _record_line(record)
        if command == "forget":
            if not arg:
                return True, "Usage: /memory forget <id|text>"
            record = service.mutate(
                MemoryMutation(
                    action="forget",
                    target=arg,
                    project_root=root,
                    session_id=session_id,
                    channel_key=channel_key,
                )
            )
            return True, "Forgot: " + _record_line(record)
        if command == "fetch":
            if not arg:
                return True, "Usage: /memory fetch <id>"
            return True, json.dumps(service.fetch(arg), indent=2, sort_keys=True)
        if command == "forgotten":
            rows = service.forgotten(limit=30)
            return True, (
                json.dumps(rows, indent=2, sort_keys=True)
                if rows
                else "(nothing forgotten recently)"
            )
        if command == "prune":
            return True, json.dumps(service.prune_idle(), indent=2, sort_keys=True)
        if command == "consolidate":
            force = arg.lower() in {"force", "--force"}
            return True, json.dumps(
                service.consolidate(force=force), indent=2, sort_keys=True
            )
        if command in {"import-rules", "sync-rules"}:
            report = service.import_project_rules(
                root,
                session_id=session_id or "chat-import",
                channel_key=channel_key,
                sync=command == "sync-rules",
            )
            return True, json.dumps(report, indent=2, sort_keys=True)
        return True, (
            "Unknown memory command. Use "
            "/memory status|list|why|on|off|learn|remember|correct|forget|"
            "fetch|forgotten|prune|consolidate|import-rules|sync-rules"
        )
    except (RuntimeError, ValueError, KeyError) as exc:
        return True, f"Memory error: {exc}"
