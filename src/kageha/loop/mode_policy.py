"""Agent mode policy: normal | plan | goal (trimmed harness).

Modes are different *machines* (artifacts + gates), not prompt variants.
One LoopController; mode selects design artifacts, approval, and execute rights.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

AgentMode = Literal["normal", "plan", "goal", "multitask"]

AGENT_MODES: frozenset[str] = frozenset({"normal", "plan", "goal", "multitask"})

AGENT_MODE_FLAG = "agent_mode.flag"
PLAN_APPROVED_FLAG = "plan_approved.flag"
CLARIFY_PENDING = "clarify_pending.json"

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

_MODE_CMD_RE = re.compile(r"^/(plan|goal|normal|multitask)\b", re.I)
_MODE_ONLY_RE = re.compile(r"^/?(plan|goal|normal|multitask)\s*$", re.I)

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
        "Escalate with escalate_plan(mode='plan'|'goal') or tell the user "
        "to send /plan or /goal for deeper work."
    ),
    "plan": (
        "## Agent mode: plan\n"
        "Clarify when ambiguous → read-only research → write plan.md → wait for "
        "Build. Mutating tools are HARD-DENIED until Build/Approve. User may edit "
        "plan.md or reply/Suggest to revise without executing. After Build, "
        f"execute and put outputs under `artifacts/`. {_ARTIFACTS_DEFAULT}"
    ),
    "goal": (
        "## Agent mode: goal\n"
        "Execute now toward a verifiable outcome — no Build gate. Keep a living "
        "goal card + todos; verify continuously. Ask only when blocked. Hard risk "
        "classes still need HITL (Approve/Deny/Suggest). Stop on SUCCESS/cancel.\n"
        f"{_ARTIFACTS_DEFAULT} Pure Q&A → answer briefly like Normal."
    ),
    "multitask": (
        "## Agent mode: multitask coordinator\n"
        "Keep this as one parent conversation. Decompose complex work into independent pieces "
        "and proactively delegate them with spawn_subagents or spawn_task_graph. Prefer one focused "
        "subagent per independent research, implementation, testing, or review stream. Run them in "
        "parallel when safe, wait for their results, then synthesize one answer for the parent chat. "
        "For a request containing two or more independent actions, delegation is mandatory: make "
        "spawn_subagents or spawn_task_graph your first tool call, rather than doing the work "
        "yourself or merely writing a plan. If the request is a single atomic action, execute it "
        "directly. "
        "Do not create parallel user chat tabs or ask the user to switch threads. "
        f"{_ARTIFACTS_DEFAULT}"
    ),
}

# WebUI / slash helper copy (keep in sync with static catalog fallbacks).
MODE_CHIP_DESCRIPTIONS: dict[str, str] = {
    "normal": "Normal mode — standard chat",
    "plan": "Plan — clarify, research, then Build",
    "goal": "Goal — execute now with HITL when needed",
    "multitask": "Multitask — coordinate parallel subagents in this chat",
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
    """Remove a leading ``/plan|/goal|/normal`` token.

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
            "Plan mode on — clarify → research → plan.md → Build.\n\n"
            "Send the **real objective** next (what to plan). "
            "Do not send just `/plan` or `plan`."
        )
    if mode == "goal":
        return (
            "Goal mode on — execute now toward a verifiable outcome.\n\n"
            "Send a ship/verify objective next. Risky actions still ask for "
            "Approve / Deny / Suggest. Pure questions fit Normal better."
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
    """Plan is a design machine — Build/approve is required before acting."""
    return normalize_agent_mode(str(agent_mode)) == "plan"


def is_plan_build_prompt(text: str) -> bool:
    """True for /build execute prompts (not plan-revise follow-ups)."""
    t = (text or "").strip().lower()
    return bool(t) and (
        t in {"build", "/build"} or t.startswith("execute the approved plan")
    )


_FILE_CUES = (".py", ".ts", ".tsx", ".js", ".go", ".rs", "src/", "tests/", "`", "file ")
_ACTION_CUES = (
    "create ", "write ", "add ", "implement ", "fix ", "refactor ",
    "build ", "make ", "ship ", "research ", "plan ", "design ",
    "generate ", "find ", "search ", "compare ", "analyze ", "summarize ",
    "book ", "schedule ", "organize ", "list ", "recommend ",
)
_CLEAR_TASK_CUES = (
    # Non-code tasks that are self-explanatory and don't need clarification.
    "trip", "travel", "itinerary", "vacation", "flight",
    "recipe", "meal", "dinner", "restaurant",
    "presentation", "slide", "deck", "report",
    "email", "letter", "message", "draft",
    "image", "logo", "poster", "banner", "video",
    "budget", "cost", "price", "estimate",
    "summary", "review", "article", "blog",
    "map", "route", "directions",
)
_AMBIGUOUS_CUES = (
    " or ", "either ", "maybe ", "best way", "how should", "what should",
    "which approach", "options", "architecture", "prefer ", "not sure",
    "whatever", " somehow",
)


def plan_needs_clarify(text: str) -> bool:
    """Pause when the objective would benefit from user input.

    The agent should ask questions when:
    - The task is creative/planning-heavy and user preferences matter
    - The objective is genuinely short/ambiguous

    Skip when:
    - Task mentions specific files/code targets (clear technical scope)
    - Task has explicit enough context (long with action verbs + specifics)
    - User said 'go' or similar
    """
    q = (text or "").strip().lower()
    # "go", "just do it", "proceed" = skip clarification
    if q in ("go", "just go", "proceed", "do it", "yes", "ok", "sure"):
        return False
    if len(q) < 12:
        return True
    has_file = any(m in q for m in _FILE_CUES)
    action = any(v in q for v in _ACTION_CUES)
    clear_deliverable = any(c in q for c in _CLEAR_TASK_CUES)
    # Code tasks with file targets don't need clarification
    if has_file and (action or len(q) >= 40):
        return False
    # Creative/planning tasks benefit from clarification (trip, event, etc.)
    # unless the user already gave enough detail (>100 chars with specifics)
    if clear_deliverable:
        # Already detailed enough — user gave preferences inline
        if len(q) >= 100:
            return False
        # Short creative task — ask questions
        return True
    # Action + enough detail = clear
    if action and len(q) >= 60:
        return False
    # Ambiguous language present
    if any(c in q for c in _AMBIGUOUS_CUES) and not has_file:
        return True
    # Short with no signals
    return not has_file and not action and len(q) < 60


def plan_clarify_question(objective: str) -> str:
    """Generate a contextual clarification question for the objective.

    Uses a lightweight heuristic to ask task-relevant questions rather than
    a one-size-fits-all template. The questions should be things the agent
    genuinely needs to know to produce a good deliverable.
    """
    obj = (objective or "").strip()
    low = obj.lower()

    # Detect task category and ask relevant questions
    if any(w in low for w in ("trip", "travel", "itinerary", "vacation", "flight")):
        return (
            f"Quick questions before I plan this:\n\n"
            f"**{obj[:400]}**\n\n"
            "1. How many people? (solo / couple / family / group)\n"
            "2. Interests? (food, nature, history, nightlife, art, adventure…)\n"
            "3. Budget range? (budget / mid-range / luxury)\n"
            "4. Any must-do's or places to avoid?\n\n"
            "Reply with whatever you know — I'll fill in reasonable defaults for the rest."
        )
    if any(w in low for w in ("presentation", "slide", "deck")):
        return (
            f"Before I create this:\n\n"
            f"**{obj[:400]}**\n\n"
            "1. Who's the audience?\n"
            "2. Roughly how many slides?\n"
            "3. Key message or takeaway?\n"
            "4. Any specific data/examples to include?\n\n"
            "Brief answers are fine — I'll handle the rest."
        )
    if any(w in low for w in ("report", "article", "blog", "summary")):
        return (
            f"Before I write this:\n\n"
            f"**{obj[:400]}**\n\n"
            "1. Target audience?\n"
            "2. Desired length?\n"
            "3. Tone — formal / casual / technical?\n"
            "4. Any specific sources or angles?\n\n"
            "Reply briefly or say 'go' for defaults."
        )
    if any(w in low for w in ("image", "logo", "poster", "banner", "design")):
        return (
            f"Before I create this:\n\n"
            f"**{obj[:400]}**\n\n"
            "1. Style/mood? (minimalist, playful, corporate, retro…)\n"
            "2. Dimensions / format?\n"
            "3. Brand colors or reference images?\n\n"
            "Brief answers are fine."
        )
    if any(w in low for w in ("event", "party", "wedding", "conference")):
        return (
            f"Before I plan this:\n\n"
            f"**{obj[:400]}**\n\n"
            "1. How many guests?\n"
            "2. Budget range?\n"
            "3. Venue preference? (indoor/outdoor/specific location)\n"
            "4. Any theme or must-haves?\n\n"
            "Reply with what you know."
        )
    # Default: software/general task
    return (
        f"Before I start:\n\n"
        f"**{obj[:400]}**\n\n"
        "Any constraints — scope boundaries, tech preferences, "
        "or must-have requirements?\n\n"
        "(Reply briefly, or say 'go' and I'll use reasonable defaults.)"
    )


def read_clarify_pending(workspace_root: Path) -> dict[str, str] | None:
    path = workspace_root / CLARIFY_PENDING
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return raw if isinstance(raw, dict) else None


def write_clarify_pending(
    workspace_root: Path, *, objective: str, question: str
) -> None:
    path = workspace_root / CLARIFY_PENDING
    path.write_text(
        json.dumps({"objective": objective, "question": question}, indent=2) + "\n",
        encoding="utf-8",
    )


def clear_clarify_pending(workspace_root: Path) -> None:
    try:
        (workspace_root / CLARIFY_PENDING).unlink(missing_ok=True)
    except OSError:
        pass


def fold_clarify_answer(objective: str, answer: str) -> str:
    base = (objective or "").strip()
    ans = (answer or "").strip()
    if not ans:
        return base
    return f"{base}\n\nClarification from user: {ans}".strip()


def plan_needs_more_info(objective: str, router: "Any", log: "Any" = None) -> bool:
    """Quick heuristic: does the accumulated objective still lack key details?

    Uses keyword density rather than an LLM call to stay fast.
    Returns True if the objective is still sparse after user answers.
    """
    text = (objective or "").lower()
    # If user explicitly said proceed, skip
    lines = text.split("\n")
    last_answer = ""
    for line in reversed(lines):
        if "clarification from user:" in line:
            last_answer = line.split("clarification from user:")[-1].strip()
            break
    if last_answer in ("go", "just go", "proceed", "ok", "sure", "defaults", "no preference"):
        return False
    # If user gave a substantive answer (>10 chars), they've clarified enough
    if len(last_answer) >= 10:
        return False
    # Count how much info we have (rough: number of meaningful tokens)
    words = len(text.split())
    # If accumulated text is substantial, we have enough
    if words >= 40:
        return False
    # Still very sparse — might need more
    return words < 20


async def generate_followup_question(
    objective: str, router: "Any", log: "Any" = None
) -> str | None:
    """Use the LLM to generate a contextual follow-up question.

    Returns None if the LLM decides it has enough info to proceed.
    """
    from kageha.models.base import ChatMessage as CM

    prompt = (
        "You are helping plan a task. Based on the conversation so far, "
        "decide: do you have enough information to proceed, or do you need "
        "one more clarifying question?\n\n"
        "Rules:\n"
        "- If you have enough info (or the user said 'go'/'proceed'/gave reasonable detail), "
        "respond with EXACTLY: READY\n"
        "- If you need one more question, respond with ONLY the question (1-3 sentences, "
        "friendly and specific). Do NOT ask about things the user already answered.\n"
        "- Never ask more than 2-3 questions total across a conversation.\n"
        "- Make reasonable assumptions for anything minor.\n\n"
        f"Conversation so far:\n{objective[:2000]}"
    )
    try:
        _, resp = await router.chat(
            [CM(role="user", content=prompt)],
            role="planning",
            max_tokens=200,
            effort="low",
        )
        answer = (resp.message.content or "").strip()
        if not answer or answer.upper().startswith("READY"):
            return None
        return answer
    except Exception as exc:  # noqa: BLE001
        if log:
            log(f"[kageha] followup question LLM failed: {exc}")
        return None


def is_plan_revise_turn(
    workspace_root: Path,
    agent_mode: AgentMode | str,
    text: str,
    *,
    auto_build: bool = False,
) -> bool:
    """Follow-up while plan.md awaits Build (not /build itself)."""
    return (
        requires_plan_approval(agent_mode)
        and not plan_already_approved(workspace_root)
        and (workspace_root / "plan.md").is_file()
        and not is_plan_build_prompt(text)
        and not auto_build
    )


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


def plan_skill_match_text(workspace_root: Path, task: str = "") -> str:
    """Skill-autoload query: prefer plan.md Objective/TL;DR when building.

    Bare ``Execute the approved plan.`` has no research/desktop cues, so
    matching against the saved objective avoids wrong skill routing on /build.
    """
    bits: list[str] = []
    plan_path = workspace_root / "plan.md"
    if plan_path.is_file():
        try:
            text = plan_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("**Objective:**"):
                bits.append(stripped[len("**Objective:**") :].strip())
            elif stripped.startswith("**TL;DR:**"):
                bits.append(stripped[len("**TL;DR:**") :].strip())
        if not bits and text.strip():
            bits.append(text.strip()[:1500])
    task_s = (task or "").strip()
    if task_s and task_s not in bits:
        bits.append(task_s)
    return "\n".join(b for b in bits if b).strip()


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
        # Short checkpoint before the step list.
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
        # Clean up any raw tool output noise from explore notes
        clean_notes = explore_notes.strip()
        # Remove raw tool output markers and URLs
        import re as _re
        clean_notes = _re.sub(
            r"\[(?:list_dir|read_file|web_search|bash)\]\s*", "", clean_notes
        )
        clean_notes = _re.sub(
            r"https?://vertexaisearch\.cloud\.google\.com/\S+", "", clean_notes
        )
        clean_notes = clean_notes.strip()
        if clean_notes and len(clean_notes) > 20:
            lines.extend(["", "## Research Notes", "", clean_notes[:2500]])
    lines.append("")
    lines.append(
        "_Approve / Build to execute, or reply with changes / Suggest to revise. "
        "Until then this session stays read-only for project mutations._"
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
