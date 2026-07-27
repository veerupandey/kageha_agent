"""Pure scheduling, recovery and stopping policies for the durable loop."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from kageha.runtime.types import (
    FailureClass,
    RunPhase,
    RunSnapshot,
    ToolReconciliation,
)


class RecoveryAction(str, Enum):
    RETRY = "retry"
    SWITCH_PROVIDER = "switch_provider"
    SWITCH_TOOL = "switch_tool"
    REPAIR = "repair"
    RECONCILE = "reconcile"
    BLOCK = "block"


class StopDecision(str, Enum):
    CONTINUE = "continue"
    COMPLETE = "complete"
    BLOCK = "block"
    FAIL_BUDGET = "fail_budget"
    FAIL_STEPS = "fail_steps"


@dataclass(frozen=True)
class ScheduledTask:
    stage: str
    task_id: str
    task: dict[str, Any]


class Scheduler:
    """Choose the next non-passed task from a versioned plan."""

    def next(self, snapshot: RunSnapshot) -> ScheduledTask | None:
        completed = {
            str(goal.get("id") or "")
            for goal in snapshot.goals
            if goal.get("passes") or goal.get("state") == "passed"
        }
        for index, task in enumerate(snapshot.plan):
            task_id = str(task.get("id") or f"p{index + 1}")
            if task_id not in completed:
                return ScheduledTask(
                    stage=str(task.get("stage") or snapshot.current_stage or task_id),
                    task_id=task_id,
                    task=dict(task),
                )
        return None


class RecoveryPolicy:
    """Deterministic failure taxonomy to durable recovery actions."""

    def decide(
        self,
        failure: FailureClass,
        *,
        side_effect: str = "read",
        reconciliation: ToolReconciliation | None = None,
        attempts: int = 0,
    ) -> RecoveryAction:
        if reconciliation == ToolReconciliation.UNCERTAIN or (
            side_effect != "read" and failure == FailureClass.TIMEOUT
        ):
            return RecoveryAction.RECONCILE
        if failure in {FailureClass.AUTH, FailureClass.QUOTA, FailureClass.PROVIDER}:
            return RecoveryAction.SWITCH_PROVIDER
        if failure in {FailureClass.TRANSIENT, FailureClass.TIMEOUT}:
            return RecoveryAction.RETRY if attempts < 3 else RecoveryAction.SWITCH_TOOL
        if failure == FailureClass.INVALID_OUTPUT:
            return RecoveryAction.REPAIR
        return RecoveryAction.BLOCK


class StopPolicy:
    """Make completion and budget decisions without model judgment."""

    def decide(self, snapshot: RunSnapshot) -> StopDecision:
        if snapshot.usd_spent >= snapshot.max_usd:
            return StopDecision.FAIL_BUDGET
        if snapshot.steps_used >= snapshot.max_steps:
            return StopDecision.FAIL_STEPS
        if snapshot.phase == RunPhase.BLOCKED:
            return StopDecision.BLOCK
        if snapshot.validated and not snapshot.open_tool_attempts:
            return StopDecision.COMPLETE
        return StopDecision.CONTINUE

