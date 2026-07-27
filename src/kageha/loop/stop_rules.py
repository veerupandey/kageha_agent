"""Stop conditions for the agent loop — calibrated against TaskState validation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from kageha.loop.goal_card import GoalCard


class StopReason(str, Enum):
    SUCCESS = "success"
    MAX_STEPS = "max_steps"
    BUDGET = "budget"
    NO_PROGRESS = "no_progress"
    CANCELLED = "cancelled"
    HITL = "hitl"
    ERROR = "error"
    ASK_USER = "ask_user"
    CONTINUE = "continue"


@dataclass
class StopDecision:
    reason: StopReason
    message: str = ""

    @property
    def should_stop(self) -> bool:
        return self.reason != StopReason.CONTINUE


@dataclass
class StopRules:
    max_steps: int = 40
    max_usd: float = 2.0
    no_progress_limit: int = 5
    # Same verifier repair with no progress — break chat "infinite repair" loops.
    max_same_repair: int = 4
    max_total_repair: int = 8

    def evaluate(
        self,
        *,
        step: int,
        spent_usd: float,
        goal: GoalCard,
        stagnant_steps: int,
        cancelled: bool = False,
        model_said_done: bool = False,
        validated: bool = False,
        validation_status: str = "",
        ask_user: bool = False,
        same_repair_streak: int = 0,
        total_repair_cycles: int = 0,
    ) -> StopDecision:
        if cancelled:
            return StopDecision(StopReason.CANCELLED, "Cancelled by user")
        if ask_user:
            return StopDecision(StopReason.ASK_USER, "Agent needs user input")
        # Calibrated stopping: model "I'm done" has no authority without validation.
        goals_ok = goal.all_passed()
        status_pass = (validation_status or "").lower() == "pass" or validated
        if goals_ok and status_pass:
            return StopDecision(StopReason.SUCCESS, "Goals validated with evidence")
        # Hard limits always apply — never skipped by unverified "I'm done".
        if step >= self.max_steps:
            return StopDecision(StopReason.MAX_STEPS, f"Hit max steps ({self.max_steps})")
        if spent_usd >= self.max_usd:
            return StopDecision(StopReason.BUDGET, f"Hit budget (${self.max_usd})")
        if total_repair_cycles >= self.max_total_repair or (
            same_repair_streak >= self.max_same_repair
        ):
            return StopDecision(
                StopReason.ASK_USER,
                "Stuck in the same repair cycle — need your guidance to continue",
            )
        if stagnant_steps >= self.no_progress_limit:
            return StopDecision(
                StopReason.NO_PROGRESS,
                f"No progress for {stagnant_steps} steps — escalate to HITL",
            )
        # Unverified model-done: keep going so verifier/repair can finish, but
        # only after the hard stops above have been checked.
        if model_said_done and not (goals_ok and status_pass):
            return StopDecision(StopReason.CONTINUE)
        return StopDecision(StopReason.CONTINUE)
