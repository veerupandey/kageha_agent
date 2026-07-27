"""Dependency-aware task graph scheduler."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kageha.agents.task_graph import TaskGraph, ready_nodes, run_task_graph


def test_from_nodes_and_cycle():
    g = TaskGraph.from_nodes(
        [
            {"id": "a", "task": "A"},
            {"id": "b", "task": "B", "depends_on": ["a"]},
            {"id": "c", "task": "C", "depends_on": ["a"]},
        ]
    )
    ready = ready_nodes(g)
    assert [n.id for n in ready] == ["a"]
    with pytest.raises(ValueError, match="cycle"):
        TaskGraph.from_nodes(
            [
                {"id": "a", "task": "A", "depends_on": ["b"]},
                {"id": "b", "task": "B", "depends_on": ["a"]},
            ]
        )


def test_parallel_after_dependency(tmp_path: Path):
    order: list[str] = []
    parallel_seen: list[set[str]] = []

    g = TaskGraph.from_nodes(
        [
            {"id": "a", "task": "A"},
            {"id": "b", "task": "B", "depends_on": ["a"]},
            {"id": "c", "task": "C", "depends_on": ["a"]},
        ]
    )

    async def runner(label: str, task: str, max_steps: int) -> dict:
        order.append(label)
        if label == "a":
            await asyncio.sleep(0.05)
        else:
            # track overlap of b/c
            await asyncio.sleep(0.02)
            running = {x for x in ("b", "c") if x in order and x != label}
            if running:
                parallel_seen.append(running | {label})
            await asyncio.sleep(0.05)
        return {"ok": True, "label": label, "task": task, "max_steps": max_steps}

    summary = asyncio.run(
        run_task_graph(
            g,
            runner=runner,
            max_parallel=4,
            max_steps=3,
            state_path=tmp_path / "task_graph.json",
        )
    )
    assert summary["ok"] is True
    assert order[0] == "a"
    assert set(order[1:]) == {"b", "c"}
    assert (tmp_path / "task_graph.json").is_file()


def test_failure_blocks_dependents():
    g = TaskGraph.from_nodes(
        [
            {"id": "a", "task": "A"},
            {"id": "b", "task": "B", "depends_on": ["a"]},
        ]
    )

    async def runner(label: str, task: str, max_steps: int) -> dict:
        if label == "a":
            return {"ok": False, "label": label, "error": "boom"}
        return {"ok": True, "label": label}

    summary = asyncio.run(run_task_graph(g, runner=runner))
    assert summary["ok"] is False
    assert summary["failed"] == 1
    assert summary["blocked"] == 1
    assert g.nodes["b"].status == "blocked"
