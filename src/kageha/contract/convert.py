"""Compatibility bridge: TaskContract -> existing GoalCard/TaskState shapes.

Required by REL-010.6 and REL-001.3. The controller loop does not need to
know a TaskContract exists — GoalCard.all_passed()/progress()/to_markdown()
and TaskState deliverable/constraint handling continue to work unchanged.
"""

from __future__ import annotations

from kageha.contract.models import TaskContract
from kageha.loop.goal_card import GoalCard, GoalItem
from kageha.loop.task_state import Deliverable as TaskStateDeliverable


def to_goal_card(contract: TaskContract) -> GoalCard:
    """Map each SuccessCriterion to one GoalItem (id, description, passes=False)."""
    items = [
        GoalItem(
            id=criterion.id,
            description=criterion.description,
            passes=False,
            evidence="",
        )
        for criterion in contract.success_criteria
    ]
    return GoalCard(task=contract.objective, items=items)


def to_task_state_deliverables(contract: TaskContract) -> list[TaskStateDeliverable]:
    """Project contract-level Deliverable entries into loop.task_state.Deliverable."""
    return [
        TaskStateDeliverable(
            path=d.path_hint or d.id,
            role=d.description,
            required=d.required,
            validated=False,
            evidence="",
        )
        for d in contract.deliverables
    ]


def to_task_state_constraints(contract: TaskContract) -> list[str]:
    """Project contract Constraint entries into TaskState's flat constraint strings."""
    out: list[str] = []
    for c in contract.constraints:
        prefix = f"[{c.source.value}]"
        if c.status.value == "unresolved":
            prefix += "[unresolved]"
        out.append(f"{prefix} {c.text}"[:300])
    return out
