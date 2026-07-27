"""Pure state transitions for the run journal."""

from __future__ import annotations

import time

from kageha.runtime.types import RunEvent, RunEventKind, RunPhase, RunSnapshot


class InvalidTransition(RuntimeError):
    """Raised when an event violates a durable run invariant."""


def initial_snapshot(event: RunEvent) -> RunSnapshot:
    if event.kind != RunEventKind.ACCEPTED:
        raise InvalidTransition("the first event for a turn must be accepted")
    objective = str(event.payload.get("objective") or "").strip()
    if not objective:
        raise InvalidTransition("accepted event requires an objective")
    return RunSnapshot(
        session_id=event.session_id,
        turn_id=event.turn_id,
        objective=objective,
        phase=RunPhase.ACCEPTED,
        max_steps=max(1, int(event.payload.get("max_steps") or 40)),
        max_usd=float(event.payload.get("max_usd") or 2.0),
        metadata=dict(event.payload.get("metadata") or {}),
        updated_at=event.created_at,
        last_sequence=event.sequence,
    )


def reduce_event(snapshot: RunSnapshot | None, event: RunEvent) -> RunSnapshot:
    """Apply one event without I/O.

    Re-applying an event at or before ``last_sequence`` is idempotent. Events
    after a terminal state are rejected so a completed turn cannot silently
    resume or mutate.
    """
    if snapshot is None:
        return initial_snapshot(event)
    if event.session_id != snapshot.session_id or event.turn_id != snapshot.turn_id:
        raise InvalidTransition("event identity does not match snapshot")
    if event.sequence <= snapshot.last_sequence:
        return snapshot
    if event.sequence != snapshot.last_sequence + 1:
        raise InvalidTransition(
            f"event sequence gap: expected {snapshot.last_sequence + 1}, "
            f"received {event.sequence}"
        )
    if snapshot.terminal:
        raise InvalidTransition(
            f"cannot apply {event.kind.value} after terminal phase {snapshot.phase.value}"
        )

    payload = event.payload
    kind = event.kind
    if kind == RunEventKind.PLANNING_STARTED:
        snapshot.phase = RunPhase.PLANNING
    elif kind == RunEventKind.PLANNED:
        snapshot.phase = RunPhase.EXECUTING
        snapshot.plan_version = max(
            snapshot.plan_version + 1,
            int(payload.get("version") or 1),
        )
        snapshot.plan = list(payload.get("plan") or payload.get("steps") or [])
        snapshot.goals = list(payload.get("goals") or snapshot.goals)
        snapshot.current_stage = str(payload.get("current_stage") or "")
    elif kind == RunEventKind.TOOL_STARTED:
        snapshot.phase = RunPhase.EXECUTING
        attempt_id = str(payload.get("attempt_id") or "")
        if attempt_id and attempt_id not in snapshot.open_tool_attempts:
            snapshot.open_tool_attempts.append(attempt_id)
        snapshot.steps_used = max(
            snapshot.steps_used,
            int(payload.get("step") or snapshot.steps_used),
        )
    elif kind == RunEventKind.TOOL_COMPLETED:
        attempt_id = str(payload.get("attempt_id") or "")
        snapshot.open_tool_attempts = [
            item for item in snapshot.open_tool_attempts if item != attempt_id
        ]
        snapshot.phase = RunPhase.EXECUTING
        snapshot.usd_spent = max(
            snapshot.usd_spent,
            float(payload.get("usd_spent") or snapshot.usd_spent),
        )
    elif kind == RunEventKind.APPROVAL_REQUIRED:
        snapshot.phase = RunPhase.WAITING_APPROVAL
        snapshot.pending_action = str(payload.get("action") or "approval")
    elif kind == RunEventKind.APPROVAL_RESOLVED:
        if not bool(payload.get("approved", False)):
            snapshot.phase = RunPhase.BLOCKED
            snapshot.status = "denied"
            snapshot.last_error = str(payload.get("reason") or "approval denied")
        else:
            snapshot.phase = RunPhase.EXECUTING
            snapshot.pending_action = ""
    elif kind == RunEventKind.VERIFICATION_STARTED:
        snapshot.phase = RunPhase.VERIFYING
    elif kind == RunEventKind.VERIFICATION:
        status = str(payload.get("status") or "unknown")
        snapshot.verification_status = status
        snapshot.validated = bool(payload.get("validated", status == "pass"))
        snapshot.goals = list(payload.get("goals") or snapshot.goals)
        snapshot.artifacts = list(payload.get("artifacts") or snapshot.artifacts)
        snapshot.phase = (
            RunPhase.EXECUTING if snapshot.validated else RunPhase.REPAIRING
        )
    elif kind == RunEventKind.REPAIR:
        snapshot.phase = RunPhase.REPAIRING
        snapshot.pending_action = str(payload.get("action") or "repair")
    elif kind == RunEventKind.CHECKPOINT:
        snapshot.pending_action = ""
    elif kind == RunEventKind.TODO_BOARD:
        # Live checklist snapshot — does not change phase.
        pass
    elif kind == RunEventKind.PROGRESS:
        snapshot.steps_used = max(
            snapshot.steps_used,
            int(payload.get("step") or snapshot.steps_used),
        )
        snapshot.usd_spent = max(
            snapshot.usd_spent,
            float(payload.get("usd_spent") or snapshot.usd_spent),
        )
        snapshot.current_stage = str(
            payload.get("current_stage") or snapshot.current_stage
        )
    elif kind == RunEventKind.BLOCKED:
        snapshot.phase = RunPhase.BLOCKED
        snapshot.status = str(payload.get("status") or "blocked")
        snapshot.last_error = str(payload.get("reason") or "")
    elif kind == RunEventKind.CANCELLED:
        snapshot.phase = RunPhase.CANCELLED
        snapshot.status = "cancelled"
    elif kind == RunEventKind.COMPLETED:
        if not bool(payload.get("validated", snapshot.validated)):
            raise InvalidTransition("completed event requires validated=true")
        snapshot.phase = RunPhase.COMPLETED
        snapshot.status = "success"
        snapshot.validated = True
        snapshot.verification_status = "pass"
        snapshot.artifacts = list(payload.get("artifacts") or snapshot.artifacts)
        snapshot.open_tool_attempts = []
    elif kind == RunEventKind.FAILED:
        snapshot.phase = RunPhase.FAILED
        snapshot.status = str(payload.get("status") or "error")
        snapshot.last_error = str(payload.get("error") or payload.get("reason") or "")
    else:  # pragma: no cover - Enum makes this defensive
        raise InvalidTransition(f"unsupported event kind: {kind}")

    snapshot.updated_at = event.created_at or time.time()
    snapshot.last_sequence = event.sequence
    return snapshot
