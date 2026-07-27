"""Dependency-aware subagent DAG scheduler."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable


@dataclass
class GraphNode:
    id: str
    task: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | ready | running | done | failed | blocked
    result: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskGraph:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "nodes": {nid: asdict(n) for nid, n in self.nodes.items()},
        }

    @classmethod
    def from_nodes(cls, raw: list[dict[str, Any]]) -> "TaskGraph":
        g = cls()
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValueError(f"node {i} must be an object")
            nid = str(item.get("id") or f"n{i+1}").strip()
            task = str(item.get("task") or item.get("prompt") or "").strip()
            if not nid or not task:
                raise ValueError(f"node {i} needs id and task")
            deps = item.get("depends_on") or item.get("deps") or []
            if isinstance(deps, str):
                deps = [d.strip() for d in deps.split(",") if d.strip()]
            if not isinstance(deps, list):
                raise ValueError(f"node {nid}: depends_on must be a list")
            if nid in g.nodes:
                raise ValueError(f"duplicate node id {nid}")
            g.nodes[nid] = GraphNode(
                id=nid,
                task=task,
                depends_on=[str(d) for d in deps],
            )
        # Validate deps exist
        for n in g.nodes.values():
            for d in n.depends_on:
                if d not in g.nodes:
                    raise ValueError(f"node {n.id} depends on unknown id {d}")
        if _has_cycle(g):
            raise ValueError("task graph has a cycle")
        return g

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


def _has_cycle(graph: TaskGraph) -> bool:
    visiting: set[str] = set()
    seen: set[str] = set()

    def dfs(nid: str) -> bool:
        if nid in seen:
            return False
        if nid in visiting:
            return True
        visiting.add(nid)
        for d in graph.nodes[nid].depends_on:
            if dfs(d):
                return True
        visiting.remove(nid)
        seen.add(nid)
        return False

    return any(dfs(nid) for nid in graph.nodes)


def ready_nodes(graph: TaskGraph) -> list[GraphNode]:
    out: list[GraphNode] = []
    for n in graph.nodes.values():
        if n.status not in {"pending", "ready"}:
            continue
        deps_ok = all(
            graph.nodes[d].status == "done" for d in n.depends_on
        )
        if any(graph.nodes[d].status == "failed" for d in n.depends_on):
            n.status = "blocked"
            continue
        if deps_ok:
            n.status = "ready"
            out.append(n)
    return out


async def run_task_graph(
    graph: TaskGraph,
    *,
    runner: Callable[[str, str, int], Awaitable[dict[str, Any]]],
    max_parallel: int = 4,
    max_steps: int = 6,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Run ready nodes in waves until done or blocked.

    ``runner(label, task, max_steps) -> result dict with ok bool``.
    Failed node blocks dependents (status=blocked); does not mark overall SUCCESS.
    """
    parallel = max(1, min(int(max_parallel or 4), 8))
    sem = asyncio.Semaphore(parallel)

    async def run_one(node: GraphNode) -> None:
        async with sem:
            node.status = "running"
            if state_path:
                graph.save(state_path)
            try:
                result = await runner(node.id, node.task, max_steps)
            except Exception as e:  # noqa: BLE001
                result = {"ok": False, "label": node.id, "error": str(e)}
            node.result = result
            node.status = "done" if result.get("ok") else "failed"
            if state_path:
                graph.save(state_path)

    while True:
        ready = ready_nodes(graph)
        if not ready:
            break
        await asyncio.gather(*[run_one(n) for n in ready])

    # Mark remaining pending as blocked if deps failed
    for n in graph.nodes.values():
        if n.status == "pending":
            if any(graph.nodes[d].status in {"failed", "blocked"} for d in n.depends_on):
                n.status = "blocked"

    if state_path:
        graph.save(state_path)

    statuses = {n.id: n.status for n in graph.nodes.values()}
    done = sum(1 for s in statuses.values() if s == "done")
    failed = sum(1 for s in statuses.values() if s == "failed")
    blocked = sum(1 for s in statuses.values() if s == "blocked")
    return {
        "ok": failed == 0 and blocked == 0 and done == len(graph.nodes),
        "done": done,
        "failed": failed,
        "blocked": blocked,
        "total": len(graph.nodes),
        "statuses": statuses,
        "results": {n.id: n.result for n in graph.nodes.values()},
    }
