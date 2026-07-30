"""Adversarial evaluation suite (REL-041/REL-042) — 30 false-success traps.

Thirty focused private tasks across coding, artifact, browser, research, and
lifecycle categories, each run exactly ADVERSARIAL_REPEAT_COUNT (3) times per
configuration regardless of task.repeat, with results stored via
RuntimeStore.record_benchmark(suite="adversarial", ...).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from kageha.eval.harness import EvalResult, GoldenTask, run_environment, run_golden

ADVERSARIAL_REPEAT_COUNT = 3  # runner never exceeds this regardless of task.repeat

_CATEGORIES = ("coding", "artifact", "browser", "research", "lifecycle")


@dataclass
class AdversarialTask:
    id: str
    category: Literal["coding", "artifact", "browser", "research", "lifecycle"]
    prompt: str
    false_success_trap: str  # human-readable description of the trap
    contract_criteria: list[dict[str, Any]] = field(default_factory=list)
    fixtures: dict[str, Any] = field(default_factory=dict)
    forbidden_actions: list[str] = field(default_factory=list)
    expected_terminal_state: str = "success"
    max_cost: float = 0.5
    max_steps: int = 20
    max_time_s: float = 300.0
    repeat: int = 3  # REL-042.1 — exactly 3, enforced by the runner

    def to_golden_task(self) -> GoldenTask:
        """Project into GoldenTask so run_golden()-equivalent logic applies."""
        return GoldenTask(
            id=self.id,
            prompt=self.prompt,
            expect_files=[],
            expect_status=[self.expected_terminal_state],
            max_usd=self.max_cost,
            max_steps=self.max_steps,
            contract_criteria=self.contract_criteria,
            fixtures=self.fixtures,
            forbidden_actions=self.forbidden_actions,
            expected_terminal_state=self.expected_terminal_state,
            max_time_s=self.max_time_s,
            repeat=self.repeat,
        )


@dataclass
class AdversarialRunResult:
    task_id: str
    run_index: int
    outcome: Literal["verified_success", "false_success", "unresolved", "recovered"]
    cost_usd: float
    latency_s: float
    steps: int
    tool_calls: int


def load_adversarial_tasks(directory: Path | None = None) -> list[AdversarialTask]:
    """Load all thirty adversarial tasks from kageha/eval/adversarial_tasks/*.json."""
    base = directory or (Path(__file__).resolve().parent / "adversarial_tasks")
    tasks: list[AdversarialTask] = []
    for category in _CATEGORIES:
        path = base / f"{category}.json"
        if not path.is_file():
            continue
        data = json.loads(path.read_text())
        for item in data:
            tasks.append(AdversarialTask(**item))
    return tasks


def _classify_outcome(
    task: AdversarialTask, result: EvalResult
) -> Literal["verified_success", "false_success", "unresolved", "recovered"]:
    """Best-effort outcome classification from an EvalResult.

    A task marked passed by the harness but whose expected_terminal_state
    was not "success" is a false success — the harness itself proved
    something the task designed to trap. Absent contract-level evidence
    (not wired into run_golden's EvalResult), this is the deterministic
    signal available without a live VerificationReport.
    """
    if task.expected_terminal_state != "success":
        if result.passed:
            return "false_success"
        return "verified_success" if result.status == task.expected_terminal_state else "unresolved"
    if result.passed:
        return "verified_success"
    if result.status in {"repair", "no_progress"}:
        return "recovered"
    return "unresolved"


async def run_adversarial_suite(
    *,
    directory: Path | None = None,
    store: Any = None,
    **run_golden_kwargs: Any,
) -> dict[str, Any]:
    """Run every AdversarialTask exactly ADVERSARIAL_REPEAT_COUNT times.

    Stores each run via RuntimeStore.record_benchmark(suite="adversarial", ...)
    plus one aggregate summary per task (Requirement 18.1, 18.3).
    """
    tasks = load_adversarial_tasks(directory)
    environment = run_environment()
    all_results: list[AdversarialRunResult] = []
    per_task_summaries: list[dict[str, Any]] = []

    for task in tasks:
        golden = task.to_golden_task()
        run_results: list[AdversarialRunResult] = []
        for run_index in range(ADVERSARIAL_REPEAT_COUNT):
            t0 = time.time()
            eval_result = await run_golden(golden, **run_golden_kwargs)
            latency = time.time() - t0
            outcome = _classify_outcome(task, eval_result)
            run_result = AdversarialRunResult(
                task_id=task.id,
                run_index=run_index,
                outcome=outcome,
                cost_usd=eval_result.spent_usd,
                latency_s=latency,
                steps=eval_result.steps,
                tool_calls=eval_result.steps,  # steps is the closest available proxy
            )
            run_results.append(run_result)
            all_results.append(run_result)
            if store is not None:
                store.record_benchmark(
                    suite="adversarial",
                    configuration={
                        "task_id": task.id,
                        "category": task.category,
                        "run_index": run_index,
                    },
                    environment=environment,
                    metrics={
                        "outcome": run_result.outcome,
                        "cost_usd": run_result.cost_usd,
                        "latency_s": run_result.latency_s,
                        "steps": run_result.steps,
                        "tool_calls": run_result.tool_calls,
                    },
                    status=run_result.outcome,
                )

        false_successes = sum(1 for r in run_results if r.outcome == "false_success")
        summary = {
            "task_id": task.id,
            "category": task.category,
            "runs": len(run_results),
            "false_success_count": false_successes,
            "verified_success_count": sum(
                1 for r in run_results if r.outcome == "verified_success"
            ),
            "avg_cost_usd": sum(r.cost_usd for r in run_results) / len(run_results),
            "avg_latency_s": sum(r.latency_s for r in run_results) / len(run_results),
        }
        per_task_summaries.append(summary)
        if store is not None:
            store.record_benchmark(
                suite="adversarial_summary",
                configuration={"task_id": task.id, "category": task.category},
                environment=environment,
                metrics=summary,
                status="ok" if false_successes == 0 else "false_success_detected",
            )

    return {
        "total_tasks": len(tasks),
        "total_runs": len(all_results),
        "false_success_total": sum(
            1 for r in all_results if r.outcome == "false_success"
        ),
        "per_task": per_task_summaries,
        "environment": environment,
    }
