"""Spec pipeline tools — exposed to the agent as callable tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def register_spec_tools(ctx: Any) -> Any:
    """Register spec-driven development tools with the harness context."""
    from kageha.harness.tools.base import ToolRegistry, tool

    registry = ToolRegistry()
    project_root: Path = getattr(ctx, "project_root", None) or Path.cwd()

    @tool(
        name="spec_new",
        description=(
            "Start a new spec-driven development pipeline. Creates a structured "
            "requirements document from a feature description. The spec lives in "
            ".kageha/specs/<name>/ and progresses through stages: "
            "requirements → design → tasks → build."
        ),
        parameters={
            "name": {"type": "string", "description": "Short name for the feature (kebab-case)"},
            "prompt": {"type": "string", "description": "Feature description / user intent"},
        },
        required=["name", "prompt"],
    )
    async def spec_new(name: str, prompt: str) -> str:
        from kageha.specs.pipeline import generate_requirements
        from kageha.specs.models import spec_dir

        router = getattr(ctx, "router", None)
        if router is None:
            return "Error: no model router available"

        state = await generate_requirements(
            prompt, router=router, project_root=project_root, spec_name=name
        )
        directory = spec_dir(project_root, name)
        return (
            f"Spec '{name}' created at {directory}/\n"
            f"Stage: {state.current_stage.value}\n"
            f"Generated: requirements.md\n\n"
            f"Next: Review requirements.md, then call spec_approve(name='{name}') "
            f"to advance to the design stage, or spec_revise(name='{name}', notes='...') "
            f"to request changes."
        )

    @tool(
        name="spec_design",
        description=(
            "Generate a technical design document from approved requirements. "
            "Requires the requirements stage to be approved first."
        ),
        parameters={
            "name": {"type": "string", "description": "Spec name"},
            "codebase_context": {
                "type": "string",
                "description": "Optional: relevant existing code context to inform the design",
                "default": "",
            },
        },
        required=["name"],
    )
    async def spec_design(name: str, codebase_context: str = "") -> str:
        from kageha.specs.pipeline import generate_design
        from kageha.specs.models import spec_dir, load_spec_state, SpecStage

        router = getattr(ctx, "router", None)
        if router is None:
            return "Error: no model router available"

        state = load_spec_state(project_root, name)
        if state is None:
            return f"Error: no spec found with name '{name}'"
        if state.current_stage.value == SpecStage.REQUIREMENTS.value:
            gate = state.gate_for(SpecStage.REQUIREMENTS)
            if gate.status.value != "approved":
                return (
                    f"Error: requirements stage not approved yet. "
                    f"Call spec_approve(name='{name}') first."
                )

        state = await generate_design(
            router=router,
            project_root=project_root,
            spec_name=name,
            codebase_context=codebase_context,
        )
        directory = spec_dir(project_root, name)
        return (
            f"Design generated at {directory}/design.md\n"
            f"Stage: {state.current_stage.value}\n\n"
            f"Next: Review design.md, then call spec_approve(name='{name}') "
            f"to advance to tasks, or spec_revise(name='{name}', notes='...') "
            f"to request changes."
        )

    @tool(
        name="spec_tasks",
        description=(
            "Generate a sequenced implementation task list from approved design. "
            "Tasks are dependency-aware and ready for parallel execution."
        ),
        parameters={
            "name": {"type": "string", "description": "Spec name"},
        },
        required=["name"],
    )
    async def spec_tasks(name: str) -> str:
        from kageha.specs.pipeline import generate_tasks
        from kageha.specs.models import spec_dir, load_spec_state, SpecStage

        router = getattr(ctx, "router", None)
        if router is None:
            return "Error: no model router available"

        state = load_spec_state(project_root, name)
        if state is None:
            return f"Error: no spec found with name '{name}'"
        if state.current_stage.value == SpecStage.DESIGN.value:
            gate = state.gate_for(SpecStage.DESIGN)
            if gate.status.value != "approved":
                return (
                    f"Error: design stage not approved yet. "
                    f"Call spec_approve(name='{name}') first."
                )

        state = await generate_tasks(
            router=router, project_root=project_root, spec_name=name
        )
        directory = spec_dir(project_root, name)
        task_summary = "\n".join(
            f"  - {t.id}: {t.title} [{t.estimated_complexity}]"
            for t in state.tasks
        )
        return (
            f"Tasks generated at {directory}/tasks.md\n"
            f"Stage: {state.current_stage.value}\n"
            f"Tasks ({len(state.tasks)}):\n{task_summary}\n\n"
            f"Next: Review tasks.md, then call spec_approve(name='{name}') "
            f"to advance to build, or spec_revise(name='{name}', notes='...') "
            f"to request changes."
        )

    @tool(
        name="spec_build",
        description=(
            "Execute the approved task list as parallel subagents via the task graph. "
            "Each task runs in an isolated worktree. Requires tasks stage to be approved."
        ),
        parameters={
            "name": {"type": "string", "description": "Spec name"},
        },
        required=["name"],
    )
    async def spec_build(name: str) -> str:
        from kageha.specs.models import load_spec_state, SpecStage, save_spec_state
        from kageha.specs.pipeline import generate_verification

        state = load_spec_state(project_root, name)
        if state is None:
            return f"Error: no spec found with name '{name}'"
        if state.current_stage.value == SpecStage.TASKS.value:
            gate = state.gate_for(SpecStage.TASKS)
            if gate.status.value != "approved":
                return (
                    f"Error: tasks stage not approved yet. "
                    f"Call spec_approve(name='{name}') first."
                )

        if not state.tasks:
            return "Error: no tasks defined. Run spec_tasks first."

        # Generate verification plan
        router = getattr(ctx, "router", None)
        if router:
            await generate_verification(
                router=router, project_root=project_root, spec_name=name
            )

        # Build task graph nodes for subagent execution
        nodes = []
        for task in state.tasks:
            nodes.append({
                "id": task.id,
                "task": (
                    f"Implement: {task.title}\n\n"
                    f"{task.description}\n\n"
                    f"Acceptance criteria:\n"
                    + "\n".join(f"- {ac}" for ac in task.acceptance_criteria)
                    + (f"\n\nFiles to modify: {', '.join(task.files_to_modify)}"
                       if task.files_to_modify else "")
                ),
                "depends_on": task.depends_on,
            })

        state.current_stage = SpecStage.BUILD
        save_spec_state(project_root, state)

        # Return the task graph for spawn_task_graph
        return (
            f"Build started for spec '{name}' with {len(nodes)} tasks.\n"
            f"Verification plan generated at verification.md\n\n"
            f"Task graph nodes (use spawn_task_graph to execute):\n"
            f"```json\n{json.dumps(nodes, indent=2)}\n```"
        )

    @tool(
        name="spec_approve",
        description="Approve the current stage gate and advance to the next stage.",
        parameters={
            "name": {"type": "string", "description": "Spec name"},
            "notes": {"type": "string", "description": "Optional approval notes", "default": ""},
        },
        required=["name"],
    )
    async def spec_approve(name: str, notes: str = "") -> str:
        from kageha.specs.pipeline import approve_gate

        state = approve_gate(project_root, name, notes)
        return (
            f"Approved. Spec '{name}' advanced to stage: {state.current_stage.value}\n"
            f"Next action: call spec_{state.current_stage.value}(name='{name}')"
        )

    @tool(
        name="spec_revise",
        description="Request revision of the current stage with feedback notes.",
        parameters={
            "name": {"type": "string", "description": "Spec name"},
            "notes": {"type": "string", "description": "Revision feedback / what to change"},
        },
        required=["name", "notes"],
    )
    async def spec_revise(name: str, notes: str) -> str:
        from kageha.specs.pipeline import reject_gate

        state = reject_gate(project_root, name, notes)
        return (
            f"Revision requested for spec '{name}' at stage: {state.current_stage.value}\n"
            f"Notes: {notes}\n\n"
            f"Re-run spec_{state.current_stage.value}(name='{name}') to regenerate."
        )

    @tool(
        name="spec_status",
        description="Show the current status of a spec or list all specs.",
        parameters={
            "name": {
                "type": "string",
                "description": "Spec name (omit to list all specs)",
                "default": "",
            },
        },
        required=[],
    )
    async def spec_status(name: str = "") -> str:
        from kageha.specs.pipeline import list_specs
        from kageha.specs.models import load_spec_state, spec_dir

        if not name:
            specs = list_specs(project_root)
            if not specs:
                return "No specs found. Use spec_new(name, prompt) to create one."
            lines = ["Specs:"]
            for s in specs:
                lines.append(
                    f"  - {s['name']} [{s['stage']}] "
                    f"({s['tasks_count']} tasks) — {s['prompt']}"
                )
            return "\n".join(lines)

        state = load_spec_state(project_root, name)
        if state is None:
            return f"No spec found: '{name}'"

        directory = spec_dir(project_root, name)
        artifacts = [f.name for f in directory.iterdir() if f.is_file()]
        gates_info = "\n".join(
            f"  - {k}: {v.status.value}"
            + (f" ({v.reviewer_notes})" if v.reviewer_notes else "")
            for k, v in state.gates.items()
        )
        tasks_info = "\n".join(
            f"  - {t.id}: {t.title} [{t.status}]" for t in state.tasks
        )
        return (
            f"Spec: {state.name}\n"
            f"Stage: {state.current_stage.value}\n"
            f"Prompt: {state.prompt[:200]}\n"
            f"Artifacts: {', '.join(artifacts)}\n"
            f"Gates:\n{gates_info or '  (none)'}\n"
            f"Tasks:\n{tasks_info or '  (none)'}\n"
            f"Updated: {state.updated_at}"
        )

    registry.register(spec_new)
    registry.register(spec_design)
    registry.register(spec_tasks)
    registry.register(spec_build)
    registry.register(spec_approve)
    registry.register(spec_revise)
    registry.register(spec_status)

    return registry
