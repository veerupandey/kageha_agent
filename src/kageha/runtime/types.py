"""Public types for the durable runtime."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SecurityProfile(str, Enum):
    STRICT = "strict"
    APPROVAL_FALLBACK = "approval_fallback"


class RunPhase(str, Enum):
    ACCEPTED = "accepted"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_APPROVAL = "waiting_approval"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"
    COMPLETED = "completed"

    @property
    def terminal(self) -> bool:
        return self in {
            RunPhase.BLOCKED,
            RunPhase.CANCELLED,
            RunPhase.FAILED,
            RunPhase.COMPLETED,
        }


class RunEventKind(str, Enum):
    ACCEPTED = "accepted"
    PLANNING_STARTED = "planning_started"
    PLANNED = "planned"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_RESOLVED = "approval_resolved"
    VERIFICATION_STARTED = "verification_started"
    VERIFICATION = "verification"
    REPAIR = "repair"
    CHECKPOINT = "checkpoint"
    PROGRESS = "progress"
    TODO_BOARD = "todo_board"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class FailureClass(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    QUOTA = "quota"
    AUTH = "auth"
    TIMEOUT = "timeout"
    POLICY_DENIAL = "policy_denial"
    UNCERTAIN_SIDE_EFFECT = "uncertain_side_effect"
    INVALID_OUTPUT = "invalid_output"
    PROVIDER = "provider"
    UNKNOWN = "unknown"


class ToolReconciliation(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYABLE = "retryable"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class TurnRequest:
    objective: str
    session_id: str = ""
    user_id: str = "local"
    agent_id: str = "main"
    channel_key: str = ""
    project_root: str = ""
    auto_approve: bool = False
    # Plan/Spec: skip Build/approve gate (independent of auto_approve tools).
    auto_build: bool = False
    security_profile: SecurityProfile = SecurityProfile.APPROVAL_FALLBACK
    max_steps: int | None = None
    knowledge_bases: tuple[str, ...] = ()
    skill_catalog: str = ""
    kb_pins: str = ""
    system_extra: str = ""
    model_override: str = ""
    export_dir: str = ""
    live: bool = True
    platform: str = "cli"
    log_handler: Any = None
    # Optional live token sink for terminal / UI streaming (str chunks).
    on_text_delta: Any = None
    approver: Any = None
    defer_human_input: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    # full = plan+verify+monitor
    # followup|act = one-step plan, sparse gates (default for chat)
    loop_mode: str = "full"
    # normal|plan|goal — policy layer over loop_mode (trimmed harness)
    agent_mode: str = "normal"


@dataclass(frozen=True)
class RunEvent:
    id: str
    session_id: str
    turn_id: str
    sequence: int
    kind: RunEventKind
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    idempotency_key: str = ""

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        turn_id: str,
        kind: RunEventKind,
        payload: dict[str, Any] | None = None,
        sequence: int = 0,
        idempotency_key: str = "",
    ) -> "RunEvent":
        return cls(
            id=uuid.uuid4().hex,
            session_id=session_id,
            turn_id=turn_id,
            sequence=sequence,
            kind=kind,
            payload=dict(payload or {}),
            idempotency_key=idempotency_key,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data


@dataclass
class RunSnapshot:
    session_id: str
    turn_id: str
    objective: str
    phase: RunPhase = RunPhase.ACCEPTED
    status: str = "running"
    plan_version: int = 0
    plan: list[dict[str, Any]] = field(default_factory=list)
    goals: list[dict[str, Any]] = field(default_factory=list)
    current_stage: str = ""
    steps_used: int = 0
    max_steps: int = 40
    usd_spent: float = 0.0
    max_usd: float = 2.0
    artifacts: list[str] = field(default_factory=list)
    pending_action: str = ""
    open_tool_attempts: list[str] = field(default_factory=list)
    last_error: str = ""
    verification_status: str = "unknown"
    validated: bool = False
    updated_at: float = field(default_factory=time.time)
    last_sequence: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.phase.terminal

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["phase"] = self.phase.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunSnapshot":
        values = dict(data)
        values["phase"] = RunPhase(values.get("phase") or RunPhase.ACCEPTED.value)
        return cls(**values)


@dataclass
class ToolAttempt:
    id: str
    session_id: str
    turn_id: str
    tool_call_id: str
    tool_name: str
    arguments_hash: str
    idempotency_key: str
    side_effect: str = "read"
    risk_class: str = "safe"
    policy_grant: str = ""
    deadline_at: float | None = None
    state: ToolReconciliation = ToolReconciliation.PENDING
    result: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        return data


@dataclass
class VerificationResult:
    status: str
    deterministic_passed: bool
    semantic_status: str = "unresolved"
    checks: list[dict[str, Any]] = field(default_factory=list)
    defects: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


@dataclass
class ProviderHealth:
    provider: str
    model_id: str
    available: bool
    state: str
    latency_ms: float = 0.0
    failure_class: FailureClass | None = None
    error: str = ""
    capabilities: list[str] = field(default_factory=list)
    checked_at: float = field(default_factory=time.time)
    circuit_open_until: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["failure_class"] = (
            self.failure_class.value if self.failure_class is not None else None
        )
        return data
