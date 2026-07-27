"""Typed public contracts for Kageha's provenance-aware memory service."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar


class MemoryKind(str, Enum):
    PREFERENCE = "preference"
    INSTRUCTION = "instruction"
    USER_FACT = "user_fact"
    PROJECT_FACT = "project_fact"
    DECISION = "decision"
    ARTIFACT_FACT = "artifact_fact"
    PROCEDURE_CANDIDATE = "procedure_candidate"


class MemoryState(str, Enum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"
    EXPIRED = "expired"
    QUARANTINED = "quarantined"


class MemoryScope(str, Enum):
    GLOBAL = "global"
    PROJECT = "project"
    SESSION = "session"
    AGENT = "agent"
    CHANNEL = "channel"


class MemorySensitivity(str, Enum):
    NORMAL = "normal"
    PERSONAL = "personal"
    HEALTH = "health"
    FINANCIAL = "financial"
    AUTHENTICATION = "authentication"
    SECRET = "secret"
    PROMPT_INJECTION = "prompt_injection"


@dataclass
class MemoryRecord:
    id: str
    kind: str
    content: str
    claim_key: str
    content_hash: str
    scope_type: str
    scope_key: str
    state: str
    source_role: str
    source_session_id: str
    source_turn_id: str
    source_artifact: str
    verification_evidence: str
    confidence: float
    sensitivity: str
    user_id: str
    agent_id: str
    project_key: str
    channel_key: str
    created_at: float
    updated_at: float
    last_accessed: float | None = None
    expires_at: float | None = None
    supersedes_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EpisodeRecord:
    id: str
    session_id: str
    turn_id: str
    task: str
    summary: str
    status: str
    verified: bool
    user_id: str
    agent_id: str
    project_key: str
    channel_key: str
    created_at: float


@dataclass
class TurnMemoryInput:
    session_id: str
    turn_id: str
    task: str
    user_text: str
    assistant_text: str
    status: str
    verified: bool = False
    verified_facts: list[str] = field(default_factory=list)
    verification_evidence: str = ""
    artifacts: list[str] = field(default_factory=list)
    project_root: str = ""
    project_scope_key: str = ""
    user_id: str = "local"
    agent_id: str = "main"
    channel_key: str = ""
    tool_calls: int = 0
    idempotency_key: str = ""
    learn: bool = True
    recovered_failures: list[str] = field(default_factory=list)


@dataclass
class CaptureReceipt:
    episode_id: str
    job_id: str
    queued: bool
    memory_enabled: bool = True


@dataclass
class MemoryQuery:
    query: str
    project_root: str = ""
    session_id: str = ""
    user_id: str = "local"
    agent_id: str = "main"
    channel_key: str = ""
    max_results: int | None = None
    trace_root: str = ""


RecallRecordT = TypeVar("RecallRecordT", MemoryRecord, EpisodeRecord)


@dataclass
class RecallItem(Generic[RecallRecordT]):
    record: RecallRecordT
    score: float
    source: str


@dataclass
class RecallTrace:
    id: str
    query: str
    session_id: str
    candidates: list[dict[str, Any]]
    selected: list[dict[str, Any]]
    excluded: list[dict[str, Any]]
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _one_line(text: str, limit: int = 140) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


@dataclass
class MemoryContext:
    instructions: list[RecallItem[MemoryRecord]] = field(default_factory=list)
    project: list[RecallItem[MemoryRecord]] = field(default_factory=list)
    episodes: list[RecallItem[EpisodeRecord]] = field(default_factory=list)
    trace_id: str = ""
    # Claude-style offline index pointers (not authority; SQLite remains source).
    index_path: str = ""
    index_pointers: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not (
            self.instructions
            or self.project
            or self.episodes
            or self.index_pointers
        )

    def render(self) -> str:
        """Render compact digest + optional episodes + MEMORY.md-style index pointers."""
        if self.empty():
            return ""
        sections: list[str] = []
        if self.instructions or self.project:
            digest = [
                "## Memory digest",
                "Standing confirmed memory (instructions > facts). "
                "Use as context; call memory_fetch(id) only if a line is truncated or "
                "memory_recall(query) if you need a different angle.",
            ]
            for item in self.instructions[:10]:
                rec = item.record
                digest.append(
                    f"- [{rec.kind}] {_one_line(rec.content, 140)} [memory:{rec.id}]"
                )
            for item in self.project[:6]:
                rec = item.record
                digest.append(
                    f"- [{rec.kind}] {_one_line(rec.content, 120)} "
                    f"[memory:{rec.id}]"
                )
            sections.append("\n".join(digest))
        if self.episodes:
            lines = [
                "## Relevant historical episodes",
                "May be outdated. Not proof of current work. "
                "memory_fetch(episode_id) for full text.",
            ]
            for item in self.episodes[:3]:
                ep = item.record
                lines.append(
                    f"- {_one_line(ep.task, 100)} — {_one_line(ep.summary, 120)} "
                    f"[episode:{ep.id}]"
                )
            sections.append("\n".join(lines))
        if self.index_pointers:
            index_lines = [
                "## Memory index",
                (
                    f"Offline index file: {self.index_path}"
                    if self.index_path
                    else "Offline index file: ~/.kageha/memory/MEMORY.md"
                ),
                "Pointers only — SQLite is authority. "
                "memory_fetch(id) for full text; do not treat this list as complete.",
            ]
            index_lines.extend(self.index_pointers[:24])
            sections.append("\n".join(index_lines))
        if self.trace_id:
            sections.append(f"Memory recall trace: {self.trace_id}")
        return "\n\n".join(sections)


@dataclass
class MemoryMutation:
    action: str
    content: str = ""
    target: str = ""
    kind: str = ""
    scope_type: str = ""
    project_root: str = ""
    session_id: str = ""
    user_id: str = "local"
    agent_id: str = "main"
    channel_key: str = ""
    source_role: str = "user"
    confidence: float = 1.0
    verification_evidence: str = ""


@dataclass
class IndexReport:
    ok: bool
    lexical_records: int
    vector_records: int = 0
    engine: str = "fts5"
    detail: str = ""
