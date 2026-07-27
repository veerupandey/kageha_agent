"""Skill proposal helpers; memory candidates never activate skills directly.

Hermes-style lifecycle: observe → distill → reuse → refine.
``skill_manage`` covers observe/refine; this module handles end-of-run distill.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any

from kageha.memory.skills import SkillRegistry


@dataclass
class DistillProposal:
    name: str
    content: str
    reason: str
    action: str = "create"  # create | refine


def should_distill(*, tool_calls: int, recovered_error: bool, user_correction: bool) -> bool:
    if user_correction or recovered_error:
        return True
    return tool_calls >= 5


def propose_skill(
    *,
    task: str,
    transcript_summary: str,
    name_hint: str | None = None,
    refine_existing: str | None = None,
) -> DistillProposal:
    if refine_existing:
        body = (
            f"Post-run improvements for task: {task[:200]}\n\n"
            f"{transcript_summary}\n"
        )
        return DistillProposal(
            name=refine_existing,
            content=body,
            reason="post-run refine",
            action="refine",
        )
    slug = (name_hint or "learned-workflow").lower().replace(" ", "-")[:40]
    # Keep slug skill-name safe
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in slug).strip("-_") or (
        "learned-workflow"
    )
    desc = f"Learned workflow for {task[:120]}".replace('"', "'")
    content = (
        f"---\nname: {slug}\ndescription: \"{desc}\"\n---\n\n"
        f"# {slug}\n\n"
        f"## Context\n\nTask: {task}\n\n"
        f"## Procedure\n\n{transcript_summary}\n\n"
        "## Verification\n\n- Confirm deliverables exist in the session workspace\n"
    )
    return DistillProposal(name=slug, content=content, reason="post-run distill", action="create")


def apply_proposal(
    proposal: DistillProposal,
    registry: SkillRegistry,
    *,
    approved: bool,
    evaluation_passed: bool,
    evaluation_evidence: str = "",
) -> str:
    if not approved:
        return "DENIED: skill creation was not approved"
    if not evaluation_passed or not evaluation_evidence.strip():
        return (
            "DENIED: procedure candidates require a passing skill evaluation "
            "with evidence before activation"
        )
    if proposal.action == "refine":
        return registry.manage(
            "refine",
            proposal.name,
            proposal.content,
            require_hitl_create=True,
            approved=True,
        )
    return registry.manage(
        "create",
        proposal.name,
        proposal.content,
        require_hitl_create=True,
        approved=True,
    )


def proposal_from_run(
    *,
    task: str,
    message: str,
    status: str,
    steps: int,
    recovered_failures: list[str] | None = None,
    verification_evidence: str = "",
    active_skills: list[str] | None = None,
) -> DistillProposal | None:
    """Build a distill proposal when the run looks worth saving as a skill."""
    recovered = list(recovered_failures or [])
    if not should_distill(
        tool_calls=steps,
        recovered_error=bool(recovered),
        user_correction=False,
    ):
        return None
    if status not in {"success", "completed", "done", "ok"} and not recovered:
        # Still allow distill after recovery even if final status is messy
        if status not in {"partial", "needs_input", "ask_user"}:
            return None
    summary_parts = [
        f"Status: {status}",
        f"Steps: {steps}",
    ]
    if message.strip():
        summary_parts.append(f"Outcome:\n{message.strip()[:1200]}")
    if verification_evidence.strip():
        summary_parts.append(f"Evidence:\n{verification_evidence.strip()[:800]}")
    if recovered:
        summary_parts.append("Recovered failures:\n- " + "\n- ".join(recovered[:8]))
    summary = "\n\n".join(summary_parts)
    active = [n for n in (active_skills or []) if n]
    if active:
        return propose_skill(
            task=task,
            transcript_summary=summary,
            refine_existing=active[0],
        )
    hint = None
    words = [w for w in task.lower().split() if w.isalnum()][:4]
    if words:
        hint = "-".join(words)
    return propose_skill(task=task, transcript_summary=summary, name_hint=hint)


def _prompt_yes_no(prompt: str) -> bool:
    """Ask on tty; return False when non-interactive or declined."""
    if os.environ.get("KAGEHA_DISTILL", "").strip().lower() in {"0", "false", "no", "off"}:
        return False
    if not sys.stdin.isatty():
        return False
    try:
        ans = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(flush=True)
        return False
    return ans in {"", "y", "yes"}


def maybe_prompt_skill_distill(
    result: Any,
    *,
    task: str,
    registry: SkillRegistry | None = None,
    interactive: bool = True,
    active_skills: list[str] | None = None,
) -> str | None:
    """Offer post-run skill distill (HITL or unattended auto). Returns result or None."""
    from kageha.memory.skill_learn import (
        skill_learn_unattended_enabled,
        stamp_unattended_provenance,
    )

    skills_used = list(active_skills or getattr(result, "active_skills", None) or [])
    proposal = proposal_from_run(
        task=task or result.message or "task",
        message=getattr(result, "message", "") or "",
        status=getattr(result, "status", "") or "",
        steps=int(getattr(result, "steps", 0) or 0),
        recovered_failures=list(getattr(result, "recovered_failures", None) or []),
        verification_evidence=getattr(result, "verification_evidence", "") or "",
        active_skills=skills_used,
    )
    if proposal is None:
        return None
    if os.environ.get("KAGEHA_DISTILL", "").strip().lower() in {"0", "false", "no", "off"}:
        return None
    unattended = skill_learn_unattended_enabled(interactive=interactive)
    if not interactive and not unattended:
        return None
    if not unattended:
        if proposal.action == "refine":
            print(
                f"\nRefine active skill from this run?\n"
                f"  skill: {proposal.name}\n"
                f"  reason: {proposal.reason} ({getattr(result, 'steps', 0)} steps)\n",
                flush=True,
            )
            if not _prompt_yes_no("Refine skill? [Y/n] "):
                return "SKIPPED: user declined skill refine"
        else:
            print(
                f"\nSave this run as a reusable skill?\n"
                f"  name: {proposal.name}\n"
                f"  reason: {proposal.reason} ({getattr(result, 'steps', 0)} steps)\n",
                flush=True,
            )
            if not _prompt_yes_no("Create skill? [Y/n] "):
                return "SKIPPED: user declined skill distill"
    else:
        print(
            f"\n[unattended] Applying skill {proposal.action} for {proposal.name}…",
            flush=True,
        )
        if proposal.action == "create":
            proposal = DistillProposal(
                name=proposal.name,
                content=stamp_unattended_provenance(proposal.content),
                reason=proposal.reason,
                action=proposal.action,
            )
    evidence = (
        (getattr(result, "verification_evidence", "") or "").strip()
        or (getattr(result, "message", "") or "").strip()
        or f"run {getattr(result, 'run_id', '')} status={getattr(result, 'status', '')}"
    )
    passed = bool(getattr(result, "validated", False)) or getattr(result, "status", "") in {
        "success",
        "completed",
        "done",
        "ok",
    }
    reg = registry or SkillRegistry()
    out = apply_proposal(
        proposal,
        reg,
        approved=True,
        evaluation_passed=passed,
        evaluation_evidence=evidence,
    )
    print(out, flush=True)
    return out
