"""Persistent structured task state — executive memory beyond the transcript."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from kageha.io import atomic_write_json


class StageStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class ClaimCertainty(str, Enum):
    VERIFIED = "verified"
    INFERRED = "inferred"
    ASSUMED = "assumed"
    UNRESOLVED = "unresolved"


class FailureKind(str, Enum):
    INVALID_ARGS = "invalid_args"
    MISSING_DEP = "missing_dependency"
    PROVIDER_ERROR = "provider_error"
    BAD_OUTPUT = "bad_output"
    TIMEOUT = "timeout"
    REASONING = "reasoning_error"
    TOOL_ERROR = "tool_error"
    ACCESS_BLOCKED = "access_blocked"
    UNKNOWN = "unknown"


class ControlDecision(str, Enum):
    CONTINUE = "continue"
    RETRY = "retry"
    SWITCH_TOOL = "switch_tool"
    REPLAN_STAGE = "replan_stage"
    REPLAN_TASK = "replan_task"
    DELEGATE = "delegate"
    ASK_USER = "ask_user"
    REPAIR = "repair"
    HUDDLE = "huddle"
    ADVANCE = "advance"
    STOP_SUCCESS = "stop_success"


Certainty = Literal["verified", "inferred", "assumed", "unresolved"]


@dataclass
class Fact:
    text: str
    source: str = ""
    certainty: str = ClaimCertainty.INFERRED.value


@dataclass
class Assumption:
    text: str
    status: str = "open"  # open | confirmed | rejected


@dataclass
class Deliverable:
    path: str
    role: str = ""  # e.g. primary video, deck, notes
    required: bool = False
    validated: bool = False
    evidence: str = ""


@dataclass
class PlanStage:
    id: str
    description: str
    status: str = StageStatus.PENDING.value
    tools: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    blocked_reason: str = ""
    attempts: int = 0
    last_error: str = ""
    # stages sharing a parallel_group activate together when deps are met
    parallel_group: str = ""
    # budget hints — 0 means unknown; surfaced in projection for cost-aware steering
    estimated_steps: int = 0
    estimated_usd: float = 0.0


@dataclass
class ToolResultNote:
    step: int
    tool: str
    ok: bool
    summary: str
    failure_kind: str = ""


@dataclass
class FailureRecord:
    step: int
    action: str
    result: str
    cause: str
    kind: str = FailureKind.UNKNOWN.value
    required_change: str = ""


@dataclass
class Defect:
    artifact: str
    severity: str  # critical | major | minor
    problem: str
    evidence: str = ""
    repair: str = ""
    stage_id: str = ""


@dataclass
class ValidationSnapshot:
    status: str = "unknown"  # pass | repair | fail | unknown
    defects: list[Defect] = field(default_factory=list)
    next_action: str = ""
    notes: str = ""


@dataclass
class BudgetState:
    max_steps: int = 40
    max_usd: float = 2.0
    steps_used: int = 0
    usd_spent: float = 0.0


@dataclass
class TaskState:
    """Executable executive state for a run — persisted as task_state.json."""

    objective: str = ""
    constraints: list[str] = field(default_factory=list)
    goals: list[dict[str, Any]] = field(default_factory=list)  # id/description/passes/evidence
    deliverables: list[Deliverable] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    stages: list[PlanStage] = field(default_factory=list)
    tool_results: list[ToolResultNote] = field(default_factory=list)
    failures: list[FailureRecord] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    validation: ValidationSnapshot = field(default_factory=ValidationSnapshot)
    budget: BudgetState = field(default_factory=BudgetState)
    unresolved: list[str] = field(default_factory=list)
    current_stage_id: str = ""
    control: str = ControlDecision.CONTINUE.value
    control_reason: str = ""
    version: int = 1
    # Runtime artifact manifest stub (path → role/meta). Populated lightly today;
    # full manifest UX / reuse indexing is a follow-up.
    artifact_manifest: dict[str, dict[str, Any]] = field(default_factory=dict)
    # A session can contain many chat turns. These fields establish the
    # current turn boundary so prior tool output is memory, never fresh proof.
    turn_id: str = ""
    turn_tool_result_start: int = 0
    turn_fact_start: int = 0
    turn_failure_start: int = 0
    pending_question: str = ""
    pending_yes_label: str = ""
    pending_no_label: str = ""
    pending_request: str = ""

    # --- mutations ---

    def begin_turn(
        self,
        *,
        turn_id: str,
        objective: str,
        goals: list[dict[str, Any]],
        plan_steps: list[Any],
        max_steps: int,
        max_usd: float,
    ) -> None:
        """Reset executable state while preserving durable session memory."""
        self.turn_id = turn_id
        self.turn_tool_result_start = len(self.tool_results)
        self.turn_fact_start = len(self.facts)
        self.turn_failure_start = len(self.failures)
        self.goals = [
            dict(item) if isinstance(item, dict)
            else {"id": f"g{idx}", "description": str(item), "passes": False, "evidence": ""}
            for idx, item in enumerate(goals, start=1)
        ]
        self.validation = ValidationSnapshot()
        self.deliverables = []
        self.artifact_manifest = {}
        self.unresolved = []
        self.control = ControlDecision.CONTINUE.value
        self.control_reason = "new chat turn"
        self.pending_question = ""
        self.pending_yes_label = ""
        self.pending_no_label = ""
        self.pending_request = ""
        self.budget = BudgetState(
            max_steps=max(1, int(max_steps)),
            max_usd=float(max_usd),
        )

    def set_stages_from_plan(self, steps: list[Any]) -> None:
        stages: list[PlanStage] = []
        for i, s in enumerate(steps):
            sid = str(getattr(s, "id", None) or f"s{i+1}")
            desc = str(getattr(s, "description", None) or s)
            tools = list(getattr(s, "tools", None) or [])
            deps = list(getattr(s, "depends_on", None) or [])
            if not deps:
                deps = [stages[-1].id] if stages else []
            parallel_group = str(getattr(s, "parallel_group", None) or "")
            estimated_steps = int(getattr(s, "estimated_steps", 0) or 0)
            estimated_usd = float(getattr(s, "estimated_usd", 0.0) or 0.0)
            stages.append(
                PlanStage(
                    id=sid,
                    description=desc,
                    status=StageStatus.ACTIVE.value if i == 0 else StageStatus.PENDING.value,
                    tools=tools,
                    depends_on=deps,
                    parallel_group=parallel_group,
                    estimated_steps=estimated_steps,
                    estimated_usd=estimated_usd,
                )
            )
        self.stages = stages
        self.current_stage_id = stages[0].id if stages else ""

    def sync_goals_from_card(self, goal: Any) -> None:
        self.objective = getattr(goal, "task", self.objective) or self.objective
        items = getattr(goal, "items", []) or []
        self.goals = [
            {
                "id": i.id,
                "description": i.description,
                "passes": bool(i.passes),
                "evidence": i.evidence or "",
            }
            for i in items
        ]

    def sync_artifacts(
        self,
        paths: list[str],
        *,
        current_paths: set[str] | None = None,
    ) -> None:
        self.artifacts = list(paths)
        # Auto-discovered files are evidence candidates, not requirements.
        # This also repairs state written by older versions that accidentally
        # made every discovered intermediate file a required deliverable.
        for d in self.deliverables:
            if d.role == "discovered":
                d.required = False
        known = {d.path for d in self.deliverables}
        for p in paths:
            if current_paths is not None and p not in current_paths:
                continue
            if p.startswith(("artifacts/", "outputs/", "diagrams/", "research/", "slides/")):
                if p not in known:
                    self.deliverables.append(
                        Deliverable(path=p, role="discovered", required=False)
                    )
                    known.add(p)
                # Keep manifest stub in sync (role only for now)
                meta = self.artifact_manifest.setdefault(p, {})
                if "role" not in meta:
                    meta["role"] = "discovered"

    def add_fact(self, text: str, *, source: str = "", certainty: str = "inferred") -> None:
        text = (text or "").strip()
        if not text:
            return
        for f in self.facts[-30:]:
            if f.text == text:
                return
        self.facts.append(Fact(text=text[:500], source=source[:200], certainty=certainty))
        if len(self.facts) > 80:
            self.facts = self.facts[-80:]

    def record_tool(
        self,
        *,
        step: int,
        tool: str,
        content: str,
    ) -> None:
        lowered = (content or "").lower()
        blocked_markers = (
            "captcha",
            "access denied",
            "unusual traffic",
            "verify you are human",
            "challenge page",
            "login required",
            "sign in to continue",
        )
        ok = (
            not (content or "").startswith("ERROR")
            and "DENIED:" not in (content or "")
            and not any(marker in lowered for marker in blocked_markers)
        )
        kind = ""
        if not ok:
            kind = _classify_failure(content).value
            self.failures.append(
                FailureRecord(
                    step=step,
                    action=tool,
                    result=(content or "")[:400],
                    cause=kind,
                    kind=kind,
                    required_change=_suggest_change(kind, content),
                )
            )
            if len(self.failures) > 40:
                self.failures = self.failures[-40:]
        self.tool_results.append(
            ToolResultNote(
                step=step,
                tool=tool,
                ok=ok,
                summary=(content or "").replace("\n", " ")[:240],
                failure_kind=kind,
            )
        )
        if len(self.tool_results) > 60:
            self.tool_results = self.tool_results[-60:]

    def apply_validation(self, snap: ValidationSnapshot) -> None:
        self.validation = snap
        # Mark deliverables validated when no critical defects on them
        defective = {
            d.artifact for d in snap.defects if d.severity in {"critical", "major"}
        }
        for d in self.deliverables:
            if d.path in defective or Path(d.path).name in defective:
                d.validated = False
                continue
            if snap.status == "pass":
                d.validated = True

    def current_stage(self) -> PlanStage | None:
        if self.current_stage_id:
            for s in self.stages:
                if s.id == self.current_stage_id:
                    return s
        for s in self.stages:
            if s.status == StageStatus.ACTIVE.value:
                return s
        return None

    def mark_stage(self, stage_id: str, status: str, *, reason: str = "") -> None:
        for s in self.stages:
            if s.id == stage_id:
                s.status = status
                if reason:
                    s.blocked_reason = reason
                    s.last_error = reason
                break

    def advance_stage(self) -> None:
        cur = self.current_stage()
        if cur:
            cur.status = StageStatus.DONE.value
        # activate ALL pending whose deps are done — enables parallel stages
        done_ids = {s.id for s in self.stages if s.status == StageStatus.DONE.value}
        for s in self.stages:
            if s.status != StageStatus.PENDING.value:
                continue
            if all(d in done_ids for d in s.depends_on):
                s.status = StageStatus.ACTIVE.value
        active = [s for s in self.stages if s.status == StageStatus.ACTIVE.value]
        self.current_stage_id = active[0].id if active else ""

    def anti_loop_hit(self, action: str, cause: str) -> bool:
        """True if same action+cause already failed recently without a required change applied."""
        recent = self.failures[-8:]
        exact = [f for f in recent if f.action == action and f.cause == cause]
        if len(exact) >= 2:
            return True
        # tool exhaustion: same action failing >= 3 times with any causes
        if len([f for f in recent if f.action == action]) >= 3:
            return True
        return False

    def goals_all_passed(self) -> bool:
        return bool(self.goals) and all(
            (g.get("passes") if isinstance(g, dict) else False) for g in self.goals
        )

    def validated_ok(self) -> bool:
        if self.validation.status == "pass" and self.goals_all_passed():
            return True
        if self.goals_all_passed() and not self.validation.defects:
            return True
        return False

    def projection(self, *, max_chars: int = 3500) -> str:
        """Compact view for the model — not the full transcript."""
        cur = self.current_stage()
        lines = [
            "# TaskState (authoritative — prefer over chat history)",
            f"Objective: {self.objective}",
            f"Control: {self.control}"
            + (f" — {self.control_reason}" if self.control_reason else ""),
            f"Budget: steps {self.budget.steps_used}/{self.budget.max_steps}, "
            f"usd~{self.budget.usd_spent:.3f}/{self.budget.max_usd}",
            "",
            "## Current stage",
        ]
        if cur:
            lines.append(f"- `{cur.id}` [{cur.status}] {cur.description}")
            if cur.last_error:
                lines.append(f"  last_error: {cur.last_error}")
        else:
            lines.append("- (none active)")
        lines.append("")
        lines.append("## Stages")
        for s in self.stages:
            cost = ""
            if s.estimated_steps or s.estimated_usd:
                parts = []
                if s.estimated_steps:
                    parts.append(f"~{s.estimated_steps} steps")
                if s.estimated_usd:
                    parts.append(f"~${s.estimated_usd:.2f}")
                cost = f" ({', '.join(parts)})"
            lines.append(f"- [{s.status}] `{s.id}` {s.description}{cost}")
        lines.append("")
        lines.append("## Goals")
        for g in self.goals:
            if isinstance(g, dict):
                box = "x" if g.get("passes") else " "
                lines.append(f"- [{box}] `{g.get('id')}` {g.get('description')}")
                if g.get("evidence"):
                    lines.append(f"  evidence: {g['evidence'][:160]}")
            else:
                lines.append(f"- [ ] {g}")
        if self.constraints:
            lines.append("")
            lines.append("## Constraints")
            for c in self.constraints[:12]:
                lines.append(f"- {c}")
        if self.facts:
            lines.append("")
            lines.append("## Facts")
            verified = [f for f in self.facts if f.certainty == ClaimCertainty.VERIFIED.value]
            others = [f for f in self.facts if f.certainty != ClaimCertainty.VERIFIED.value]
            for f in (verified[-8:] + others[-4:])[-12:]:
                lines.append(f"- ({f.certainty}) {f.text}" + (f" [{f.source}]" if f.source else ""))
        if self.assumptions:
            lines.append("")
            lines.append("## Assumptions")
            for a in self.assumptions[-8:]:
                lines.append(f"- [{a.status}] {a.text}")
        if self.deliverables:
            lines.append("")
            lines.append("## Deliverables")
            for d in self.deliverables[:20]:
                flag = "ok" if d.validated else "pending"
                lines.append(f"- [{flag}] {d.path}" + (f" — {d.role}" if d.role else ""))
        if self.validation.defects:
            lines.append("")
            lines.append("## Open defects (MUST repair)")
            for d in self.validation.defects[:8]:
                lines.append(
                    f"- [{d.severity}] {d.artifact}: {d.problem} → {d.repair}"
                )
            if self.validation.next_action:
                lines.append(f"next_action: {self.validation.next_action}")
        if self.failures:
            lines.append("")
            lines.append("## Recent failures (do not repeat without change)")
            for f in self.failures[-6:]:
                lines.append(
                    f"- step{f.step} {f.action}/{f.kind}: {f.cause} "
                    f"→ change: {f.required_change or 'alter approach'}"
                )
        if self.unresolved:
            lines.append("")
            lines.append("## Unresolved")
            for u in self.unresolved[:8]:
                lines.append(f"- {u}")
        text = "\n".join(lines) + "\n"
        if len(text) > max_chars:
            return text[: max_chars - 20] + "\n…[truncated]\n"
        return text

    def save(self, path: Path) -> None:
        atomic_write_json(path, asdict(self))

    @classmethod
    def load(cls, path: Path) -> "TaskState":
        data = json.loads(path.read_text())
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskState":
        def _list(key: str, typ: type):
            out = []
            for item in data.get(key) or []:
                if isinstance(item, dict):
                    out.append(typ(**{k: v for k, v in item.items() if k in typ.__dataclass_fields__}))  # type: ignore[attr-defined]
            return out

        val = data.get("validation") or {}
        defects = [
            Defect(**{k: v for k, v in d.items() if k in Defect.__dataclass_fields__})
            for d in (val.get("defects") or [])
            if isinstance(d, dict)
        ]
        validation = ValidationSnapshot(
            status=str(val.get("status") or "unknown"),
            defects=defects,
            next_action=str(val.get("next_action") or ""),
            notes=str(val.get("notes") or ""),
        )
        bud = data.get("budget") or {}
        budget = BudgetState(
            max_steps=int(bud.get("max_steps") or 40),
            max_usd=float(bud.get("max_usd") or 2.0),
            steps_used=int(bud.get("steps_used") or 0),
            usd_spent=float(bud.get("usd_spent") or 0.0),
        )
        return cls(
            objective=str(data.get("objective") or ""),
            constraints=list(data.get("constraints") or []),
            goals=list(data.get("goals") or []),
            deliverables=_list("deliverables", Deliverable),
            facts=_list("facts", Fact),
            assumptions=_list("assumptions", Assumption),
            stages=_list("stages", PlanStage),
            tool_results=_list("tool_results", ToolResultNote),
            failures=_list("failures", FailureRecord),
            artifacts=list(data.get("artifacts") or []),
            validation=validation,
            budget=budget,
            unresolved=list(data.get("unresolved") or []),
            current_stage_id=str(data.get("current_stage_id") or ""),
            control=str(data.get("control") or ControlDecision.CONTINUE.value),
            control_reason=str(data.get("control_reason") or ""),
            version=int(data.get("version") or 1),
            artifact_manifest={
                str(k): dict(v) if isinstance(v, dict) else {"role": str(v)}
                for k, v in (data.get("artifact_manifest") or {}).items()
            },
            turn_id=str(data.get("turn_id") or ""),
            turn_tool_result_start=int(data.get("turn_tool_result_start") or 0),
            turn_fact_start=int(data.get("turn_fact_start") or 0),
            turn_failure_start=int(data.get("turn_failure_start") or 0),
            pending_question=str(data.get("pending_question") or ""),
            pending_yes_label=str(data.get("pending_yes_label") or ""),
            pending_no_label=str(data.get("pending_no_label") or ""),
            pending_request=str(data.get("pending_request") or ""),
        )


def _classify_failure(content: str) -> FailureKind:
    c = (content or "").lower()
    if any(
        x in c
        for x in (
            "captcha",
            "access denied",
            "unusual traffic",
            "verify you are human",
            "challenge page",
            "login required",
            "sign in to continue",
        )
    ):
        return FailureKind.ACCESS_BLOCKED
    if "bad arguments" in c or "missing" in c and "argument" in c:
        return FailureKind.INVALID_ARGS
    if "timeout" in c:
        return FailureKind.TIMEOUT
    if "denied" in c:
        return FailureKind.MISSING_DEP
    if any(x in c for x in ("429", "rate limit", "503", "provider", "api key")):
        return FailureKind.PROVIDER_ERROR
    if c.startswith("error:"):
        return FailureKind.TOOL_ERROR
    return FailureKind.UNKNOWN


def _suggest_change(kind: str, content: str) -> str:
    mapping = {
        FailureKind.INVALID_ARGS.value: "Fix tool arguments; use smaller payloads / correct keys",
        FailureKind.MISSING_DEP.value: "Install dependency, seed inputs, or use an alternate tool",
        FailureKind.PROVIDER_ERROR.value: "Switch model/provider or retry with backoff",
        FailureKind.BAD_OUTPUT.value: "Regenerate with tighter constraints; verify evidence",
        FailureKind.TIMEOUT.value: "Narrow scope or raise timeout; split the work",
        FailureKind.TOOL_ERROR.value: "Try a different tool or simplify the command",
        FailureKind.ACCESS_BLOCKED.value: (
            "Use an available logged-in browser; otherwise ask the user to sign in or provide access"
        ),
        FailureKind.REASONING.value: "Re-plan the stage with corrected assumptions",
        FailureKind.UNKNOWN.value: "Change parameters or approach before retry",
    }
    return mapping.get(kind, mapping[FailureKind.UNKNOWN.value])
