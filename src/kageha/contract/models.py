"""TaskContract and element types (REL-010).

Versioned, typed, authoritative representation of an executable turn.
See .kiro/specs/kageha-reliability-spine/design.md for the full spec.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class ConstraintSource(str, Enum):
    EXPLICIT = "explicit"  # verbatim in user text
    DETERMINISTIC = "deterministic"  # rule-extracted
    SEMANTIC = "semantic"  # planner-model inferred


class ContractStatus(str, Enum):
    ACTIVE = "active"
    UNRESOLVED = "unresolved"  # contradiction pending clarification


@dataclass(frozen=True)
class Constraint:
    id: str
    text: str
    source: ConstraintSource
    status: ContractStatus = ContractStatus.ACTIVE
    contradicts: tuple[str, ...] = ()  # ids of conflicting entries


class RequirementKind(str, Enum):
    FILE_COUNT = "file_count"
    FILENAME = "filename"
    SLIDE_COUNT = "slide_count"
    PAGE_COUNT = "page_count"
    DIMENSIONS = "dimensions"
    CITATION = "citation"
    TEST_COMMAND = "test_command"
    BROWSER_OUTCOME = "browser_outcome"
    PROHIBITION = "prohibition"


@dataclass(frozen=True)
class Requirement:
    id: str
    kind: RequirementKind
    value: Any
    source: ConstraintSource
    status: ContractStatus = ContractStatus.ACTIVE
    contradicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Deliverable:
    """Contract-level deliverable; converts to loop.task_state.Deliverable."""

    id: str
    description: str
    path_hint: str = ""
    required: bool = True


class VerifierKind(str, Enum):
    DETERMINISTIC_POSTCONDITION = "deterministic_postcondition"
    ARTIFACT_CHECK = "artifact_check"
    SEMANTIC_JUDGMENT = "semantic_judgment"


@dataclass(frozen=True)
class VerifierSpec:
    kind: VerifierKind
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SuccessCriterion:
    id: str
    required: bool
    description: str
    verifier: VerifierSpec
    depends_on: tuple[str, ...] = ()
    source: ConstraintSource = ConstraintSource.DETERMINISTIC


@dataclass(frozen=True)
class PermissionEnvelope:
    allowed_actions: tuple[str, ...] = ()
    denied_actions: tuple[str, ...] = ()
    default: Literal["deny", "allow"] = "deny"  # fail-closed default (REL-001)


@dataclass(frozen=True)
class ResourceBudget:
    max_steps: int
    max_usd: float
    max_time_s: float

    @classmethod
    def resolve(
        cls,
        *,
        default: "ResourceBudget",
        requested_max_steps: int | None = None,
        requested_max_usd: float | None = None,
        requested_max_time_s: float | None = None,
    ) -> "ResourceBudget":
        """Per-field min(default, requested); default when no request value.

        Implements REL-010.4/5 with a single rule: the resolved budget is
        never looser than the default and never looser than a supplied
        stricter request.
        """

        def _field(default_value: float, requested_value: float | None) -> float:
            if requested_value is None:
                return default_value
            return min(default_value, requested_value)

        return cls(
            max_steps=int(_field(default.max_steps, requested_max_steps)),
            max_usd=float(_field(default.max_usd, requested_max_usd)),
            max_time_s=float(_field(default.max_time_s, requested_max_time_s)),
        )


@dataclass
class TaskContract:
    schema_version: int = 1
    contract_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str = ""
    turn_id: str = ""
    objective: str = ""
    constraints: list[Constraint] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    deliverables: list[Deliverable] = field(default_factory=list)
    success_criteria: list[SuccessCriterion] = field(default_factory=list)
    permissions: PermissionEnvelope = field(default_factory=PermissionEnvelope)
    budget: ResourceBudget = field(
        default_factory=lambda: ResourceBudget(40, 2.0, 3600.0)
    )
    # deterministic | deterministic+semantic | deterministic_fallback
    compiler_source: str = "deterministic"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe projection (enums -> values, tuples -> lists)."""

        def _constraint(c: Constraint) -> dict[str, Any]:
            return {
                "id": c.id,
                "text": c.text,
                "source": c.source.value,
                "status": c.status.value,
                "contradicts": list(c.contradicts),
            }

        def _requirement(r: Requirement) -> dict[str, Any]:
            return {
                "id": r.id,
                "kind": r.kind.value,
                "value": r.value,
                "source": r.source.value,
                "status": r.status.value,
                "contradicts": list(r.contradicts),
            }

        def _deliverable(d: Deliverable) -> dict[str, Any]:
            return {
                "id": d.id,
                "description": d.description,
                "path_hint": d.path_hint,
                "required": d.required,
            }

        def _criterion(sc: SuccessCriterion) -> dict[str, Any]:
            return {
                "id": sc.id,
                "required": sc.required,
                "description": sc.description,
                "verifier": {
                    "kind": sc.verifier.kind.value,
                    "config": dict(sc.verifier.config),
                },
                "depends_on": list(sc.depends_on),
                "source": sc.source.value,
            }

        return {
            "schema_version": self.schema_version,
            "contract_id": self.contract_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "objective": self.objective,
            "constraints": [_constraint(c) for c in self.constraints],
            "requirements": [_requirement(r) for r in self.requirements],
            "deliverables": [_deliverable(d) for d in self.deliverables],
            "success_criteria": [_criterion(sc) for sc in self.success_criteria],
            "permissions": {
                "allowed_actions": list(self.permissions.allowed_actions),
                "denied_actions": list(self.permissions.denied_actions),
                "default": self.permissions.default,
            },
            "budget": {
                "max_steps": self.budget.max_steps,
                "max_usd": self.budget.max_usd,
                "max_time_s": self.budget.max_time_s,
            },
            "compiler_source": self.compiler_source,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskContract":
        constraints = [
            Constraint(
                id=str(c["id"]),
                text=str(c.get("text") or ""),
                source=ConstraintSource(c["source"]),
                status=ContractStatus(c.get("status") or ContractStatus.ACTIVE.value),
                contradicts=tuple(c.get("contradicts") or ()),
            )
            for c in data.get("constraints") or []
        ]
        requirements = [
            Requirement(
                id=str(r["id"]),
                kind=RequirementKind(r["kind"]),
                value=r.get("value"),
                source=ConstraintSource(r["source"]),
                status=ContractStatus(r.get("status") or ContractStatus.ACTIVE.value),
                contradicts=tuple(r.get("contradicts") or ()),
            )
            for r in data.get("requirements") or []
        ]
        deliverables = [
            Deliverable(
                id=str(d["id"]),
                description=str(d.get("description") or ""),
                path_hint=str(d.get("path_hint") or ""),
                required=bool(d.get("required", True)),
            )
            for d in data.get("deliverables") or []
        ]
        success_criteria = [
            SuccessCriterion(
                id=str(sc["id"]),
                required=bool(sc.get("required", True)),
                description=str(sc.get("description") or ""),
                verifier=VerifierSpec(
                    kind=VerifierKind((sc.get("verifier") or {}).get("kind")),
                    config=dict((sc.get("verifier") or {}).get("config") or {}),
                ),
                depends_on=tuple(sc.get("depends_on") or ()),
                source=ConstraintSource(
                    sc.get("source") or ConstraintSource.DETERMINISTIC.value
                ),
            )
            for sc in data.get("success_criteria") or []
        ]
        perm = data.get("permissions") or {}
        permissions = PermissionEnvelope(
            allowed_actions=tuple(perm.get("allowed_actions") or ()),
            denied_actions=tuple(perm.get("denied_actions") or ()),
            default=perm.get("default") or "deny",
        )
        bud = data.get("budget") or {}
        budget = ResourceBudget(
            max_steps=int(bud.get("max_steps") or 40),
            max_usd=float(bud.get("max_usd") or 2.0),
            max_time_s=float(bud.get("max_time_s") or 3600.0),
        )
        return cls(
            schema_version=int(data.get("schema_version") or 1),
            contract_id=str(data.get("contract_id") or uuid.uuid4().hex),
            session_id=str(data.get("session_id") or ""),
            turn_id=str(data.get("turn_id") or ""),
            objective=str(data.get("objective") or ""),
            constraints=constraints,
            requirements=requirements,
            deliverables=deliverables,
            success_criteria=success_criteria,
            permissions=permissions,
            budget=budget,
            compiler_source=str(data.get("compiler_source") or "deterministic"),
            created_at=float(data.get("created_at") or time.time()),
        )
