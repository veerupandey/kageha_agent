"""Semantic_Completion_Service (REL-012) — planner-model contract completion.

Sends the deterministic draft contract to the planner model for structured
completion, additively merging valid new SuccessCriterion/Constraint entries
without ever weakening or deleting an explicit/deterministic entry.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field

from kageha.contract.models import (
    Constraint,
    ConstraintSource,
    ContractStatus,
    SuccessCriterion,
    VerifierKind,
    VerifierSpec,
)
from kageha.models.base import ChatMessage
from kageha.models.router import ModelRouter


def _new_id(prefix: str = "sem") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@dataclass
class SemanticCompletionResult:
    added_criteria: list[SuccessCriterion] = field(default_factory=list)
    added_constraints: list[Constraint] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)  # human-readable rejection reasons
    fallback: bool = False  # True when planner failed/invalid — draft returned unchanged


_VALID_VERIFIER_KINDS = {k.value for k in VerifierKind}


class SemanticCompletionService:
    """Adds semantic criteria/constraints on top of a deterministic draft."""

    def __init__(self, router: ModelRouter) -> None:
        self.router = router

    async def complete(
        self,
        *,
        objective: str,
        existing_criterion_ids: set[str],
        existing_constraint_texts: set[str],
        role: str = "planning",
    ) -> SemanticCompletionResult:
        prompt = (
            "You are completing a task contract for an autonomous coding/task agent.\n"
            "Given the user's request, propose ADDITIONAL success criteria and "
            "constraints that are implied but not explicitly stated (subjective "
            "quality bars, implicit conventions, safety constraints). Do not "
            "restate or attempt to modify any existing criterion.\n"
            "Return ONLY JSON with this schema:\n"
            "{\n"
            '  "success_criteria": [{"id": str, "required": bool, '
            '"description": str, "verifier_kind": str, "depends_on": [str]}],\n'
            '  "constraints": [{"id": str, "text": str}]\n'
            "}\n"
            "verifier_kind must be one of: "
            f"{', '.join(sorted(_VALID_VERIFIER_KINDS))}.\n"
            "Do not invent ids matching any existing id. Add nothing if the "
            "request has no meaningful implicit criteria.\n\n"
            f"Existing criterion ids: {sorted(existing_criterion_ids)}\n\n"
            f"User request: {objective}"
        )
        try:
            _, resp = await self.router.chat(
                [ChatMessage(role="user", content=prompt)],
                role=role,
                max_tokens=1024,
                effort="medium",
            )
            text = resp.message.content or ""
            match = re.search(r"\{.*\}", text, flags=re.S)
            if not match:
                return SemanticCompletionResult(fallback=True)
            data = json.loads(match.group(0))
        except Exception:  # noqa: BLE001
            return SemanticCompletionResult(fallback=True)

        result = SemanticCompletionResult()
        for item in data.get("success_criteria") or []:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id") or "").strip() or _new_id("sc")
            if cid in existing_criterion_ids:
                result.rejected.append(
                    f"criterion id collides with existing entry: {cid}"
                )
                continue
            verifier_kind = str(item.get("verifier_kind") or "").strip()
            if verifier_kind not in _VALID_VERIFIER_KINDS:
                result.rejected.append(
                    f"criterion {cid} has invalid verifier_kind: {verifier_kind}"
                )
                continue
            depends_on = tuple(str(d) for d in (item.get("depends_on") or []))
            # Dependency ids must resolve to an existing or already-added criterion.
            known_ids = existing_criterion_ids | {c.id for c in result.added_criteria}
            if any(d not in known_ids and d != cid for d in depends_on):
                result.rejected.append(
                    f"criterion {cid} has an invalid dependency id in {depends_on}"
                )
                continue
            description = str(item.get("description") or "").strip()
            if not description:
                result.rejected.append(f"criterion {cid} has an empty description")
                continue
            result.added_criteria.append(
                SuccessCriterion(
                    id=cid,
                    required=bool(item.get("required", False)),
                    description=description,
                    verifier=VerifierSpec(kind=VerifierKind(verifier_kind)),
                    depends_on=depends_on,
                    source=ConstraintSource.SEMANTIC,
                )
            )

        for item in data.get("constraints") or []:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id") or "").strip() or _new_id("con")
            text_value = str(item.get("text") or "").strip()
            if not text_value:
                result.rejected.append(f"constraint {cid} has empty text")
                continue
            if text_value in existing_constraint_texts:
                # Additive only — do not duplicate/weaken an existing constraint.
                result.rejected.append(
                    f"constraint duplicates existing text: {text_value[:60]}"
                )
                continue
            result.added_constraints.append(
                Constraint(
                    id=cid,
                    text=text_value,
                    source=ConstraintSource.SEMANTIC,
                    status=ContractStatus.ACTIVE,
                )
            )
        return result
