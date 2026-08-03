"""VerificationEngine (REL-030) — the sole authority for criterion verdicts.

Adapts existing runtime validators, the semantic verifier, and deterministic
postcondition checks behind one Verifier protocol. Every criterion is run
through all three stages in fixed order (deterministic -> artifact ->
semantic); a deterministic failure always wins regardless of the semantic
stage's result.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from kageha.contract.models import SuccessCriterion, TaskContract, VerifierKind
from kageha.verification.evidence import EvidenceLedger, EvidenceRecord


class CriterionStatus:
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"  # unresolved contradiction / missing dependency
    UNRESOLVED = "unresolved"


StageResult = Literal["pass", "fail", "skip_not_applicable"]


@dataclass(frozen=True)
class VerificationDefect:
    criterion_id: str
    severity: Literal["critical", "major", "minor"]
    problem: str
    evidence_ids: tuple[str, ...] = ()
    repair_hint: str = ""
    stage: Literal["deterministic", "artifact", "semantic"] = "deterministic"


@dataclass(frozen=True)
class CriterionVerdict:
    criterion_id: str
    status: str  # CriterionStatus value
    stage_results: dict[str, StageResult]
    evidence_ids: tuple[str, ...] = ()
    defect: VerificationDefect | None = None


@dataclass(frozen=True)
class VerificationReport:
    report_id: str
    session_id: str
    turn_id: str
    scope: str  # milestone:<id> | completion_claim | final
    verdicts: tuple[CriterionVerdict, ...]
    generated_at: float

    @property
    def success(self) -> bool:
        """Task succeeds iff every *required* criterion passes (Requirement
        14.1). An optional criterion may fail or lack evidence without
        excluding the task from success. A required criterion left
        UNRESOLVED counts as not-passing (Requirement 14.2).
        """
        required_verdicts = [
            v for v in self.verdicts if v.criterion_id in self._required_ids
        ]
        if not required_verdicts:
            return all(
                v.status != CriterionStatus.UNRESOLVED for v in self.verdicts
            ) or not self.verdicts
        return all(v.status == CriterionStatus.PASS for v in required_verdicts)

    # populated by VerificationEngine.verify() so .success can distinguish
    # required from optional criteria without re-querying the contract.
    _required_ids: frozenset[str] = field(default_factory=frozenset, repr=False, compare=False)

    @staticmethod
    def deterministic_report_id(turn_id: str, scope: str, criterion_ids: tuple[str, ...]) -> str:
        """Deterministic per (turn_id, scope, criterion_ids) — supports
        idempotent verification-event emission on replay/resume (REL-033.3).
        """
        import hashlib

        material = f"{turn_id}:{scope}:{','.join(sorted(criterion_ids))}"
        return hashlib.sha256(material.encode()).hexdigest()[:32]


@dataclass
class VerificationContext:
    session_id: str
    turn_id: str
    workspace: Path
    objective: str = ""
    workspace_summary: str = ""
    transcript_tail: str = ""
    task_state_projection: str = ""
    router: Any = None
    execution_provider: str = ""
    goal: Any = None  # loop.goal_card.GoalCard, for SemanticJudgmentVerifier reuse
    model_said_done: bool = False
    successful_tools: list[str] = field(default_factory=list)
    turn_artifacts: list[str] = field(default_factory=list)
    answer_text: str = ""


class Verifier(Protocol):
    kind: VerifierKind

    async def check(
        self, criterion: SuccessCriterion, ctx: VerificationContext
    ) -> tuple[StageResult, list[EvidenceRecord], VerificationDefect | None]: ...


class DeterministicPostconditionVerifier:
    """Wraps verifier_agent.py's check_files_exist/check_python_syntax/
    check_tests/check_lint functions individually (REL-030.3) — the standalone
    module's orchestration function (run_deterministic_verification) is not
    called in production.
    """

    kind = VerifierKind.DETERMINISTIC_POSTCONDITION

    async def check(
        self, criterion: SuccessCriterion, ctx: VerificationContext
    ) -> tuple[StageResult, list[EvidenceRecord], VerificationDefect | None]:
        from kageha.contract.models import RequirementKind
        from kageha.loop.verifier_agent import check_tests
        from kageha.verification.evidence import EvidenceCertainty, EvidenceSource

        config = criterion.verifier.config or {}
        test_command = config.get("test_command") or config.get("command")
        if not test_command and RequirementKind.TEST_COMMAND.value not in criterion.description:
            return "skip_not_applicable", [], None

        check = await check_tests(
            workspace=ctx.workspace,
            test_command=test_command,
        )
        evidence = [
            EvidenceRecord.new(
                session_id=ctx.session_id,
                turn_id=ctx.turn_id,
                criterion_id=criterion.id,
                source=EvidenceSource.COMMAND_OUTPUT,
                source_ref=test_command or "auto-detected test command",
                digest="",
                certainty=(
                    EvidenceCertainty.VERIFIED
                    if check.passed
                    else EvidenceCertainty.PROBABLE
                ),
                producer="check_tests",
                metadata={"evidence": check.evidence, "error": check.error},
            )
        ]
        if check.passed:
            return "pass", evidence, None
        defect = VerificationDefect(
            criterion_id=criterion.id,
            severity="critical",
            problem=check.error or "deterministic postcondition failed",
            repair_hint="Fix the failing tests before claiming completion.",
            stage="deterministic",
        )
        return "fail", evidence, defect


class ArtifactCheckVerifier:
    """Wraps runtime/validators.ValidatorRegistry (REL-030.2, REL-033.1)."""

    kind = VerifierKind.ARTIFACT_CHECK

    async def check(
        self, criterion: SuccessCriterion, ctx: VerificationContext
    ) -> tuple[StageResult, list[EvidenceRecord], VerificationDefect | None]:
        from kageha.runtime.validators import ValidationContext, ValidatorRegistry

        config = criterion.verifier.config or {}
        artifacts = config.get("artifacts") or list(ctx.turn_artifacts)
        if not artifacts:
            return "skip_not_applicable", [], None

        registry = ValidatorRegistry()
        result = registry.validate(
            ValidationContext(
                objective=ctx.objective,
                workspace=ctx.workspace,
                artifacts=tuple(artifacts),
            ),
            requirements=config.get("requirements"),
        )
        from kageha.runtime.validators import ValidationCheck

        checks = [ValidationCheck(**c) if isinstance(c, dict) else c for c in result.checks]
        evidence = [
            check.to_evidence(
                session_id=ctx.session_id,
                turn_id=ctx.turn_id,
                criterion_id=criterion.id,
            )
            for check in checks
        ]
        if result.deterministic_passed:
            return "pass", evidence, None
        problems = "; ".join(str(d.get("problem") or "") for d in result.defects[:3])
        defect = VerificationDefect(
            criterion_id=criterion.id,
            severity="major",
            problem=problems or "artifact check failed",
            repair_hint="Regenerate or fix the reported artifact(s).",
            stage="artifact",
        )
        return "fail", evidence, defect


class SemanticJudgmentVerifier:
    """Wraps loop/verifier.verify_with_defects unchanged (REL-030.2)."""

    kind = VerifierKind.SEMANTIC_JUDGMENT

    async def check(
        self, criterion: SuccessCriterion, ctx: VerificationContext
    ) -> tuple[StageResult, list[EvidenceRecord], VerificationDefect | None]:
        from kageha.loop.goal_card import GoalCard, GoalItem
        from kageha.loop.verifier import verify_with_defects
        from kageha.verification.evidence import EvidenceCertainty, EvidenceSource

        if ctx.router is None:
            return "skip_not_applicable", [], None

        goal = GoalCard(
            task=ctx.objective,
            items=[GoalItem(id=criterion.id, description=criterion.description)],
        )
        result = await verify_with_defects(
            goal,
            router=ctx.router,
            workspace_summary=ctx.workspace_summary,
            transcript_tail=ctx.transcript_tail,
            task_state_projection=ctx.task_state_projection,
            execution_provider=ctx.execution_provider,
            model_said_done=ctx.model_said_done,
            successful_tools=ctx.successful_tools,
            turn_artifacts=ctx.turn_artifacts,
            answer_text=ctx.answer_text,
        )
        item = next((i for i in result.goal.items if i.id == criterion.id), None)
        passed = bool(item and item.passes)
        evidence = []
        if item and item.evidence:
            evidence.append(
                EvidenceRecord.new(
                    session_id=ctx.session_id,
                    turn_id=ctx.turn_id,
                    criterion_id=criterion.id,
                    source=EvidenceSource.MODEL_JUDGMENT,
                    source_ref=item.evidence[:300],
                    digest="",
                    certainty=(
                        EvidenceCertainty.PROBABLE
                        if passed
                        else EvidenceCertainty.UNVERIFIABLE
                    ),
                    producer="verify_with_defects",
                )
            )
        if passed:
            return "pass", evidence, None
        matching_defects = [
            d for d in result.snapshot.defects if d.stage_id == criterion.id or not d.stage_id
        ]
        defect_src = matching_defects[0] if matching_defects else None
        defect = VerificationDefect(
            criterion_id=criterion.id,
            severity=(defect_src.severity if defect_src else "major"),  # type: ignore[arg-type]
            problem=(defect_src.problem if defect_src else "semantic judgment did not pass"),
            repair_hint=(defect_src.repair if defect_src else ""),
            stage="semantic",
        )
        return "fail", evidence, defect


_DEFAULT_VERIFIERS: dict[VerifierKind, Verifier] = {
    VerifierKind.DETERMINISTIC_POSTCONDITION: DeterministicPostconditionVerifier(),
    VerifierKind.ARTIFACT_CHECK: ArtifactCheckVerifier(),
    VerifierKind.SEMANTIC_JUDGMENT: SemanticJudgmentVerifier(),
}

_STAGE_ORDER: tuple[VerifierKind, ...] = (
    VerifierKind.DETERMINISTIC_POSTCONDITION,
    VerifierKind.ARTIFACT_CHECK,
    VerifierKind.SEMANTIC_JUDGMENT,
)
_STAGE_NAME = {
    VerifierKind.DETERMINISTIC_POSTCONDITION: "deterministic",
    VerifierKind.ARTIFACT_CHECK: "artifact",
    VerifierKind.SEMANTIC_JUDGMENT: "semantic",
}


class VerificationEngine:
    """The sole authority producing a CriterionVerdict for each SuccessCriterion."""

    def __init__(
        self,
        ledger: EvidenceLedger,
        verifiers: dict[VerifierKind, Verifier] | None = None,
        *,
        quorum_threshold: float = 0.5,
    ) -> None:
        self.ledger = ledger
        self.verifiers = verifiers or dict(_DEFAULT_VERIFIERS)
        self.quorum_threshold = quorum_threshold

    async def verify(
        self,
        contract: TaskContract,
        *,
        criterion_ids: set[str] | None = None,
        ctx: VerificationContext,
        scope: str = "final",
    ) -> VerificationReport:
        criteria = [
            c
            for c in contract.success_criteria
            if criterion_ids is None or c.id in criterion_ids
        ]
        verdicts: list[CriterionVerdict] = []
        for criterion in criteria:
            verdict = await self._verify_one(criterion, ctx)
            verdicts.append(verdict)

        required_ids = frozenset(c.id for c in contract.success_criteria if c.required)
        report = VerificationReport(
            report_id=VerificationReport.deterministic_report_id(
                ctx.turn_id, scope, tuple(sorted(c.id for c in criteria))
            ),
            session_id=ctx.session_id,
            turn_id=ctx.turn_id,
            scope=scope,
            verdicts=tuple(verdicts),
            generated_at=time.time(),
        )
        # dataclass is frozen but this field is compare=False/repr=False and
        # exists solely to let .success apply the required-only rule without
        # re-querying the contract from the report's own scope.
        object.__setattr__(report, "_required_ids", required_ids)
        return report

    async def _verify_one(
        self, criterion: SuccessCriterion, ctx: VerificationContext
    ) -> CriterionVerdict:
        # Unresolved (contradictory) requirements never auto-resolve
        # (REL-011.4, Requirement 14.2) — blocked regardless of stage results.
        # (Enforcement lives in ContractCompiler/DeterministicExtractor; this
        # stage loop treats every criterion uniformly regardless of source.)
        stage_results: dict[str, StageResult] = {}
        evidence_ids: list[str] = []
        first_fail_defect: VerificationDefect | None = None
        deterministic_failed = False

        for kind in _STAGE_ORDER:
            stage_name = _STAGE_NAME[kind]
            verifier = self.verifiers.get(kind)
            if verifier is None:
                stage_results[stage_name] = "skip_not_applicable"
                continue
            try:
                outcome, records, defect = await verifier.check(criterion, ctx)
            except Exception as exc:  # noqa: BLE001
                outcome, records, defect = (
                    "fail",
                    [],
                    VerificationDefect(
                        criterion_id=criterion.id,
                        severity="major",
                        problem=f"{stage_name} verifier raised: {exc}",
                        stage=stage_name,  # type: ignore[arg-type]
                    ),
                )
            stage_results[stage_name] = outcome
            for record in records:
                appended = self.ledger.append(record)
                evidence_ids.append(appended.id)
            if outcome == "fail":
                if kind == VerifierKind.DETERMINISTIC_POSTCONDITION:
                    deterministic_failed = True
                if first_fail_defect is None and defect is not None:
                    first_fail_defect = defect

        if deterministic_failed:
            # Deterministic failure always wins regardless of semantic result
            # (Requirement 12.5) — status is FAIL even if later stages passed.
            status = CriterionStatus.FAIL
        elif stage_results.get("artifact") == "fail" or stage_results.get("semantic") == "fail":
            status = CriterionStatus.FAIL
        else:
            status = CriterionStatus.PASS

        return CriterionVerdict(
            criterion_id=criterion.id,
            status=status,
            stage_results=stage_results,
            evidence_ids=tuple(evidence_ids),
            defect=first_fail_defect,
        )
