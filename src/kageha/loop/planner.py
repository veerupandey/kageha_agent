"""Task planner with LLM plan + deterministic template fallback."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from kageha.models.base import ChatMessage
from kageha.models.router import ModelRouter


@dataclass
class PlanStep:
    id: str
    description: str
    tools: list[str] = field(default_factory=list)


@dataclass
class TaskPlan:
    summary: str
    steps: list[PlanStep] = field(default_factory=list)
    milestones: list[str] = field(default_factory=list)
    source: str = "template"


TEMPLATE_STEPS = [
    PlanStep("p1", "Inspect the workspace and gather inputs", ["list_dir", "read_file", "bash"]),
    PlanStep("p2", "Execute the core work toward the deliverable", ["bash", "write_file", "edit_file"]),
    PlanStep("p3", "Verify the result and write a short summary", ["read_file", "todo_write"]),
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
        "capability names. Return ONLY JSON.\n"
        f"{tool_contract}.{dag_hint}"
        f"{explore_block}\n\n"
        f"Task: {task}"
    )
    try:
        _, resp = await router.chat(
            [ChatMessage(role="user", content=prompt)],
            role=role,
            max_tokens=1024,
            effort=effort or "medium",
        )
        text = resp.message.content or ""
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise ValueError("no json")
        data = json.loads(match.group(0))
        steps = [
            PlanStep(
                id=s.get("id") or f"s{i}",
                description=s.get("description") or "",
                tools=[
                    str(name)
                    for name in (s.get("tools") or [])
                    if str(name) in allowed_tools
                ],
            )
            for i, s in enumerate(data.get("steps") or [])
        ]
        if not steps:
            raise ValueError("empty steps")
        return TaskPlan(
            summary=data.get("summary") or task,
            steps=steps,
            milestones=[
                str(item)
                for item in (data.get("milestones") or [])
                if str(item).strip()
            ][:12],
            source="llm",
        )
    except Exception:  # noqa: BLE001
        fallback_steps = [
            PlanStep(
                step.id,
                step.description,
                [name for name in step.tools if name in allowed_tools],
            )
            for step in TEMPLATE_STEPS
        ]
        return TaskPlan(
            summary=task,
            steps=fallback_steps,
            milestones=[
                "Understood the task and constraints",
                "Produced the primary deliverable",
                "Verified the deliverable against the request",
            ],
            source="template",
        )


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
