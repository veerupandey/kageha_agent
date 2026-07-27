"""Agent mode policy: normal | plan | spec | goal (trimmed harness).

Modes are different *machines* (artifacts + gates), not prompt variants.
One LoopController; mode selects design artifacts, approval, and execute rights.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

AgentMode = Literal["normal", "plan", "spec", "goal"]

AGENT_MODES: frozenset[str] = frozenset({"normal", "plan", "spec", "goal"})

AGENT_MODE_FLAG = "agent_mode.flag"
PLAN_APPROVED_FLAG = "plan_approved.flag"

# Spec machine: three artifacts required before Build/execute.
SPEC_ARTIFACTS: tuple[str, ...] = (
    "requirements.md",
    "plan.md",
    "skill_gaps.md",
)

# Tools that mutate project/source — blocked in Plan design until approved.
PLAN_DESIGN_BLOCKED_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "edit_file",
        "bash",
        "shell",
        "run_terminal",
        "spawn_subagent",
        "spawn_subagents",
        "spawn_task_graph",
        "skill_manage",
        "skill_install",
        "skill_run",
        "todo_write",
    }
)

_MODE_CMD_RE = re.compile(r"^/(plan|spec|goal|normal)\b", re.I)
_MODE_ONLY_RE = re.compile(r"^/?(plan|spec|goal|normal)\s*$", re.I)

_ARTIFACTS_DEFAULT = (
    "File deliverables DEFAULT to `artifacts/` (e.g. `artifacts/deck.pptx`, "
    "`artifacts/report.pdf`). Never leave final user outputs in the project root."
)

MODE_PROMPTS: dict[str, str] = {
    "normal": (
        "## Agent mode: normal\n"
        "Conversational self-depth. Answer simply in chat when possible — "
        "do not create unsolicited .md deliverables for small talk or factual Q&A. "
        f"{_ARTIFACTS_DEFAULT} "
        "Escalate with escalate_plan(mode='plan'|'spec'|'goal') or tell the user "
        "to send /plan, /spec, or /goal for deeper work."
    ),
    "plan": (
        "## Agent mode: plan (design machine)\n"
        "HARD RULE: read-only explore → write plan.md → wait for Build.\n"
        "You may list_dir/read_file/web_search during design. "
        "Mutating tools (write/edit/bash/spawn/skill_run) are HARD-DENIED until "
        "the user Builds. After Build, execute the approved plan and put "
        f"user-facing outputs under `artifacts/`. {_ARTIFACTS_DEFAULT}"
    ),
    "spec": (
        "## Agent mode: spec (requirements machine — Kiro-style phases)\n"
        "Phases: requirements → clarify → plan → skill_gaps → build.\n"
        "Ask concrete clarifying questions when the task is underspecified "
        "before finalizing plan.md; record assumptions when none are needed.\n"
        "Read-only research is allowed before Build; mutations are HARD-DENIED. "
        "No fan-out execution until all three artifacts exist and the user Builds. "
        f"After Build, {_ARTIFACTS_DEFAULT}"
    ),
    "goal": (
        "## Agent mode: goal\n"
        "Maximize autonomy toward a verifiable outcome (ship/verify), not Q&A. "
        "Maintain a living goal card and task DAG. Invent skills/forge under HITL "
        "when gaps block progress. Hard risk classes (destructive shell, elevated, "
        "messaging, skill writes) still require human approval. Stop only on goal "
        "SUCCESS or explicit cancel.\n"
        f"{_ARTIFACTS_DEFAULT} "
        "If the user asks a pure informational question, answer briefly like Normal "
        "— do not invent skills or DAG theater."
    ),
}

# WebUI / slash helper copy (keep in sync with static catalog fallbacks).
MODE_CHIP_DESCRIPTIONS: dict[str, str] = {
    "normal": "Normal mode — standard chat",
    "plan": "Plan mode — design before acting",
    "spec": "Spec mode — detailed requirements",
    "goal": "Goal — verifiable outcome, not Q&A",
}

GOAL_QA_MISFIT_MESSAGE = "This looks like Normal"


def normalize_agent_mode(raw: str | None) -> AgentMode:
    m = (raw or "normal").strip().lower()
    if m in AGENT_MODES:
        return m  # type: ignore[return-value]
    if m in {"full", "executive"}:
        return "plan"
    return "normal"


def loop_mode_for(agent_mode: AgentMode | str) -> str:
    """Map agent mode → LoopController loop_mode."""
    mode = normalize_agent_mode(str(agent_mode))
    if mode == "normal":
        return "followup"
    return "full"


def parse_mode_slash(message: str) -> AgentMode | None:
    m = _MODE_CMD_RE.match((message or "").strip())
    if not m:
        return None
    return normalize_agent_mode(m.group(1))


def strip_mode_slash(message: str) -> str:
    """Remove a leading ``/plan|/spec|/goal|/normal`` token.

    Mode-only messages (``/plan`` with no task) return ``\"\"`` — never echo
    the mode token back as the objective (that produced junk plan.md).
    """
    text = (message or "").strip()
    if not text:
        return ""
    if _MODE_CMD_RE.match(text):
        return _MODE_CMD_RE.sub("", text).strip()
    return text


def is_mode_only_message(message: str) -> bool:
    """True when the user only switched mode (no real objective)."""
    text = (message or "").strip()
    if not text:
        return True
    if _MODE_ONLY_RE.match(text):
        return True
    if _MODE_CMD_RE.match(text) and not strip_mode_slash(text):
        return True
    return False


def mode_only_ack(agent_mode: AgentMode | str) -> str:
    mode = normalize_agent_mode(str(agent_mode))
    label = mode.capitalize()
    if mode == "normal":
        return "Normal mode on. Send your next message."
    if mode == "plan":
        return (
            "Plan mode on — design only until you Build.\n\n"
            "Send the **real objective** next (what to plan). "
            "Do not send just `/plan` or `plan`."
        )
    if mode == "spec":
        return (
            "Spec mode on — requirements → clarify → plan → skill gaps → Build.\n\n"
            "Send the **real objective** next. Do not send just `/spec`."
        )
    if mode == "goal":
        return (
            "Goal mode on — verifiable outcome, not Q&A.\n\n"
            "Send a ship/verify objective next. Pure questions fit Normal better."
        )
    return (
        f"{label} mode on.\n\n"
        f"Send the **real objective** next. Do not send just `/{mode}`."
    )


def read_agent_mode_flag(workspace_root: Path | None) -> AgentMode | None:
    if workspace_root is None:
        return None
    path = workspace_root / AGENT_MODE_FLAG
    if not path.is_file():
        return None
    try:
        return normalize_agent_mode(path.read_text(encoding="utf-8").splitlines()[0])
    except OSError:
        return None


def write_agent_mode_flag(workspace_root: Path, mode: AgentMode | str) -> None:
    m = normalize_agent_mode(str(mode))
    (workspace_root / AGENT_MODE_FLAG).write_text(m + "\n", encoding="utf-8")


def clear_agent_mode_flag(workspace_root: Path) -> None:
    path = workspace_root / AGENT_MODE_FLAG
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def resolve_agent_mode(
    message: str = "",
    *,
    explicit: str | None = None,
    workspace_root: Path | None = None,
    consume_flag: bool = False,
) -> AgentMode:
    """Precedence: slash command → explicit param → workspace flag → normal.

    Slash wins so ``/plan …`` (and friends) work even when a client sends a
    default ``agent_mode=normal`` (CLI ``--mode`` default, older WebUI payloads).
    """
    slash = parse_mode_slash(message)
    if slash:
        return slash
    if explicit:
        return normalize_agent_mode(explicit)
    flagged = read_agent_mode_flag(workspace_root)
    if flagged:
        if consume_flag and workspace_root is not None:
            clear_agent_mode_flag(workspace_root)
        return flagged
    return "normal"


def mode_system_extra(agent_mode: AgentMode | str) -> str:
    return MODE_PROMPTS[normalize_agent_mode(str(agent_mode))]


def requires_plan_approval(agent_mode: AgentMode | str) -> bool:
    """Plan/Spec are design machines — Build/approve is required before act."""
    return normalize_agent_mode(str(agent_mode)) in {"plan", "spec"}


def is_informational_qa_prompt(text: str) -> bool:
    """True for lookup/status/Q&A prompts that misfit Goal autonomy.

    Reuses verifier lookup heuristics so Goal soft-redirect and verify agree.
    """
    from kageha.loop.verifier import is_lookup_status_text

    return is_lookup_status_text(text)


def goal_qa_misfit(agent_mode: AgentMode | str, text: str) -> bool:
    """Goal mode + informational prompt → soft-redirect to Normal (no Build gate)."""
    if normalize_agent_mode(str(agent_mode)) != "goal":
        return False
    return is_informational_qa_prompt(text)


def plan_already_approved(workspace_root: Path) -> bool:
    return (workspace_root / PLAN_APPROVED_FLAG).is_file()


def mark_plan_approved(workspace_root: Path) -> None:
    (workspace_root / PLAN_APPROVED_FLAG).write_text("approved\n", encoding="utf-8")


def clear_plan_approved(workspace_root: Path) -> None:
    path = workspace_root / PLAN_APPROVED_FLAG
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def mutations_blocked_until_approve(agent_mode: AgentMode | str) -> bool:
    """True while Plan/Spec design has not been approved for execute."""
    return requires_plan_approval(agent_mode)


def tool_blocked_in_plan_design(tool_name: str, *, approved: bool) -> bool:
    if approved:
        return False
    name = (tool_name or "").strip()
    if name in PLAN_DESIGN_BLOCKED_TOOLS:
        return True
    # Dynamic forge / skill writers
    if name.startswith("forged_") or name.startswith("skill_write"):
        return True
    return False


def render_plan_markdown(
    agent_mode: AgentMode | str,
    *,
    summary: str,
    steps: list[Any],
    task: str = "",
    tldr: str = "",
    explore_notes: str = "",
) -> str:
    mode = normalize_agent_mode(str(agent_mode))
    lines = [
        f"# Plan ({mode})",
        "",
    ]
    if task.strip():
        lines.extend([f"**Objective:** {task.strip()}", ""])
    brief = (tldr or summary or "").strip()
    if brief:
        # Codex-style short checkpoint before the step list.
        one_line = " ".join(brief.split())
        if len(one_line) > 280:
            one_line = one_line[:277] + "…"
        lines.extend([f"**TL;DR:** {one_line}", ""])
    lines.extend([summary.strip() or "(no summary)", ""])
    lines.append("## Steps")
    lines.append("")
    for step in steps:
        sid = getattr(step, "id", None) or (
            step.get("id") if isinstance(step, dict) else None
        ) or "?"
        desc = getattr(step, "description", None) or (
            step.get("description") if isinstance(step, dict) else None
        ) or ""
        lines.append(f"- [ ] `{sid}`: {desc}")
    if (explore_notes or "").strip():
        lines.extend(["", "## Explore notes", "", explore_notes.strip()[:2500]])
    lines.append("")
    lines.append(
        "_Approve / Build to execute. Until then this session stays read-only "
        "for project mutations._"
    )
    return "\n".join(lines) + "\n"


def write_plan_artifact(
    workspace_root: Path,
    agent_mode: AgentMode | str,
    *,
    summary: str,
    steps: list[Any],
    task: str = "",
    tldr: str = "",
    explore_notes: str = "",
) -> str:
    """Always materialize plan.md so the user can SEE the plan."""
    text = render_plan_markdown(
        agent_mode,
        summary=summary,
        steps=steps,
        task=task,
        tldr=tldr,
        explore_notes=explore_notes,
    )
    (workspace_root / "plan.md").write_text(text, encoding="utf-8")
    return text


def render_skill_gaps_markdown(
    *,
    task: str,
    steps: list[Any],
    matched: list[Any] | None = None,
    catalog_preview: str = "",
) -> str:
    """Build skill_gaps.md from real SkillRegistry matches (not a stub)."""
    lines = [
        "# Skill gaps",
        "",
        "## Objective",
        (task or "").strip() or "(unspecified)",
        "",
        "## Proposed matches",
    ]
    matched = list(matched or [])
    if matched:
        for skill in matched[:12]:
            name = getattr(skill, "name", None) or str(skill)
            desc = getattr(skill, "description", None) or ""
            desc = " ".join(str(desc).split())[:160]
            if desc:
                lines.append(f"- `{name}` — {desc}")
            else:
                lines.append(f"- `{name}`")
    else:
        lines.append("- (no catalog matches ranked for this objective)")
    lines.extend(["", "## Gaps to invent under HITL"])
    # Heuristic gaps from plan step verbs when catalog is thin.
    if not matched:
        lines.append(
            "- Capability may be missing for this objective — invent a skill "
            "or forge a tool under HITL after Build if execution stalls."
        )
    else:
        step_text = " ".join(
            str(
                getattr(s, "description", None)
                or (s.get("description") if isinstance(s, dict) else "")
                or ""
            )
            for s in steps
        ).lower()
        if any(
            k in step_text
            for k in ("browser", "scrape", "login", "oauth", "desktop", "gui")
        ) and not any(
            getattr(s, "name", "") in {"computer_use", "web_browse"} for s in matched
        ):
            lines.append(
                "- Interactive browser/desktop work may need `computer_use` or "
                "browser pack — confirm after Build."
            )
        else:
            lines.append(
                "- No critical gaps identified from catalog match; "
                "escalate with skill_manage/forge under HITL if a step fails."
            )
    if (catalog_preview or "").strip():
        lines.extend(
            [
                "",
                "## Catalog snapshot",
                "```",
                catalog_preview.strip()[:2000],
                "```",
            ]
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def write_spec_artifacts(
    workspace_root: Path,
    *,
    task: str,
    summary: str,
    steps: list[Any],
    milestones: list[str] | None = None,
    explore_notes: str = "",
    open_questions: list[str] | None = None,
    matched_skills: list[Any] | None = None,
    catalog_preview: str = "",
) -> list[str]:
    """Spec machine: requirements.md + plan.md + skill_gaps.md (phase-real)."""
    written: list[str] = []
    ms = [m for m in (milestones or []) if str(m).strip()]
    questions = [q for q in (open_questions or []) if str(q).strip()]
    if not questions:
        # Prefer clarify-phase skip label over a forever stub.
        from kageha.loop.spec_clarify import SKIP_CONTINUE_LABEL

        questions = [SKIP_CONTINUE_LABEL]
    req_lines = [
        "# Requirements",
        "",
        "## Phase",
        "requirements — Spec machine",
        "",
        "## Objective",
        (task or "").strip() or "(unspecified)",
        "",
        "## Acceptance criteria",
    ]
    if ms:
        req_lines.extend(f"- {m}" for m in ms)
    else:
        req_lines.append("- Deliverables match the objective and plan steps.")
    req_lines.extend(["", "## Open questions"])
    req_lines.extend(f"- {q}" for q in questions)
    if (explore_notes or "").strip():
        req_lines.extend(
            ["", "## Explore notes", "", explore_notes.strip()[:2000], ""]
        )
    else:
        req_lines.append("")
    (workspace_root / "requirements.md").write_text(
        "\n".join(req_lines), encoding="utf-8"
    )
    written.append("requirements.md")
    write_plan_artifact(
        workspace_root,
        "spec",
        summary=summary,
        steps=steps,
        task=task,
        tldr=summary,
        explore_notes=explore_notes,
    )
    written.append("plan.md")
    gaps = render_skill_gaps_markdown(
        task=task,
        steps=steps,
        matched=matched_skills,
        catalog_preview=catalog_preview,
    )
    (workspace_root / "skill_gaps.md").write_text(gaps, encoding="utf-8")
    written.append("skill_gaps.md")
    return written


def spec_artifacts_ready(workspace_root: Path) -> bool:
    return all((workspace_root / name).is_file() for name in SPEC_ARTIFACTS)


def missing_spec_artifacts(workspace_root: Path) -> list[str]:
    return [n for n in SPEC_ARTIFACTS if not (workspace_root / n).is_file()]


# Checklist lines written by render_plan_markdown / common edits.
_PLAN_STEP_RE = re.compile(
    r"^- \[[ xX]\]\s+(?:`([^`]+)`|([A-Za-z0-9_.-]+))\s*:\s*(.+)$"
)
_PLAN_TLDR_RE = re.compile(r"^\*\*TL;DR:\*\*\s*(.+)$", re.I)


def parse_plan_markdown_steps(text: str) -> list[tuple[str, str]]:
    """Parse ``- [ ] `id`: description`` steps from plan.md.

    Prefers the ``## Steps`` section when present; otherwise scans the whole doc.
    """
    lines = (text or "").splitlines()
    section: list[str] | None = None
    collecting = False
    for line in lines:
        stripped = line.strip()
        if re.match(r"^##\s+steps\s*$", stripped, flags=re.I):
            collecting = True
            section = []
            continue
        if collecting:
            if stripped.startswith("## "):
                break
            section.append(stripped)

    scan = section if section is not None else [ln.strip() for ln in lines]
    steps: list[tuple[str, str]] = []
    for stripped in scan:
        match = _PLAN_STEP_RE.match(stripped)
        if not match:
            continue
        sid = (match.group(1) or match.group(2) or "").strip()
        desc = (match.group(3) or "").strip()
        if sid and desc:
            steps.append((sid, desc))
    return steps


def parse_plan_markdown_tldr(text: str) -> str:
    for line in (text or "").splitlines():
        match = _PLAN_TLDR_RE.match(line.strip())
        if match:
            return match.group(1).strip()
    return ""


def apply_saved_plan_markdown(plan: Any, text: str) -> Any:
    """Refresh an in-memory TaskPlan from user-edited plan.md on disk.

    Keeps tool hints from matching prior step ids when possible; new ids get
    empty tool lists. Returns ``plan`` unchanged when no checklist steps parse.
    """
    from kageha.loop.planner import PlanStep, TaskPlan

    parsed = parse_plan_markdown_steps(text)
    if not parsed:
        return plan
    prior_tools: dict[str, list[str]] = {}
    for step in getattr(plan, "steps", None) or []:
        sid = str(getattr(step, "id", "") or "")
        if sid:
            prior_tools[sid] = list(getattr(step, "tools", None) or [])
    new_steps = [
        PlanStep(id=sid, description=desc, tools=list(prior_tools.get(sid) or []))
        for sid, desc in parsed
    ]
    tldr = parse_plan_markdown_tldr(text)
    summary = tldr or str(getattr(plan, "summary", "") or "")
    source = str(getattr(plan, "source", "") or "template")
    if not source.endswith("+disk"):
        source = f"{source}+disk"
    return TaskPlan(
        summary=summary or getattr(plan, "summary", "") or "",
        steps=new_steps,
        milestones=list(getattr(plan, "milestones", None) or []),
        source=source,
    )
