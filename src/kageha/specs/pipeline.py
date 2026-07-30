"""Spec pipeline — generates requirements, design, and tasks via LLM."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kageha.models.base import ChatMessage
from kageha.models.router import ModelRouter
from kageha.specs.models import (
    SpecStage,
    SpecState,
    SpecTask,
    load_spec_state,
    save_spec_state,
    spec_dir,
)


REQUIREMENTS_PROMPT = """\
You are a senior requirements engineer. Given the user's feature description,
produce a structured requirements document in Markdown.

Include these sections:
## Overview
Brief description of the feature and its purpose.

## User Stories
- As a [role], I want [capability] so that [benefit]

## Functional Requirements
Numbered list (FR-1, FR-2, ...) of specific functional requirements.

## Non-Functional Requirements
Performance, security, scalability, accessibility constraints.

## Acceptance Criteria
Testable conditions that must be true when the feature is complete.

## Out of Scope
What this feature explicitly does NOT include.

## Open Questions
Unresolved questions that need clarification before design.

Be specific and testable. Avoid vague language. Each requirement should be
independently verifiable.

User's feature description:
{prompt}
"""

DESIGN_PROMPT = """\
You are a senior software architect. Given the requirements document below,
produce a technical design document in Markdown.

Include these sections:
## Architecture Overview
High-level description of how the feature fits into the existing system.

## Component Design
For each component/module:
- Responsibility
- Public interface (key functions/classes)
- Dependencies

## Data Model
Any new data structures, database changes, or state management.

## API Design
New or modified API endpoints/interfaces (if applicable).

## Integration Points
How this connects to existing code. Which files/modules are affected.

## Error Handling
How errors propagate and are handled.

## Testing Strategy
Unit test approach, integration test approach, edge cases to cover.

## Migration/Deployment
Any migration steps, feature flags, or rollout considerations.

Requirements document:
{requirements}

Original feature prompt:
{prompt}
"""

TASKS_PROMPT = """\
You are a senior engineering manager breaking down a design into implementation tasks.

Given the design document and requirements below, produce a sequenced task list.
Return ONLY a JSON array where each element has:
{{
  "id": "task-N",
  "title": "Short title",
  "description": "Detailed description of what to implement",
  "acceptance_criteria": ["Criterion 1", "Criterion 2"],
  "depends_on": ["task-M"],
  "estimated_complexity": "low|medium|high",
  "files_to_modify": ["path/to/file.py"]
}}

Rules:
- Tasks should be small enough for a single focused agent (1-3 files each)
- Order by dependency (task-1 has no deps, task-2 may depend on task-1)
- Include a final task for integration testing
- Be specific about files to create or modify
- Each task should be independently testable

Design document:
{design}

Requirements document:
{requirements}

Original feature prompt:
{prompt}
"""

VERIFICATION_PROMPT = """\
You are a QA engineer. Given the requirements and design, produce a verification
plan in Markdown.

Include:
## Test Strategy
Overall approach to verifying this feature works correctly.

## Unit Tests
Specific test cases for individual components.

## Integration Tests
Tests for component interactions.

## Property-Based Tests
Properties that should hold for all inputs (use Hypothesis-style thinking).

## Edge Cases
Boundary conditions and error scenarios to test.

## Acceptance Test Script
Step-by-step manual or automated verification procedure.

Requirements:
{requirements}

Design:
{design}

Tasks:
{tasks_summary}
"""


async def generate_requirements(
    prompt: str,
    *,
    router: ModelRouter,
    project_root: Path,
    spec_name: str,
    task_id: str = "",
) -> SpecState:
    """Generate requirements from a feature prompt."""
    now = datetime.now(timezone.utc).isoformat()

    # Check for existing spec
    state = load_spec_state(project_root, spec_name)
    if state is None:
        state = SpecState(name=spec_name, prompt=prompt, created_at=now)
    state.updated_at = now

    filled_prompt = REQUIREMENTS_PROMPT.format(prompt=prompt)
    _, resp = await router.chat(
        [ChatMessage(role="user", content=filled_prompt)],
        role="planning",
        max_tokens=4000,
        task_id=task_id,
    )
    requirements_md = resp.message.content or ""

    # Write artifact
    directory = spec_dir(project_root, spec_name)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "requirements.md").write_text(requirements_md, encoding="utf-8")

    state.current_stage = SpecStage.REQUIREMENTS
    save_spec_state(project_root, state)
    return state


async def generate_design(
    *,
    router: ModelRouter,
    project_root: Path,
    spec_name: str,
    codebase_context: str = "",
    task_id: str = "",
) -> SpecState:
    """Generate design from approved requirements."""
    state = load_spec_state(project_root, spec_name)
    if state is None:
        raise ValueError(f"No spec found: {spec_name}")

    directory = spec_dir(project_root, spec_name)
    req_path = directory / "requirements.md"
    if not req_path.is_file():
        raise FileNotFoundError(f"requirements.md not found for spec: {spec_name}")

    requirements = req_path.read_text(encoding="utf-8")
    filled_prompt = DESIGN_PROMPT.format(
        requirements=requirements,
        prompt=state.prompt,
    )
    if codebase_context:
        filled_prompt += f"\n\nExisting codebase context:\n{codebase_context[:8000]}"

    _, resp = await router.chat(
        [ChatMessage(role="user", content=filled_prompt)],
        role="planning",
        max_tokens=6000,
        task_id=task_id,
    )
    design_md = resp.message.content or ""

    (directory / "design.md").write_text(design_md, encoding="utf-8")

    state.current_stage = SpecStage.DESIGN
    state.updated_at = datetime.now(timezone.utc).isoformat()
    save_spec_state(project_root, state)
    return state


async def generate_tasks(
    *,
    router: ModelRouter,
    project_root: Path,
    spec_name: str,
    task_id: str = "",
) -> SpecState:
    """Generate implementation tasks from approved design."""
    state = load_spec_state(project_root, spec_name)
    if state is None:
        raise ValueError(f"No spec found: {spec_name}")

    directory = spec_dir(project_root, spec_name)
    req_path = directory / "requirements.md"
    design_path = directory / "design.md"
    if not design_path.is_file():
        raise FileNotFoundError(f"design.md not found for spec: {spec_name}")

    requirements = req_path.read_text(encoding="utf-8") if req_path.is_file() else ""
    design = design_path.read_text(encoding="utf-8")

    filled_prompt = TASKS_PROMPT.format(
        design=design,
        requirements=requirements,
        prompt=state.prompt,
    )

    _, resp = await router.chat(
        [ChatMessage(role="user", content=filled_prompt)],
        role="planning",
        max_tokens=6000,
        task_id=task_id,
    )
    text = resp.message.content or ""

    # Parse JSON task list
    match = re.search(r"\[.*\]", text, flags=re.S)
    tasks: list[SpecTask] = []
    if match:
        try:
            raw_tasks = json.loads(match.group(0))
            for t in raw_tasks:
                tasks.append(
                    SpecTask(
                        id=t.get("id", f"task-{len(tasks)+1}"),
                        title=t.get("title", ""),
                        description=t.get("description", ""),
                        acceptance_criteria=t.get("acceptance_criteria", []),
                        depends_on=t.get("depends_on", []),
                        estimated_complexity=t.get("estimated_complexity", "medium"),
                        files_to_modify=t.get("files_to_modify", []),
                    )
                )
        except (json.JSONDecodeError, TypeError):
            pass

    # Write tasks.md summary
    tasks_md_lines = ["# Implementation Tasks\n"]
    for t in tasks:
        deps = f" (depends: {', '.join(t.depends_on)})" if t.depends_on else ""
        tasks_md_lines.append(f"## {t.id}: {t.title}{deps}\n")
        tasks_md_lines.append(f"**Complexity:** {t.estimated_complexity}\n")
        tasks_md_lines.append(f"{t.description}\n")
        if t.acceptance_criteria:
            tasks_md_lines.append("**Acceptance Criteria:**")
            for ac in t.acceptance_criteria:
                tasks_md_lines.append(f"- {ac}")
            tasks_md_lines.append("")
        if t.files_to_modify:
            tasks_md_lines.append(f"**Files:** {', '.join(t.files_to_modify)}\n")
        tasks_md_lines.append("")

    (directory / "tasks.md").write_text("\n".join(tasks_md_lines), encoding="utf-8")

    state.tasks = tasks
    state.current_stage = SpecStage.TASKS
    state.updated_at = datetime.now(timezone.utc).isoformat()
    save_spec_state(project_root, state)
    return state


async def generate_verification(
    *,
    router: ModelRouter,
    project_root: Path,
    spec_name: str,
    task_id: str = "",
) -> Path:
    """Generate verification plan from requirements, design, and tasks."""
    directory = spec_dir(project_root, spec_name)
    req_path = directory / "requirements.md"
    design_path = directory / "design.md"
    tasks_path = directory / "tasks.md"

    requirements = req_path.read_text(encoding="utf-8") if req_path.is_file() else ""
    design = design_path.read_text(encoding="utf-8") if design_path.is_file() else ""
    tasks_summary = tasks_path.read_text(encoding="utf-8") if tasks_path.is_file() else ""

    filled_prompt = VERIFICATION_PROMPT.format(
        requirements=requirements,
        design=design,
        tasks_summary=tasks_summary[:4000],
    )

    _, resp = await router.chat(
        [ChatMessage(role="user", content=filled_prompt)],
        role="planning",
        max_tokens=4000,
        task_id=task_id,
    )
    verification_md = resp.message.content or ""

    out_path = directory / "verification.md"
    out_path.write_text(verification_md, encoding="utf-8")
    return out_path


def approve_gate(project_root: Path, spec_name: str, notes: str = "") -> SpecState:
    """Approve the current stage gate and advance to next stage."""
    state = load_spec_state(project_root, spec_name)
    if state is None:
        raise ValueError(f"No spec found: {spec_name}")

    gate = state.gate_for(state.current_stage)
    gate.approve(notes)
    gate.timestamp = datetime.now(timezone.utc).isoformat()
    state.advance()
    state.updated_at = datetime.now(timezone.utc).isoformat()
    save_spec_state(project_root, state)
    return state


def reject_gate(
    project_root: Path, spec_name: str, notes: str = ""
) -> SpecState:
    """Reject the current stage gate with revision notes."""
    state = load_spec_state(project_root, spec_name)
    if state is None:
        raise ValueError(f"No spec found: {spec_name}")

    gate = state.gate_for(state.current_stage)
    gate.request_revision(notes)
    gate.timestamp = datetime.now(timezone.utc).isoformat()
    state.updated_at = datetime.now(timezone.utc).isoformat()
    save_spec_state(project_root, state)
    return state


def list_specs(project_root: Path) -> list[dict[str, Any]]:
    """List all specs in the project."""
    specs_root = project_root / ".kageha" / "specs"
    if not specs_root.is_dir():
        return []
    results = []
    for child in sorted(specs_root.iterdir()):
        if child.is_dir() and (child / "state.json").is_file():
            state = load_spec_state(project_root, child.name)
            if state:
                results.append({
                    "name": state.name,
                    "stage": state.current_stage.value,
                    "prompt": state.prompt[:100],
                    "tasks_count": len(state.tasks),
                    "updated_at": state.updated_at,
                })
    return results
