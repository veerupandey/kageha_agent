"""Task planner with LLM plan + objective-derived fallback.

Complexity-aware: dispatches to different planning strategies based on TaskProfile.
- Simple: zero-step or one-step plan, no LLM call.
- Moderate: focused 2-3 step plan, optional lightweight LLM call.
- Complex: full LLM plan with explore notes, up to 8 steps, DAG support.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from kageha.models.base import ChatMessage
from kageha.models.effort import TaskProfile, profile_task
from kageha.models.router import ModelRouter


@dataclass
class PlanStep:
    id: str
    description: str
    tools: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    parallel_group: str = ""
    estimated_steps: int = 0
    estimated_usd: float = 0.0


@dataclass
class TaskPlan:
    summary: str
    steps: list[PlanStep] = field(default_factory=list)
    milestones: list[str] = field(default_factory=list)
    source: str = "template"


TEMPLATE_STEPS = [
    PlanStep("p1", "Research and gather context for the task", ["list_dir", "read_file", "bash", "web_search"]),
    PlanStep("p2", "Execute the primary work and produce deliverables", ["bash", "write_file", "edit_file"]),
    PlanStep("p3", "Verify the result and summarize in chat", ["read_file", "todo_write"]),
]


def make_followup_plan(task: str) -> TaskPlan:
    """One-step plan for chat follow-ups — no LLM planner round-trip."""
    text = (task or "").strip() or "Complete the follow-up"
    return TaskPlan(
        summary=f"Follow-up: {text[:140]}",
        steps=[
            PlanStep(
                "act",
                (
                    f"Do exactly this follow-up with the minimum tools, answer in "
                    f"chat (no .md file unless the user asked to save one), then "
                    f"stop: {text}"
                ),
                [],
            )
        ],
        milestones=["Follow-up answered in chat"],
        source="followup",
    )


def make_simple_plan(task: str, profile: TaskProfile | None = None) -> TaskPlan:
    """Zero-overhead plan for simple Q&A or single-action tasks.

    No LLM round-trip. The model will answer directly or use one tool.
    """
    text = (task or "").strip() or "Answer the question"
    p = profile or profile_task(text)

    if p.is_qa:
        return TaskPlan(
            summary=f"Direct answer: {text[:120]}",
            steps=[
                PlanStep(
                    "answer",
                    (
                        f"Answer this directly in chat. Use a tool only if you "
                        f"need to look something up first. Do NOT create files, "
                        f"plans, or todo lists: {text}"
                    ),
                    [],
                )
            ],
            milestones=["Question answered in chat"],
            source="simple_qa",
        )

    # Single action (open, run, check, read, etc.)
    return TaskPlan(
        summary=f"Quick action: {text[:120]}",
        steps=[
            PlanStep(
                "do",
                (
                    f"Execute this single action with the minimum tools needed, "
                    f"then report the result in chat: {text}"
                ),
                [],
            )
        ],
        milestones=["Action completed"],
        source="simple_action",
    )


def make_moderate_plan(
    task: str,
    allowed_tools: set[str],
    profile: TaskProfile | None = None,
) -> TaskPlan:
    """Focused 2-3 step plan for moderate complexity tasks.

    No LLM round-trip needed — uses task structure to generate a tight plan.
    Covers: bug fixes, single-feature additions, script writing, focused edits.
    """
    text = (task or "").strip() or "Complete the requested work"
    p = profile or profile_task(text)
    clip = text if len(text) <= 200 else text[:197].rstrip() + "…"

    tools_read = [
        name for name in ("read_file", "list_dir", "bash", "web_search")
        if name in allowed_tools
    ]
    tools_impl = [
        name for name in ("write_file", "edit_file", "bash", "read_file")
        if name in allowed_tools
    ]
    tools_verify = [
        name for name in ("bash", "read_file")
        if name in allowed_tools
    ]

    steps: list[PlanStep] = []

    # Step 1: Read/understand (always needed for code tasks).
    steps.append(PlanStep(
        "understand",
        (
            f"Read the relevant code/context needed to accomplish: {clip}. "
            "Identify the exact files and locations to change."
        ),
        tools_read,
    ))

    # Step 2: Implement.
    steps.append(PlanStep(
        "implement",
        f"Make the changes to accomplish: {clip}",
        tools_impl,
    ))

    # Step 3: Verify (if tests are required or task warrants it).
    if p.requires_tests:
        steps.append(PlanStep(
            "verify",
            "Run tests (pytest or equivalent) and fix any failures until green",
            tools_verify,
        ))
    else:
        steps.append(PlanStep(
            "verify",
            "Quickly verify the change works (read the result or run a command)",
            tools_verify,
        ))

    milestones = ["Change implemented correctly"]
    if p.requires_tests:
        milestones.append("Tests pass")

    return TaskPlan(
        summary=clip,
        steps=steps,
        milestones=milestones,
        source="moderate",
    )


def _objective_fallback_plan(task: str, allowed_tools: set[str]) -> TaskPlan:
    """Better than a generic template when the planner LLM fails.

    Embeds the actual task into each step description so the executor
    knows what it's supposed to do (not just 'execute the core work').
    """
    text = (task or "").strip() or "Complete the requested work"
    clip = text if len(text) <= 240 else text[:237].rstrip() + "…"
    tools_research = [
        name
        for name in ("web_search", "read_file", "list_dir", "bash", "memory_recall")
        if name in allowed_tools
    ]
    tools_impl = [
        name
        for name in ("write_file", "edit_file", "bash", "web_search", "todo_write")
        if name in allowed_tools
    ]
    tools_verify = [
        name for name in ("bash", "read_file", "todo_write") if name in allowed_tools
    ]
    return TaskPlan(
        summary=clip,
        steps=[
            PlanStep(
                "p1",
                f"Research: gather the information needed for — {clip}",
                tools_research,
            ),
            PlanStep(
                "p2",
                f"Execute: produce the deliverable(s) for — {clip}",
                tools_impl,
            ),
            PlanStep(
                "p3",
                "Verify: run tests/verification and confirm deliverables exist, summarize in chat",
                tools_verify,
            ),
        ],
        milestones=[
            f"Deliverable produced for: {clip[:100]}",
            "Verification passed",
        ],
        source="template",
    )


def _loads_json_lenient(blob: str) -> object:
    """Parse JSON, repairing common LLM trailing-comma mistakes."""
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        repaired = re.sub(r",\s*([}\]])", r"\1", blob)
        if repaired == blob:
            raise
        return json.loads(repaired)


def _parse_plan_json(
    text: str, *, task: str, allowed_tools: set[str]
) -> TaskPlan:
    match = re.search(r"\{.*\}", text or "", flags=re.S)
    if not match:
        raise ValueError("no json")
    data = _loads_json_lenient(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("plan json must be an object")
    steps = [
        PlanStep(
            id=s.get("id") or f"s{i}",
            description=s.get("description") or "",
            tools=[
                str(name)
                for name in (s.get("tools") or [])
                if str(name) in allowed_tools
            ],
            depends_on=[str(d) for d in (s.get("depends_on") or [])],
            parallel_group=str(s.get("parallel_group") or ""),
            estimated_steps=int(s.get("estimated_steps") or 0),
            estimated_usd=float(s.get("estimated_usd") or 0.0),
        )
        for i, s in enumerate(data.get("steps") or [])
    ]
    steps = [s for s in steps if (s.description or "").strip()]
    if not steps:
        raise ValueError("empty steps")
    # Deduplicate ids while preserving order.
    seen: set[str] = set()
    unique: list[PlanStep] = []
    for step in steps[:8]:
        sid = step.id or f"s{len(unique)+1}"
        if sid in seen:
            sid = f"{sid}_{len(unique)+1}"
        seen.add(sid)
        unique.append(
            PlanStep(
                id=sid,
                description=step.description,
                tools=step.tools,
                depends_on=step.depends_on,
                parallel_group=step.parallel_group,
                estimated_steps=step.estimated_steps,
                estimated_usd=step.estimated_usd,
            )
        )
    return TaskPlan(
        summary=data.get("summary") or task,
        steps=unique,
        milestones=[
            str(item)
            for item in (data.get("milestones") or [])
            if str(item).strip()
        ][:12],
        source="llm",
    )


async def make_plan(
    task: str,
    router: ModelRouter,
    *,
    role: str = "planning",
    available_tools: set[str] | None = None,
    effort: str | None = None,
    explore_notes: str = "",
) -> TaskPlan:
    allowed_tools = set(available_tools or set())
    tool_names = sorted(allowed_tools)
    tool_contract = (
        "\nAvailable tools (steps may reference ONLY these exact names):\n"
        + ", ".join(tool_names)
        if tool_names
        else "\nNo tool catalog was supplied; leave each step's tools list empty."
    )
    dag_hint = ""
    if "spawn_task_graph" in allowed_tools:
        dag_hint = (
            " When work can fan out, prefer a step that calls spawn_task_graph with "
            "a dependency graph (nodes with id/task/depends_on) instead of serial steps."
        )
    explore_block = ""
    if (explore_notes or "").strip():
        explore_block = (
            "\n\nExplore notes from read-only research (use these; do not invent "
            "files that contradict them):\n"
            + explore_notes.strip()[:4000]
        )
    prompt = (
        "Create a short JSON plan for this task. Schema:\n"
        '{"summary": str, "milestones": [str], "steps": [{"id": str, "description": str, "tools": [str]}]}\n'
        "Max 8 steps. Make milestones independently verifiable and include every explicitly "
        "requested deliverable. When the task produces files (pptx/pdf/html/images), "
        "plan steps that write them under artifacts/ (e.g. artifacts/deck.pptx). "
        "For simple Q&A, plan a chat answer — do not invent "
        ".md file deliverables the user did not ask for. Never invent tools or "
        "capability names.\n"
        "Guidelines for complex tasks:\n"
        "- Start with a step to read/explore relevant existing code (conventions, patterns, imports).\n"
        "- Group related changes together (don't make one step per file).\n"
        "- Include a testing/verification step if the task involves code changes.\n"
        "- If tests are explicitly requested, include a dedicated pytest step.\n"
        "- Use parallel_group when steps are independent of each other.\n"
        "Return ONLY JSON.\n"
        f"{tool_contract}.{dag_hint}"
        f"{explore_block}\n\n"
        f"Task: {task}"
    )
    raw_text = ""
    parse_error = ""
    try:
        _, resp = await router.chat(
            [ChatMessage(role="user", content=prompt)],
            role=role,
            max_tokens=1024,
            effort=effort or "medium",
        )
        raw_text = resp.message.content or ""
        return _parse_plan_json(raw_text, task=task, allowed_tools=allowed_tools)
    except Exception as first_exc:  # noqa: BLE001
        parse_error = str(first_exc)
        # One repair pass for malformed JSON / truncated planner output.
        if raw_text.strip():
            try:
                repair = (
                    "Repair the following into ONLY valid JSON matching schema "
                    '{"summary": str, "milestones": [str], '
                    '"steps": [{"id": str, "description": str, "tools": [str]}]}. '
                    f"Parse error: {parse_error[:200]}\n\nBroken output:\n"
                    f"{raw_text[:3000]}"
                )
                _, repaired = await router.chat(
                    [ChatMessage(role="user", content=repair)],
                    role=role,
                    max_tokens=1024,
                    effort="low",
                )
                return _parse_plan_json(
                    repaired.message.content or "",
                    task=task,
                    allowed_tools=allowed_tools,
                )
            except Exception:  # noqa: BLE001
                pass
        return _objective_fallback_plan(task, allowed_tools)


def replace_plan(existing: TaskPlan | None, new_plan: TaskPlan, *, version: int = 1) -> TaskPlan:
    """Extension point for in-session plan replacement (versioned archive later).

    Today this returns ``new_plan`` tagged with a versioned source string so callers
    can persist it over plan.json without nesting resume wrappers. Full archival
    (plan.vN.json) can hook here later.
    """
    _ = existing  # prior plan reserved for future archive
    tagged = TaskPlan(
        summary=new_plan.summary,
        steps=list(new_plan.steps),
        milestones=list(new_plan.milestones),
        source=f"{new_plan.source}|v{max(1, int(version))}",
    )
    return tagged


async def make_adaptive_plan(
    task: str,
    router: ModelRouter,
    *,
    role: str = "planning",
    available_tools: set[str] | None = None,
    effort: str | None = None,
    explore_notes: str = "",
    profile: TaskProfile | None = None,
) -> TaskPlan:
    """Complexity-aware planning entry point.

    Dispatches to the appropriate planning strategy:
    - Simple (low effort, Q&A, single action): instant plan, no LLM.
    - Moderate (medium effort, focused work): deterministic 2-3 step plan, no LLM.
    - Complex (high effort, multi-file/deliverable): full LLM plan with explore notes.

    The controller should prefer this over make_plan() directly for new turns.
    """
    text = (task or "").strip()
    p = profile or profile_task(text)
    allowed = set(available_tools or set())

    # Effort override wins if explicitly set by the controller.
    effective_effort = effort or p.effort

    if effective_effort == "low" or p.skip_planning:
        return make_simple_plan(text, profile=p)

    if effective_effort == "medium" and not p.is_multi_file and not p.is_multi_deliverable:
        return make_moderate_plan(text, allowed, profile=p)

    # High effort → full LLM plan.
    return await make_plan(
        text,
        router,
        role=role,
        available_tools=allowed,
        effort=effective_effort,
        explore_notes=explore_notes,
    )
