"""Spec clarify phase — interactive questions before plan.md (Kiro-style).

Runs after explore and before ``make_plan`` / final Spec artifacts.
Unambiguous tasks skip with recorded assumptions ("No questions — Continue").
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kageha.harness.approvals import (
    ApprovalRequest,
    _channel_asker,
    cli_ask_human,
)
from kageha.models.base import ChatMessage


# Visible Design phase labels for Spec machine.
SPEC_DESIGN_PHASES: tuple[str, ...] = (
    "requirements",
    "clarify",
    "plan",
    "skill_gaps",
    "build",
)

SKIP_CONTINUE_LABEL = "No questions — Continue"

_AMBIGUOUS_RE = re.compile(
    r"\b("
    r"our|checkout|payment|shop|cart|flow|system|platform|integrate|integration|"
    r"redesign|improve|better|refactor|somehow|something|support|handle|"
    r"dashboard|auth|onboard|pipeline|migrate|sync"
    r")\b",
    re.I,
)
_CONCRETE_FILE_RE = re.compile(
    r"\b(?:create|write|add|make|implement)\b.{0,80}\b[\w./-]+\.\w{1,8}\b",
    re.I,
)
_OPEN_Q_SECTION_RE = re.compile(
    r"(?is)^(.*?\n##\s+Open questions\s*\n)(.*?)(\n##\s+\S.*|\Z)"
)


@dataclass
class ClarifyProposal:
    questions: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    skip: bool = False
    source: str = "heuristic"


@dataclass
class ClarifyResult:
    questions: list[str] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    skipped: bool = False
    open_questions: list[str] = field(default_factory=list)
    interactive: bool = False
    continued: bool = True


def task_needs_clarify(task: str) -> bool:
    """Heuristic: underspecified product/engineering asks need clarify."""
    text = (task or "").strip()
    if not text:
        return True
    if _CONCRETE_FILE_RE.search(text):
        return False
    # Bare create-file style without the verb+ext pattern still counts concrete.
    if re.search(r"\b[\w.-]+\.(py|md|ts|tsx|js|json|yml|yaml|txt)\b", text) and re.search(
        r"\b(create|write|add|print|exists?)\b", text, re.I
    ):
        return False
    if _AMBIGUOUS_RE.search(text):
        return True
    words = text.split()
    if len(words) <= 8 and not re.search(r"[`\"'].*[`\"']", text):
        return True
    return False


def heuristic_questions(task: str) -> list[str]:
    """Concrete clarifying questions from task shape (no LLM required)."""
    low = (task or "").lower()
    qs: list[str] = []
    if any(k in low for k in ("checkout", "payment", "shop", "cart", "stripe")):
        qs.append(
            "Which payment provider should checkout use (Stripe, PayPal, other)?"
        )
        qs.append("Should guest checkout be allowed, or login-required only?")
    elif any(k in low for k in ("auth", "login", "oauth", "sso")):
        qs.append("Which auth model (session cookie, JWT, OAuth/OIDC provider)?")
        qs.append("Which roles or account types must be supported first?")
    elif any(k in low for k in ("api", "endpoint", "rest", "graphql")):
        qs.append("Which routes/methods are in scope for v1?")
        qs.append("What auth and error-contract constraints apply?")
    elif any(k in low for k in ("dashboard", "ui", "frontend", "page")):
        qs.append("Which primary user and screen(s) are in scope for v1?")
        qs.append("Any required stack or design-system constraints?")
    else:
        brief = " ".join((task or "").split())[:100]
        qs.append(f"What is the primary deliverable for: {brief}?")
        qs.append(
            "Which constraints matter most (stack, out-of-scope, success check)?"
        )
    # Dedupe preserve order
    out: list[str] = []
    seen: set[str] = set()
    for q in qs:
        key = q.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(q.strip())
    return out[:3]


def heuristic_assumptions(task: str) -> list[str]:
    brief = " ".join((task or "").split())[:160] or "(unspecified)"
    return [
        f"Proceed with objective as stated: {brief}",
        "Prefer smallest change that meets the acceptance criteria.",
    ]


async def propose_clarify(
    task: str,
    *,
    router: Any = None,
    explore_notes: str = "",
    role: str = "planning",
) -> ClarifyProposal:
    """Propose questions or skip with assumptions.

    Tries a short LLM JSON propose when a router is available; falls back to
    heuristics so Spec clarify works offline / in tests.
    """
    needs = task_needs_clarify(task)
    if not needs:
        return ClarifyProposal(
            questions=[],
            assumptions=heuristic_assumptions(task),
            skip=True,
            source="heuristic_skip",
        )

    if router is not None:
        try:
            proposed = await _llm_propose(
                task,
                router=router,
                explore_notes=explore_notes,
                role=role,
            )
            if proposed is not None:
                return proposed
        except Exception:  # noqa: BLE001
            pass

    return ClarifyProposal(
        questions=heuristic_questions(task),
        assumptions=[],
        skip=False,
        source="heuristic",
    )


async def _llm_propose(
    task: str,
    *,
    router: Any,
    explore_notes: str,
    role: str,
) -> ClarifyProposal | None:
    notes = (explore_notes or "").strip()[:1500]
    system = (
        "You clarify Spec requirements before planning. "
        "If the objective is already concrete enough to plan, return "
        '{"skip": true, "assumptions": ["..."], "questions": []}. '
        "If underspecified, return 1-3 concrete questions "
        '{"skip": false, "questions": ["..."], "assumptions": []}. '
        "Questions must be answerable in one short phrase. JSON only."
    )
    user = f"Objective:\n{task.strip()}\n"
    if notes:
        user += f"\nExplore notes:\n{notes}\n"
    history = [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=user),
    ]
    _model, resp = await router.chat(
        history,
        tools=[],
        role=role or "planning",
        max_tokens=400,
        effort="low",
    )
    text = (getattr(resp, "message", None) and resp.message.content) or ""
    data = _parse_json_object(text)
    if not isinstance(data, dict):
        return None
    skip = bool(data.get("skip"))
    questions = [
        str(q).strip()
        for q in (data.get("questions") or [])
        if str(q).strip()
    ][:3]
    assumptions = [
        str(a).strip()
        for a in (data.get("assumptions") or [])
        if str(a).strip()
    ][:4]
    if skip or not questions:
        return ClarifyProposal(
            questions=[],
            assumptions=assumptions or heuristic_assumptions(task),
            skip=True,
            source="llm_skip",
        )
    return ClarifyProposal(
        questions=questions,
        assumptions=assumptions,
        skip=False,
        source="llm",
    )


def _parse_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


def render_requirements_markdown(
    *,
    task: str,
    phase: str = "clarify",
    milestones: list[str] | None = None,
    open_questions: list[str] | None = None,
    assumptions: list[str] | None = None,
    explore_notes: str = "",
    answers: list[str] | None = None,
) -> str:
    """Render requirements.md including real Open questions (never stub-only)."""
    ms = [m for m in (milestones or []) if str(m).strip()]
    questions = [q for q in (open_questions or []) if str(q).strip()]
    assumptions = [a for a in (assumptions or []) if str(a).strip()]
    answers = [a for a in (answers or []) if str(a).strip()]

    lines = [
        "# Requirements",
        "",
        "## Phase",
        f"{phase} — Spec machine",
        "",
        "## Objective",
        (task or "").strip() or "(unspecified)",
        "",
        "## Acceptance criteria",
    ]
    if ms:
        lines.extend(f"- {m}" for m in ms)
    else:
        lines.append("- Deliverables match the objective and plan steps.")

    lines.extend(["", "## Open questions"])
    if questions:
        for i, q in enumerate(questions):
            lines.append(f"- Q: {q}")
            if i < len(answers) and answers[i]:
                lines.append(f"  - A: {answers[i]}")
            elif len(answers) == 1 and i == 0:
                # Single free-text reply covering all questions.
                lines.append(f"  - A: {answers[0]}")
    elif assumptions:
        lines.append(f"- {SKIP_CONTINUE_LABEL}")
        for a in assumptions:
            lines.append(f"- Assumption: {a}")
    else:
        lines.append(f"- {SKIP_CONTINUE_LABEL}")

    if assumptions and questions:
        lines.extend(["", "## Assumptions"])
        lines.extend(f"- {a}" for a in assumptions)

    if (explore_notes or "").strip():
        lines.extend(
            ["", "## Explore notes", "", explore_notes.strip()[:2000], ""]
        )
    else:
        lines.append("")
    return "\n".join(lines) + "\n"


def write_requirements_draft(
    workspace_root: Path,
    *,
    task: str,
    questions: list[str] | None = None,
    assumptions: list[str] | None = None,
    answers: list[str] | None = None,
    explore_notes: str = "",
    phase: str = "clarify",
) -> str:
    text = render_requirements_markdown(
        task=task,
        phase=phase,
        open_questions=questions,
        assumptions=assumptions,
        answers=answers,
        explore_notes=explore_notes,
    )
    (workspace_root / "requirements.md").write_text(text, encoding="utf-8")
    return text


def parse_open_question_answers(text: str) -> tuple[list[str], list[str]]:
    """Parse ``- Q:`` / ``- A:`` pairs (and Assumption lines) from requirements."""
    questions: list[str] = []
    answers: list[str] = []
    section = ""
    match = _OPEN_Q_SECTION_RE.search(text or "")
    if match:
        section = match.group(2)
    else:
        section = text or ""
    pending_q: str | None = None
    for raw in section.splitlines():
        line = raw.strip()
        if not line.startswith("-"):
            continue
        body = line.lstrip("-").strip()
        low = body.lower()
        if low.startswith("q:"):
            if pending_q is not None:
                questions.append(pending_q)
                answers.append("")
            pending_q = body[2:].strip()
        elif low.startswith("a:") and pending_q is not None:
            questions.append(pending_q)
            answers.append(body[2:].strip())
            pending_q = None
        elif low.startswith("assumption:"):
            # Ignore for Q list; caller can re-read assumptions separately.
            continue
        elif body == SKIP_CONTINUE_LABEL:
            continue
        elif pending_q is None and body and not low.startswith("none recorded"):
            # Plain bullet treated as answered note / legacy open question.
            questions.append(body)
            answers.append("")
    if pending_q is not None:
        questions.append(pending_q)
        answers.append("")
    return questions, answers


def open_questions_for_artifacts(
    *,
    questions: list[str],
    answers: list[str],
    assumptions: list[str],
    skipped: bool,
) -> list[str]:
    """Flatten clarify outcome into requirements Open questions bullets."""
    out: list[str] = []
    if skipped and not questions:
        out.append(SKIP_CONTINUE_LABEL)
        for a in assumptions:
            out.append(f"Assumption: {a}")
        return out or [SKIP_CONTINUE_LABEL]
    if len(answers) == 1 and len(questions) > 1 and answers[0]:
        # One free-text reply — attach to each Q for visibility.
        joined = answers[0]
        for q in questions:
            out.append(f"Q: {q}")
            out.append(f"A: {joined}")
        return out
    for i, q in enumerate(questions):
        out.append(f"Q: {q}")
        ans = answers[i] if i < len(answers) else ""
        if ans:
            out.append(f"A: {ans}")
        else:
            out.append("A: (unanswered — planner should state assumptions)")
    for a in assumptions:
        out.append(f"Assumption: {a}")
    return out


def _format_clarify_prompt(questions: list[str]) -> str:
    lines = [
        "Spec clarify — answer before plan is finalized:",
        "",
    ]
    for i, q in enumerate(questions, 1):
        lines.append(f"{i}. {q}")
    lines.append("")
    lines.append(
        "Reply with short answers (one line or numbered). "
        "Or edit requirements.md Open questions, then Continue."
    )
    return "\n".join(lines)


async def run_spec_clarify_phase(
    *,
    task: str,
    workspace_root: Path,
    router: Any = None,
    events: Any = None,
    approvals: Any = None,
    explore_notes: str = "",
    role: str = "planning",
    auto_continue: bool = False,
    defer_human_input: bool = False,
    log: Any = None,
) -> ClarifyResult:
    """Run Spec clarify before ``make_plan``.

    - Unambiguous → record assumptions + skip label (no HITL pause).
    - Ambiguous → draft requirements, pause for answer when a human is available,
      then persist Q&A into ``requirements.md`` Open questions.
    """

    def _log(msg: str) -> None:
        if log:
            log(msg)

    proposal = await propose_clarify(
        task,
        router=router,
        explore_notes=explore_notes,
        role=role,
    )
    if events:
        events.emit(
            "spec_clarify_proposed",
            {
                "skip": proposal.skip,
                "questions": list(proposal.questions),
                "assumptions": list(proposal.assumptions),
                "source": proposal.source,
                "phases": list(SPEC_DESIGN_PHASES),
            },
        )

    if proposal.skip or not proposal.questions:
        assumptions = list(proposal.assumptions) or heuristic_assumptions(task)
        write_requirements_draft(
            workspace_root,
            task=task,
            questions=[],
            assumptions=assumptions,
            explore_notes=explore_notes,
            phase="clarify",
        )
        open_qs = open_questions_for_artifacts(
            questions=[],
            answers=[],
            assumptions=assumptions,
            skipped=True,
        )
        status = {
            "status": "skipped",
            "message": SKIP_CONTINUE_LABEL,
            "assumptions": assumptions,
        }
        (workspace_root / "clarify_status.json").write_text(
            json.dumps(status, indent=2) + "\n", encoding="utf-8"
        )
        if events:
            events.emit(
                "spec_clarify_done",
                {
                    "skipped": True,
                    "message": SKIP_CONTINUE_LABEL,
                    "phases": list(SPEC_DESIGN_PHASES),
                },
            )
        _log(f"[kageha] spec clarify: {SKIP_CONTINUE_LABEL}")
        return ClarifyResult(
            questions=[],
            answers=[],
            assumptions=assumptions,
            skipped=True,
            open_questions=open_qs,
            interactive=False,
            continued=True,
        )

    # Draft requirements with unanswered questions for Design panel.
    write_requirements_draft(
        workspace_root,
        task=task,
        questions=list(proposal.questions),
        assumptions=list(proposal.assumptions),
        explore_notes=explore_notes,
        phase="clarify",
    )
    if events:
        events.emit(
            "design_artifacts",
            {
                "agent_mode": "spec",
                "artifacts": ["requirements.md"],
                "phases": list(SPEC_DESIGN_PHASES),
                "awaiting_clarify": True,
                "message": "Clarify requirements before plan",
            },
        )
        events.emit(
            "spec_clarify_required",
            {
                "questions": list(proposal.questions),
                "phases": list(SPEC_DESIGN_PHASES),
            },
        )

    answers: list[str] = []
    interactive = False
    continued = True
    prompt = _format_clarify_prompt(proposal.questions)

    can_ask_approval = (
        not auto_continue
        and approvals is not None
        and getattr(approvals, "approver", None) is not None
    )
    can_ask_cli = (
        not auto_continue
        and not defer_human_input
        and (
            _channel_asker.get() is not None
            or (sys.stdin.isatty() and sys.stdout.isatty())
        )
    )

    # WebUI (defer_human_input): Continue via approval bus + Design edit.
    # CLI/TTY: free-text ask_human (avoid binary Approve then re-ask).
    if can_ask_approval and defer_human_input:
        interactive = True
        _log("[kageha] waiting for Spec clarify answers…")
        req = ApprovalRequest(
            action="spec_clarify",
            detail=prompt[:4000],
            risk_class="clarify",
        )
        continued = bool(await approvals.require_explicit(req))
        try:
            disk = (workspace_root / "requirements.md").read_text(encoding="utf-8")
            qs, ans = parse_open_question_answers(disk)
            if any(a.strip() for a in ans):
                answers = [a for a in ans if a.strip()]
                if qs:
                    proposal.questions = qs
        except OSError:
            pass
    elif can_ask_cli:
        interactive = True
        _log("[kageha] Spec clarify (CLI)…")
        try:
            reply = (await cli_ask_human(prompt)).strip()
        except Exception:  # noqa: BLE001
            reply = ""
        if reply:
            answers = [reply]
            continued = True
    elif can_ask_approval:
        interactive = True
        _log("[kageha] waiting for Spec clarify Continue…")
        req = ApprovalRequest(
            action="spec_clarify",
            detail=prompt[:4000],
            risk_class="clarify",
        )
        continued = bool(await approvals.require_explicit(req))
        try:
            disk = (workspace_root / "requirements.md").read_text(encoding="utf-8")
            qs, ans = parse_open_question_answers(disk)
            if any(a.strip() for a in ans):
                answers = [a for a in ans if a.strip()]
                if qs:
                    proposal.questions = qs
        except OSError:
            pass

    if answers:
        assumptions = list(proposal.assumptions)
    elif proposal.assumptions:
        assumptions = list(proposal.assumptions)
    else:
        assumptions = [
            "No human answer yet — planner will state reversible assumptions."
        ]

    open_qs = open_questions_for_artifacts(
        questions=list(proposal.questions),
        answers=answers,
        assumptions=assumptions if not answers else list(proposal.assumptions),
        skipped=False,
    )
    write_requirements_draft(
        workspace_root,
        task=task,
        questions=list(proposal.questions),
        assumptions=assumptions if not answers else list(proposal.assumptions),
        answers=answers,
        explore_notes=explore_notes,
        phase="requirements",
    )

    status = {
        "status": "answered" if answers else "continued",
        "message": "Clarify complete" if continued else "Clarify denied — continuing",
        "questions": list(proposal.questions),
        "answers": answers,
        "interactive": interactive,
        "continued": continued,
    }
    (workspace_root / "clarify_status.json").write_text(
        json.dumps(status, indent=2) + "\n", encoding="utf-8"
    )
    if events:
        events.emit(
            "spec_clarify_done",
            {
                "skipped": False,
                "answered": bool(answers),
                "questions": len(proposal.questions),
                "phases": list(SPEC_DESIGN_PHASES),
            },
        )
    _log(
        f"[kageha] spec clarify done "
        f"(questions={len(proposal.questions)} answered={bool(answers)})"
    )
    return ClarifyResult(
        questions=list(proposal.questions),
        answers=answers,
        assumptions=assumptions,
        skipped=False,
        open_questions=open_qs,
        interactive=interactive,
        continued=continued,
    )
