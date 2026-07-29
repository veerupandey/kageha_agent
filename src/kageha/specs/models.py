"""Data models for spec-driven development pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SpecStage(str, Enum):
    """Stages in the spec pipeline."""

    REQUIREMENTS = "requirements"
    DESIGN = "design"
    TASKS = "tasks"
    BUILD = "build"
    COMPLETE = "complete"

    @property
    def next(self) -> SpecStage | None:
        order = list(SpecStage)
        idx = order.index(self)
        return order[idx + 1] if idx < len(order) - 1 else None

    @property
    def prev(self) -> SpecStage | None:
        order = list(SpecStage)
        idx = order.index(self)
        return order[idx - 1] if idx > 0 else None


class GateStatus(str, Enum):
    """Status of a validation gate."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"


@dataclass
class TaskDependency:
    """A dependency between tasks."""

    task_id: str
    depends_on: list[str] = field(default_factory=list)


@dataclass
class SpecTask:
    """A single task in the implementation plan."""

    id: str
    title: str
    description: str
    acceptance_criteria: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    estimated_complexity: str = "medium"  # low, medium, high
    files_to_modify: list[str] = field(default_factory=list)
    status: str = "pending"  # pending, in_progress, done, failed


@dataclass
class SpecGate:
    """A validation gate between stages."""

    stage: SpecStage
    status: GateStatus = GateStatus.PENDING
    reviewer_notes: str = ""
    timestamp: str = ""

    def approve(self, notes: str = "") -> None:
        self.status = GateStatus.APPROVED
        self.reviewer_notes = notes

    def reject(self, notes: str = "") -> None:
        self.status = GateStatus.REJECTED
        self.reviewer_notes = notes

    def request_revision(self, notes: str = "") -> None:
        self.status = GateStatus.REVISION_REQUESTED
        self.reviewer_notes = notes


@dataclass
class SpecState:
    """Full state of a spec pipeline run."""

    name: str
    prompt: str
    current_stage: SpecStage = SpecStage.REQUIREMENTS
    gates: dict[str, SpecGate] = field(default_factory=dict)
    tasks: list[SpecTask] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def gate_for(self, stage: SpecStage) -> SpecGate:
        key = stage.value
        if key not in self.gates:
            self.gates[key] = SpecGate(stage=stage)
        return self.gates[key]

    def can_advance(self) -> bool:
        """Check if current stage gate is approved."""
        gate = self.gate_for(self.current_stage)
        return gate.status == GateStatus.APPROVED

    def advance(self) -> bool:
        """Move to next stage if gate is approved."""
        if not self.can_advance():
            return False
        nxt = self.current_stage.next
        if nxt is None:
            return False
        self.current_stage = nxt
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "prompt": self.prompt,
            "current_stage": self.current_stage.value,
            "gates": {
                k: {
                    "stage": v.stage.value,
                    "status": v.status.value,
                    "reviewer_notes": v.reviewer_notes,
                    "timestamp": v.timestamp,
                }
                for k, v in self.gates.items()
            },
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "description": t.description,
                    "acceptance_criteria": t.acceptance_criteria,
                    "depends_on": t.depends_on,
                    "estimated_complexity": t.estimated_complexity,
                    "files_to_modify": t.files_to_modify,
                    "status": t.status,
                }
                for t in self.tasks
            ],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpecState:
        gates = {}
        for k, v in (data.get("gates") or {}).items():
            gates[k] = SpecGate(
                stage=SpecStage(v["stage"]),
                status=GateStatus(v["status"]),
                reviewer_notes=v.get("reviewer_notes", ""),
                timestamp=v.get("timestamp", ""),
            )
        tasks = [
            SpecTask(
                id=t["id"],
                title=t["title"],
                description=t["description"],
                acceptance_criteria=t.get("acceptance_criteria", []),
                depends_on=t.get("depends_on", []),
                estimated_complexity=t.get("estimated_complexity", "medium"),
                files_to_modify=t.get("files_to_modify", []),
                status=t.get("status", "pending"),
            )
            for t in (data.get("tasks") or [])
        ]
        return cls(
            name=data["name"],
            prompt=data["prompt"],
            current_stage=SpecStage(data.get("current_stage", "requirements")),
            gates=gates,
            tasks=tasks,
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


def spec_dir(project_root: Path, name: str) -> Path:
    """Resolve the spec directory for a given feature name."""
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower())
    return project_root / ".kageha" / "specs" / safe_name


def load_spec_state(project_root: Path, name: str) -> SpecState | None:
    """Load spec state from disk."""
    state_path = spec_dir(project_root, name) / "state.json"
    if not state_path.is_file():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return SpecState.from_dict(data)
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def save_spec_state(project_root: Path, state: SpecState) -> Path:
    """Save spec state to disk."""
    directory = spec_dir(project_root, state.name)
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / "state.json"
    state_path.write_text(
        json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    return state_path
