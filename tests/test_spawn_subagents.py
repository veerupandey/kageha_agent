"""Parallel subagent fan-out."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import patch

from kageha.agents.subagent import (
    _format_subagent_assignments,
    register_subagent_tools,
)
from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.models.registry import ModelRegistry
from kageha.models.router import ModelRouter


def test_format_subagent_assignments_lists_each_task():
    text = _format_subagent_assignments(
        [
            ("a", "research angle A"),
            ("b", "write the report"),
        ],
        kind="spawn_subagents",
        parallel=2,
    )
    assert "2 tasks, parallel≤2" in text
    assert "1. [a] research angle A" in text
    assert "2. [b] write the report" in text


def test_spawn_subagents_runs_in_parallel(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ws = SessionWorkspace.create("parent")
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )
    reg = register_subagent_tools(ctx)
    tool = reg.get("spawn_subagents")
    assert tool is not None

    async def fake_subagent(
        ctx, *, task, mode, max_steps, label="", isolation="", keep_worktree=False
    ):
        await asyncio.sleep(0.08)
        return {
            "ok": True,
            "label": label,
            "run_id": f"sub-{label}",
            "status": "success",
            "message": f"done:{task[:40]}",
            "artifacts": ["result.md"],
            "mode": mode,
            "workspace": str(ctx.workspace.root),
        }

    with patch("kageha.agents.subagent._run_subagent", new=fake_subagent):
        t0 = time.perf_counter()
        out = asyncio.run(
            tool.call(
                tasks_json=json.dumps(
                    [
                        {"id": "a", "task": "research angle A"},
                        {"id": "b", "task": "research angle B"},
                        {"id": "c", "task": "research angle C"},
                    ]
                ),
                max_steps=2,
                max_parallel=3,
            )
        )
        elapsed = time.perf_counter() - t0

    data = json.loads(out)
    assert data["total"] == 3
    assert data["completed"] == 3
    assert {r["label"] for r in data["results"]} == {"a", "b", "c"}
    # Serial would be ~0.24s; parallel should finish near one sleep.
    assert elapsed < 0.18
    board = (ws.root / "subagents_tasks.md").read_text(encoding="utf-8")
    assert "`a`: research angle A" in board
    assert "`c`: research angle C" in board


def test_spawn_subagents_rejects_bad_json(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ws = SessionWorkspace.create("parent2")
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )
    tool = register_subagent_tools(ctx).get("spawn_subagents")
    assert tool is not None
    out = json.loads(asyncio.run(tool.call(tasks_json="{not json")))
    assert out["ok"] is False
