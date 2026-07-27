"""Shared helpers and settings for the memory service."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from kageha.config import kageha_home
from kageha.memory.models import MemoryKind, MemoryScope, TurnMemoryInput

_STOP = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "create",
    "do",
    "for",
    "from",
    "help",
    "i",
    "in",
    "is",
    "it",
    "make",
    "me",
    "my",
    "of",
    "on",
    "or",
    "please",
    "that",
    "the",
    "this",
    "to",
    "we",
    "with",
    "you",
}
_SUCCESS = {"success"}
_SCOPE_AUTHORITY = {
    MemoryScope.SESSION.value: 4,
    MemoryScope.CHANNEL.value: 4,
    MemoryScope.PROJECT.value: 3,
    MemoryScope.AGENT.value: 2,
    MemoryScope.GLOBAL.value: 1,
}
_INSTRUCTION_RE = re.compile(
    r"(?i)\b(from now on|always|never|every time|standing instruction)\b"
)
_PREFERENCE_RE = re.compile(
    r"(?i)\b(?:i prefer|i like|my preference is|user prefers)\s+(.+)"
)
_USER_FACT_RE = re.compile(
    r"(?i)\b(?:my name is|i am based in|i live in|my timezone is|i work at)\s+(.+)"
)
_DECISION_RE = re.compile(
    r"(?i)\b(?:we decided|decision:|we chose|we will use|use .+ instead of)\b"
)
_PROJECT_FACT_RE = re.compile(
    r"(?i)\b(?:the project|this project|the repo|this repo|kageha)\b.{0,80}"
    r"\b(?:uses|has|is|requires|stores|runs)\b"
)
_REMEMBER_RE = re.compile(r"(?i)^\s*(?:please\s+)?remember(?:\s+that)?\s+(.+)$")
_FORGET_TARGET_RE = re.compile(
    r"(?i)^\s*(?:please\s+)?forget\s+(?!that\s*[.!]*$)(?:that\s+)?(.+?)\s*[.!]*\s*$"
)
_CORRECT_TARGET_RE = re.compile(
    r"(?i)^\s*(?:please\s+)?(?:correct|replace)\s+(?:memory\s+)?"
    r"([a-f0-9]{16,64})\s+(?:to|with)\s+(.+)$"
)
_CORRECTION_RE = re.compile(
    r"(?i)^\s*(?:that(?:'s| is) not true|that is wrong|you remembered that wrong|"
    r"forget that|forget what you just recalled)\s*[.!]*\s*$"
)


def _truthy(value: str) -> bool:
    return value.strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _runtime_setting(key: str, env_name: str, default: bool = True) -> bool:
    if env_name in os.environ:
        return _truthy(os.environ[env_name])
    path = kageha_home() / "memory" / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get(key)
        return bool(value) if isinstance(value, bool) else default
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def set_runtime_memory_setting(key: str, enabled: bool) -> Path:
    if key not in {"enabled", "learning_enabled"}:
        raise ValueError("memory setting must be enabled|learning_enabled")
    path = kageha_home() / "memory" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    data[key] = bool(enabled)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)
    return path


def memory_enabled() -> bool:
    return _runtime_setting("enabled", "KAGEHA_MEMORY_ENABLED", True)


def memory_learning_enabled() -> bool:
    return _runtime_setting("learning_enabled", "KAGEHA_MEMORY_LEARN", True)


def _llm_extract_mode() -> str:
    from kageha.memory.extract import llm_extract_mode

    return llm_extract_mode()


def project_key(project_root: str = "") -> str:
    root = Path(project_root or Path.cwd()).expanduser().resolve()
    return hashlib.sha256(str(root).encode()).hexdigest()[:20]


def private_channel_key(channel: str, identity: str) -> str:
    raw = f"{channel.strip().lower()}:{identity.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24] if raw.strip(":") else ""


def turn_memory_input_from_result(
    result: Any,
    *,
    task: str,
    user_text: str = "",
    project_root: str = "",
    user_id: str = "local",
    agent_id: str = "main",
    channel_key: str = "",
    learn: bool = True,
) -> TurnMemoryInput:
    """Build the single capture contract used by every execution surface."""
    turn_id = str(getattr(result, "turn_id", "") or "")
    run_id = str(getattr(result, "run_id", "") or "")
    return TurnMemoryInput(
        session_id=run_id,
        turn_id=turn_id,
        task=task,
        user_text=user_text or task,
        assistant_text=str(getattr(result, "message", "") or ""),
        status=str(getattr(result, "status", "") or "error"),
        verified=bool(getattr(result, "validated", False)),
        verified_facts=list(getattr(result, "verified_facts", []) or []),
        verification_evidence=str(
            getattr(result, "verification_evidence", "") or ""
        ),
        artifacts=list(
            getattr(result, "turn_artifacts", None)
            or getattr(result, "artifacts", [])
            or []
        ),
        project_root=project_root,
        project_scope_key=project_key(project_root),
        user_id=user_id,
        agent_id=agent_id,
        channel_key=channel_key,
        tool_calls=int(getattr(result, "steps", 0) or 0),
        idempotency_key=hashlib.sha256(
            f"capture:{run_id}:{turn_id}".encode()
        ).hexdigest(),
        learn=learn,
        recovered_failures=list(
            getattr(result, "recovered_failures", []) or []
        ),
    )


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9_]{3,}", (text or "").lower())
        if token not in _STOP
    ]


def _fts_query(text: str) -> str:
    unique: list[str] = []
    for token in _tokens(text):
        if token not in unique:
            unique.append(token)
    return " OR ".join(f'"{token}"' for token in unique[:16])


def _content_hash(content: str) -> str:
    normalized = re.sub(r"\s+", " ", content.strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()


def _claim_key(kind: str, content: str) -> str:
    normalized = re.sub(r"\s+", " ", content.strip().lower())
    relation = re.match(
        r"(.{2,100}?)\s+(uses|use|is|has|requires|stores|runs|prefers|should use)\s+(.+)",
        normalized,
    )
    if relation:
        base = f"{relation.group(1)}:{relation.group(2)}"
    elif kind == MemoryKind.INSTRUCTION.value:
        words = [w for w in _tokens(normalized) if w not in {"always", "never"}][:6]
        base = " ".join(words)
    elif kind == MemoryKind.PREFERENCE.value:
        words = _tokens(normalized.replace("user prefers", ""))[:5]
        base = " ".join(words)
    else:
        base = " ".join(_tokens(normalized)[:10]) or normalized[:80]
    return hashlib.sha256(f"{kind}:{base}".encode()).hexdigest()[:24]


def _infer_kind(content: str) -> str:
    text = (content or "").strip()
    if _PREFERENCE_RE.search(text):
        return MemoryKind.PREFERENCE.value
    if _INSTRUCTION_RE.search(text):
        return MemoryKind.INSTRUCTION.value
    if _USER_FACT_RE.search(text):
        return MemoryKind.USER_FACT.value
    if _DECISION_RE.search(text):
        return MemoryKind.DECISION.value
    if _PROJECT_FACT_RE.search(text):
        return MemoryKind.PROJECT_FACT.value
    return MemoryKind.PROJECT_FACT.value


def _default_scope(kind: str) -> str:
    if kind in {
        MemoryKind.PREFERENCE.value,
        MemoryKind.INSTRUCTION.value,
        MemoryKind.USER_FACT.value,
    }:
        return MemoryScope.GLOBAL.value
    return MemoryScope.PROJECT.value


def _scope_key(
    scope_type: str,
    *,
    user_id: str,
    agent_id: str,
    project: str,
    session_id: str,
    channel_key: str,
) -> str:
    return {
        MemoryScope.GLOBAL.value: user_id,
        MemoryScope.PROJECT.value: project,
        MemoryScope.SESSION.value: session_id,
        MemoryScope.AGENT.value: agent_id,
        MemoryScope.CHANNEL.value: channel_key,
    }.get(scope_type, project)


