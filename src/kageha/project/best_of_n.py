"""Best-of-N parallel attempts in isolated git worktrees."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Any

from kageha.project.worktree import WorktreeHandle, create_worktree, is_git_repo

ProgressFn = Callable[[dict[str, Any]], None]


@dataclass
class AttemptResult:
    index: int
    label: str
    ok: bool
    run_id: str = ""
    status: str = ""
    message: str = ""
    artifacts: list[str] = field(default_factory=list)
    worktree: str = ""
    branch: str = ""
    error: str = ""
    score: float = 0.0


@dataclass
class BestOfNResult:
    n: int
    winner_index: int
    attempts: list[AttemptResult]
    winner: AttemptResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "winner_index": self.winner_index,
            "winner": None if self.winner is None else _attempt_dict(self.winner),
            "attempts": [_attempt_dict(a) for a in self.attempts],
        }


def _attempt_dict(a: AttemptResult) -> dict[str, Any]:
    return {
        "index": a.index,
        "label": a.label,
        "ok": a.ok,
        "run_id": a.run_id,
        "status": a.status,
        "message": a.message[:2000],
        "artifacts": a.artifacts[:30],
        "worktree": a.worktree,
        "branch": a.branch,
        "error": a.error,
        "score": a.score,
    }


def _score_attempt(a: AttemptResult) -> float:
    if not a.ok:
        return -1.0
    score = 0.0
    status = (a.status or "").lower()
    if status in {"success", "ok", "completed"}:
        score += 3.0
    elif status in {"partial", "needs_input"}:
        score += 1.0
    score += min(len(a.artifacts), 10) * 0.5
    score += min(len(a.message), 800) / 800.0
    return score


def _emit_progress(on_progress: ProgressFn | None, payload: dict[str, Any]) -> None:
    if on_progress is None:
        return
    try:
        on_progress(payload)
    except Exception:  # noqa: BLE001
        pass


async def run_best_of_n(
    *,
    objective: str,
    project_root: str | Path,
    n: int = 2,
    max_steps: int = 24,
    auto_approve: bool = True,
    keep_losers: bool = False,
    base_ref: str = "HEAD",
    on_progress: ProgressFn | None = None,
) -> BestOfNResult:
    """Run N isolated attempts; pick the highest-scoring success.

    ``on_progress`` receives lightweight dicts (started/done/winner) for streaming UIs.
    """
    from kageha.config import security_profile
    from kageha.runtime import AgentRuntime, SecurityProfile, TurnRequest

    count = max(2, min(int(n), 5))
    if not is_git_repo(project_root):
        raise ValueError("best-of-n requires a git repository project_root")

    handles: list[WorktreeHandle] = []
    attempts: list[AttemptResult] = []
    _emit_progress(
        on_progress,
        {"event": "started", "n": count, "objective": objective[:500]},
    )

    async def one(i: int) -> AttemptResult:
        label = f"n{i+1}"
        _emit_progress(
            on_progress,
            {"event": "attempt_started", "index": i, "label": label},
        )
        try:
            wt = create_worktree(project_root, label=label, base_ref=base_ref)
        except Exception as exc:  # noqa: BLE001
            att = AttemptResult(
                index=i, label=label, ok=False, error=f"worktree: {exc}"
            )
            _emit_progress(
                on_progress,
                {"event": "attempt_done", "attempt": _attempt_dict(att)},
            )
            return att
        handles.append(wt)
        _emit_progress(
            on_progress,
            {
                "event": "attempt_ready",
                "index": i,
                "label": label,
                "worktree": str(wt.path),
                "branch": wt.branch,
            },
        )
        runtime = AgentRuntime()
        try:
            result = await runtime.execute(
                TurnRequest(
                    objective=objective,
                    user_id="local",
                    agent_id=f"bestofn:{label}",
                    project_root=str(wt.path),
                    auto_approve=auto_approve,
                    security_profile=SecurityProfile(security_profile()),
                    max_steps=max_steps,
                    live=False,
                    platform="best_of_n",
                    loop_mode="full",
                    agent_mode="plan",
                    system_extra=(
                        f"You are attempt {i+1} of {count} in an isolated git "
                        f"worktree at {wt.path} (branch {wt.branch}). Complete the "
                        "objective in this worktree only. Prefer concrete artifacts."
                    ),
                )
            )
            att = AttemptResult(
                index=i,
                label=label,
                ok=True,
                run_id=result.run_id,
                status=result.status,
                message=result.message or "",
                artifacts=list(result.artifacts or []),
                worktree=str(wt.path),
                branch=wt.branch,
            )
            att.score = _score_attempt(att)
            _emit_progress(
                on_progress,
                {"event": "attempt_done", "attempt": _attempt_dict(att)},
            )
            return att
        except Exception as exc:  # noqa: BLE001
            att = AttemptResult(
                index=i,
                label=label,
                ok=False,
                worktree=str(wt.path),
                branch=wt.branch,
                error=str(exc),
                score=-1.0,
            )
            _emit_progress(
                on_progress,
                {"event": "attempt_done", "attempt": _attempt_dict(att)},
            )
            return att
        finally:
            runtime.close()

    try:
        attempts = list(await asyncio.gather(*[one(i) for i in range(count)]))
    finally:
        if not keep_losers:
            # Keep winner worktree; remove others after scoring.
            pass

    ranked = sorted(attempts, key=lambda a: a.score, reverse=True)
    winner = ranked[0] if ranked else None
    winner_index = winner.index if winner is not None else -1

    if not keep_losers:
        for h in handles:
            if winner is not None and str(h.path) == winner.worktree:
                continue
            try:
                h.remove(force=True)
            except Exception:  # noqa: BLE001
                pass

    result = BestOfNResult(
        n=count,
        winner_index=winner_index,
        attempts=attempts,
        winner=winner,
    )
    _emit_progress(
        on_progress,
        {"event": "done", "result": result.to_dict()},
    )
    return result


def format_best_of_n(result: BestOfNResult) -> str:
    return json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
