"""Subagent spawn — isolated or shared; single or parallel fan-out."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from kageha.harness.tools.base import ToolRegistry, tool

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext

Mode = Literal["communication"]


def _format_subagent_assignments(
    jobs: list[tuple[str, str]],
    *,
    kind: str = "spawn_subagents",
    parallel: int | None = None,
    max_task_chars: int = 120,
) -> str:
    """Human-readable assignment board for chat progress / logs."""
    unit = "nodes" if kind == "spawn_task_graph" else "tasks"
    head = f"[kageha] {kind}: {len(jobs)} {unit}"
    if parallel is not None:
        head += f", parallel≤{parallel}"
    lines = [head]
    for i, (label, task) in enumerate(jobs, 1):
        body = " ".join((task or "").split())
        if len(body) > max_task_chars:
            body = body[: max_task_chars - 1].rstrip() + "…"
        lines.append(f"  {i}. [{label}] {body}")
    return "\n".join(lines)


def _write_subagent_board(
    ctx: "HarnessContext",
    jobs: list[tuple[str, str]],
    *,
    kind: str,
) -> None:
    """Persist assignment list under the parent workspace for /files /where."""
    try:
        lines = [f"# {kind}\n"]
        for label, task in jobs:
            lines.append(f"- [ ] `{label}`: {task}")
        ctx.workspace.write_text("subagents_tasks.md", "\n".join(lines) + "\n")
    except Exception:  # noqa: BLE001
        pass


async def _run_subagent(
    ctx: "HarnessContext",
    *,
    task: str,
    mode: str,
    max_steps: int,
    label: str = "",
    isolation: str = "",
    keep_worktree: bool = False,
) -> dict[str, Any]:
    from kageha.config import security_profile
    from kageha.memory.service import (
        get_memory_service,
        turn_memory_input_from_result,
    )
    from kageha.memory.skills import SkillRegistry
    from kageha.project.hooks import load_hook_runner
    from kageha.project.worktree import create_worktree, is_git_repo

    mode_norm = (mode or "communication").strip().lower()
    if mode_norm != "communication":
        return {
            "ok": False,
            "error": (
                "shared_memory subagents are unsupported because concurrent "
                "writers cannot be safely journaled; use communication mode"
            ),
            "label": label,
        }

    parent_root = (
        str(getattr(ctx, "project_root", "") or "").strip()
        or str(ctx.meta.get("project_root") or "").strip()
        or str(Path.cwd())
    )
    project_root = parent_root
    worktree_meta: dict[str, Any] = {}
    wt_handle = None
    isolation_norm = (isolation or "").strip().lower()
    if isolation_norm in {"worktree", "git", "wt"} and is_git_repo(parent_root):
        try:
            wt_handle = create_worktree(parent_root, label=label or "subagent")
            project_root = str(wt_handle.path)
            worktree_meta = {
                "worktree": project_root,
                "branch": wt_handle.branch,
                "isolation": "worktree",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": f"worktree isolation failed: {exc}",
                "label": label,
            }

    hooks = load_hook_runner(project_root)
    hooks.run(
        "subagentStart",
        payload={"task": task, "label": label, "project_root": project_root},
    )

    skills = SkillRegistry()
    agent_id = f"subagent:{label or 'isolated'}"
    from kageha.memory.bootstrap import prepare_turn_memory

    memory = get_memory_service(start_worker=True)
    memory_extra = prepare_turn_memory(
        memory,
        query=task,
        project_root=project_root,
        session_id="",
        agent_id=agent_id,
    )
    steps = max(1, min(int(max_steps), 12))
    from kageha.runtime import (
        AgentRuntime,
        SecurityProfile,
        TurnRequest,
    )

    runtime = AgentRuntime()
    try:
        result = await runtime.execute(
            TurnRequest(
                objective=task,
                user_id="local",
                agent_id=agent_id,
                project_root=project_root,
                auto_approve=ctx.approvals.auto_approve,
                approver=ctx.approvals.approver,
                security_profile=SecurityProfile(security_profile()),
                max_steps=steps,
                knowledge_bases=tuple(ctx.attached_kbs),
                skill_catalog=skills.catalog(limit=40),
                kb_pins=", ".join(ctx.attached_kbs) if ctx.attached_kbs else "",
                live=False,
                platform="subagent",
                system_extra=(memory_extra + "\n\n" if memory_extra else "")
                + (
                    "You are a focused subagent in an isolated, journaled "
                    "workspace. Complete only the assigned subtask. Write outputs "
                    "into your session workspace and summarize absolute paths for "
                    "the parent. Be concise."
                    + (
                        f"\nYou are checked out in git worktree `{project_root}` "
                        f"(branch `{worktree_meta.get('branch', '')}`). Edit only "
                        "files inside this worktree."
                        if worktree_meta
                        else ""
                    )
                ),
            )
        )
        from kageha.config import sessions_dir

        ws_root = str(sessions_dir() / result.run_id)
    except Exception as exc:
        from kageha.memory.models import TurnMemoryInput

        memory.capture_turn(
            TurnMemoryInput(
                session_id="subagent-failed",
                turn_id=f"error-{time.time_ns()}",
                task=task,
                user_text=task,
                assistant_text=str(exc),
                status="error",
                verified=False,
                project_root=project_root,
                agent_id=agent_id,
            )
        )
        hooks.run(
            "subagentStop",
            payload={
                "ok": False,
                "error": str(exc),
                "label": label,
                "project_root": project_root,
            },
        )
        if wt_handle is not None and not keep_worktree:
            try:
                wt_handle.remove(force=True)
            except Exception:  # noqa: BLE001
                pass
        raise
    finally:
        runtime.close()
    memory.capture_turn(
        turn_memory_input_from_result(
            result,
            task=task,
            user_text=task,
            project_root=project_root,
            agent_id=agent_id,
        )
    )
    out = {
        "ok": True,
        "label": label or result.run_id,
        "run_id": result.run_id,
        "status": result.status,
        "message": result.message[:4000],
        "artifacts": result.artifacts[:30],
        "mode": mode_norm,
        "workspace": ws_root,
        **worktree_meta,
    }
    hooks.run("subagentStop", payload={**out, "project_root": project_root})
    if wt_handle is not None and not keep_worktree:
        # Keep worktree when the attempt produced a branch the parent may merge.
        if str(result.status).lower() not in {"success", "ok", "completed"}:
            try:
                wt_handle.remove(force=True)
            except Exception:  # noqa: BLE001
                pass
    return out


def register_subagent_tools(ctx: "HarnessContext") -> ToolRegistry:
    reg = ToolRegistry()

    @tool(
        description=(
            "Spawn one focused subagent in an isolated journaled workspace "
            "(communication mode). Safe to parallelize. "
            "Set isolation='worktree' to check out a git worktree under "
            ".kageha/worktrees/ so parallel agents do not clobber files."
        )
    )
    async def spawn_subagent(
        task: str,
        max_steps: int = 8,
        isolation: str = "",
        keep_worktree: bool = False,
    ) -> str:
        try:
            out = await _run_subagent(
                ctx,
                task=task,
                mode="communication",
                max_steps=max_steps,
                isolation=isolation,
                keep_worktree=keep_worktree,
            )
            return json.dumps(out)
        except Exception as e:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(e)})

    @tool(
        description=(
            "Fan-out: run several subagents IN PARALLEL. "
            "tasks_json is a JSON array of strings, or objects "
            '{"id":"a","task":"..."} . Uses isolated workspaces (communication mode). '
            "Ideal for parallel research angles, parallel slide drafts, etc. "
            "max_parallel caps concurrency (default 4, max 8). "
            "Set isolation='worktree' for git worktree isolation per task."
        )
    )
    async def spawn_subagents(
        tasks_json: str,
        max_steps: int = 6,
        max_parallel: int = 4,
        isolation: str = "",
    ) -> str:
        try:
            raw = json.loads(tasks_json or "[]")
        except json.JSONDecodeError as e:
            return json.dumps({"ok": False, "error": f"tasks_json must be JSON array: {e}"})
        if not isinstance(raw, list) or not raw:
            return json.dumps({"ok": False, "error": "tasks_json must be a non-empty JSON array"})

        jobs: list[tuple[str, str]] = []
        for i, item in enumerate(raw[:12]):
            if isinstance(item, str):
                jobs.append((f"t{i+1}", item.strip()))
            elif isinstance(item, dict):
                label = str(item.get("id") or item.get("label") or f"t{i+1}")
                task = str(item.get("task") or item.get("prompt") or "").strip()
                if task:
                    jobs.append((label, task))
            else:
                return json.dumps({"ok": False, "error": f"bad task entry at index {i}"})
        if not jobs:
            return json.dumps({"ok": False, "error": "no valid tasks"})

        parallel = max(1, min(int(max_parallel or 4), 8))
        sem = asyncio.Semaphore(parallel)

        async def one(label: str, task: str) -> dict[str, Any]:
            async with sem:
                try:
                    return await _run_subagent(
                        ctx,
                        task=task,
                        mode="communication",
                        max_steps=max_steps,
                        label=label,
                        isolation=isolation,
                        keep_worktree=True,
                    )
                except Exception as e:  # noqa: BLE001
                    return {"ok": False, "label": label, "error": str(e)}

        board = _format_subagent_assignments(
            jobs, kind="spawn_subagents", parallel=parallel
        )
        print(board, flush=True)
        _write_subagent_board(ctx, jobs, kind="spawn_subagents")
        results = await asyncio.gather(*[one(label, task) for label, task in jobs])
        ok_n = sum(1 for r in results if r.get("ok"))
        return json.dumps(
            {
                "ok": ok_n == len(results),
                "completed": ok_n,
                "total": len(results),
                "max_parallel": parallel,
                "results": list(results),
            },
            indent=2,
        )[:12000]

    @tool(
        description=(
            "Run a dependency graph of subagents. nodes_json is a JSON array of "
            '{"id":"a","task":"...","depends_on":["b"]}. Ready nodes run in parallel '
            "(max_parallel, default 4, max 8). Failed nodes block dependents. "
            "Persists state to task_graph.json. Prefer this in spec/goal modes."
        )
    )
    async def spawn_task_graph(
        nodes_json: str,
        max_steps: int = 6,
        max_parallel: int = 4,
        isolation: str = "worktree",
    ) -> str:
        from kageha.agents.task_graph import TaskGraph, run_task_graph

        try:
            raw = json.loads(nodes_json or "[]")
        except json.JSONDecodeError as e:
            return json.dumps(
                {"ok": False, "error": f"nodes_json must be JSON array: {e}"}
            )
        if not isinstance(raw, list) or not raw:
            return json.dumps(
                {"ok": False, "error": "nodes_json must be a non-empty JSON array"}
            )
        if len(raw) > 12:
            raw = raw[:12]
        try:
            graph = TaskGraph.from_nodes(raw)
        except ValueError as e:
            return json.dumps({"ok": False, "error": str(e)})

        state_path = ctx.workspace.root / "task_graph.json"
        graph.save(state_path)

        async def runner(label: str, task: str, steps: int) -> dict[str, Any]:
            return await _run_subagent(
                ctx,
                task=task,
                mode="communication",
                max_steps=steps,
                label=label,
                isolation=isolation,
                keep_worktree=True,
            )

        graph_jobs = [(nid, node.task) for nid, node in graph.nodes.items()]
        print(
            _format_subagent_assignments(
                graph_jobs,
                kind="spawn_task_graph",
                parallel=max(1, min(int(max_parallel or 4), 8)),
            ),
            flush=True,
        )
        _write_subagent_board(ctx, graph_jobs, kind="spawn_task_graph")
        summary = await run_task_graph(
            graph,
            runner=runner,
            max_parallel=max_parallel,
            max_steps=max_steps,
            state_path=state_path,
        )
        # Also write a markdown board for humans / todos sync
        lines = ["# Task graph\n"]
        for nid, node in graph.nodes.items():
            mark = {
                "done": "x",
                "failed": "!",
                "blocked": "-",
                "running": "~",
            }.get(node.status, " ")
            deps = f" (after {', '.join(node.depends_on)})" if node.depends_on else ""
            lines.append(f"- [{mark}] `{nid}`{deps}: {node.task[:120]}")
        ctx.workspace.write_text("task_graph.md", "\n".join(lines) + "\n")
        return json.dumps(summary, indent=2)[:12000]

    for t in (spawn_subagent, spawn_subagents, spawn_task_graph):
        if hasattr(t, "name"):
            reg.register(t)  # type: ignore[arg-type]
    return reg
