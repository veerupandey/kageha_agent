"""Falsifiable goal checklist."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from kageha.io import atomic_write_json

# Matches "- [x] `g1` …" / "- [X] g1 …" written into todo.md by the agent.
_TODO_DONE_RE = re.compile(
    r"^[ \t]*[-*][ \t]+\[[xX]\][ \t]+`?(g\d+)`?",
    re.MULTILINE,
)


@dataclass
class GoalItem:
    id: str
    description: str
    passes: bool = False
    evidence: str = ""


@dataclass
class GoalCard:
    task: str
    items: list[GoalItem] = field(default_factory=list)

    @classmethod
    def from_task(cls, task: str, milestones: list[str] | None = None) -> GoalCard:
        ms = milestones or [
            "Understood the task and constraints",
            "Produced the primary deliverable",
            "Verified the deliverable against the request",
        ]
        items = [
            GoalItem(id=f"g{i+1}", description=m, passes=False) for i, m in enumerate(ms)
        ]
        return cls(task=task, items=items)

    def mark(self, goal_id: str, *, passes: bool, evidence: str = "") -> None:
        for item in self.items:
            if item.id == goal_id:
                item.passes = passes
                item.evidence = evidence
                return

    def apply_todo_checkboxes(self, markdown: str, *, evidence: str = "") -> int:
        """Mark goals passed when todo.md has checked boxes for their ids.

        Returns how many items newly flipped to passes=True.
        """
        newly = 0
        for match in _TODO_DONE_RE.finditer(markdown or ""):
            goal_id = match.group(1)
            for item in self.items:
                if item.id != goal_id or item.passes:
                    continue
                item.passes = True
                if evidence and not item.evidence:
                    item.evidence = evidence
                newly += 1
        return newly

    def all_passed(self) -> bool:
        return bool(self.items) and all(i.passes for i in self.items)

    def progress(self) -> float:
        if not self.items:
            return 0.0
        return sum(1 for i in self.items if i.passes) / len(self.items)

    def to_markdown(self) -> str:
        lines = [f"# Goal: {self.task}", ""]
        for i in self.items:
            box = "[x]" if i.passes else "[ ]"
            lines.append(f"- {box} `{i.id}` {i.description}")
            if i.evidence:
                lines.append(f"  - evidence: {i.evidence}")
        return "\n".join(lines) + "\n"

    def save(self, path: Path) -> None:
        atomic_write_json(path, asdict(self))

    @classmethod
    def load(cls, path: Path) -> GoalCard:
        data = json.loads(path.read_text())
        items = [GoalItem(**i) for i in data.get("items") or [] if isinstance(i, dict)]
        return cls(task=data.get("task", ""), items=items)
