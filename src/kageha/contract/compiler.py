"""Contract_Compiler (REL-010/REL-012) — orchestrates contract compilation.

Classification -> deterministic extraction -> draft assembly -> semantic
completion -> budget resolution. Returns None for trivial/lookup turns so
the caller keeps building GoalCard.from_task() directly (Requirement 1.8).
"""

from __future__ import annotations

import uuid

from kageha.contract.extractor import DeterministicExtractor
from kageha.contract.models import (
    ContractStatus,
    Deliverable,
    RequirementKind,
    ResourceBudget,
    SuccessCriterion,
    TaskContract,
    VerifierKind,
    VerifierSpec,
)
from kageha.contract.semantic import SemanticCompletionService
from kageha.loop.verifier import is_lookup_status_text
from kageha.models.router import ModelRouter


def _new_id(prefix: str = "sc") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# Requirement kinds whose evidence is a concrete pass/fail check, not a
# subjective judgment call — mapped to deterministic postcondition / artifact
# checks rather than semantic judgment.
_DETERMINISTIC_VERIFIER_KIND = {
    RequirementKind.TEST_COMMAND: VerifierKind.DETERMINISTIC_POSTCONDITION,
    RequirementKind.FILE_COUNT: VerifierKind.ARTIFACT_CHECK,
    RequirementKind.FILENAME: VerifierKind.ARTIFACT_CHECK,
    RequirementKind.SLIDE_COUNT: VerifierKind.ARTIFACT_CHECK,
    RequirementKind.PAGE_COUNT: VerifierKind.ARTIFACT_CHECK,
    RequirementKind.DIMENSIONS: VerifierKind.ARTIFACT_CHECK,
    RequirementKind.CITATION: VerifierKind.ARTIFACT_CHECK,
    RequirementKind.BROWSER_OUTCOME: VerifierKind.ARTIFACT_CHECK,
    RequirementKind.PROHIBITION: VerifierKind.SEMANTIC_JUDGMENT,
}


def _is_trivial_or_lookup(objective: str) -> bool:
    text = (objective or "").strip()
    if not text:
        return True
    if len(text) < 6:
        return True
    return is_lookup_status_text(text)


class ContractCompiler:
    def __init__(self, router: ModelRouter) -> None:
        self.router = router
        self.extractor = DeterministicExtractor()
        self.semantic = SemanticCompletionService(router)

    async def compile(
        self,
        *,
        objective: str,
        session_id: str,
        turn_id: str,
        default_budget: ResourceBudget,
        requested_max_steps: int | None = None,
        requested_max_usd: float | None = None,
        requested_max_time_s: float | None = None,
    ) -> TaskContract | None:
        # 1. Classification — trivial/lookup turns skip contract compilation
        # entirely (Requirement 1.8) and skip the planner call (Requirement 7.7).
        if _is_trivial_or_lookup(objective):
            return None

        # 2. Deterministic extraction.
        extraction = self.extractor.extract(objective)

        # 3. Draft assembly — deterministic Requirement entries become
        # SuccessCriterion entries.
        success_criteria: list[SuccessCriterion] = []
        for req in extraction.requirements:
            verifier_kind = _DETERMINISTIC_VERIFIER_KIND.get(
                req.kind, VerifierKind.SEMANTIC_JUDGMENT
            )
            required = req.status != ContractStatus.UNRESOLVED
            success_criteria.append(
                SuccessCriterion(
                    id=_new_id("sc"),
                    required=required,
                    description=f"{req.kind.value}: {req.value}",
                    verifier=VerifierSpec(kind=verifier_kind),
                    source=req.source,
                )
            )

        contract = TaskContract(
            session_id=session_id,
            turn_id=turn_id,
            objective=objective,
            constraints=[],
            requirements=list(extraction.requirements),
            deliverables=[
                Deliverable(id=_new_id("del"), description=objective[:200])
            ],
            success_criteria=success_criteria,
            budget=ResourceBudget.resolve(
                default=default_budget,
                requested_max_steps=requested_max_steps,
                requested_max_usd=requested_max_usd,
                requested_max_time_s=requested_max_time_s,
            ),
            compiler_source="deterministic",
        )

        # 4. Semantic completion (skipped for trivial turns — already excluded above).
        existing_ids = {sc.id for sc in contract.success_criteria}
        existing_constraint_texts = {c.text for c in contract.constraints}
        try:
            completion = await self.semantic.complete(
                objective=objective,
                existing_criterion_ids=existing_ids,
                existing_constraint_texts=existing_constraint_texts,
            )
        except Exception:  # noqa: BLE001
            # Planner call failure — never block the turn (REL-012.5).
            contract.compiler_source = "deterministic_fallback"
            return contract

        if completion.fallback:
            contract.compiler_source = "deterministic_fallback"
            return contract

        if completion.added_criteria or completion.added_constraints:
            contract.success_criteria = contract.success_criteria + completion.added_criteria
            contract.constraints = contract.constraints + completion.added_constraints
            contract.compiler_source = "deterministic+semantic"

        return contract
