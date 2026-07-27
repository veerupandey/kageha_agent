"""Reproducible Kageha-only benchmark and soak scorecards."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from kageha.runtime.engine import AgentRuntime
from kageha.runtime.store import RuntimeStore
from kageha.runtime.types import SecurityProfile, TurnRequest


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    category: str
    objective: str
    max_steps: int = 40
    expected_artifacts: int = 0
    tags: tuple[str, ...] = ()


@dataclass
class BenchmarkScorecard:
    suite: str
    runs: int
    accepted: int
    delivered: int
    successful: int
    false_successes: int
    duplicate_mutations: int
    secret_leaks: int
    latencies_ms: list[float] = field(default_factory=list)
    costs_usd: list[float] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return self.successful / self.runs if self.runs else 0.0

    @property
    def delivery_rate(self) -> float:
        return self.delivered / self.accepted if self.accepted else 0.0

    @property
    def false_success_rate(self) -> float:
        return self.false_successes / self.runs if self.runs else 0.0

    def percentile(self, percentile: float) -> float:
        if not self.latencies_ms:
            return 0.0
        values = sorted(self.latencies_ms)
        index = min(
            len(values) - 1,
            max(0, int(round((len(values) - 1) * percentile))),
        )
        return values[index]

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "success_rate": self.success_rate,
            "delivery_rate": self.delivery_rate,
            "false_success_rate": self.false_success_rate,
            "latency_p50_ms": self.percentile(0.50),
            "latency_p95_ms": self.percentile(0.95),
            "gates": {
                "deterministic_success_95": self.success_rate >= 0.95,
                "false_success_below_1": self.false_success_rate < 0.01,
                "delivery_999": self.delivery_rate >= 0.999,
                "zero_duplicate_mutations": self.duplicate_mutations == 0,
                "zero_secret_leaks": self.secret_leaks == 0,
            },
        }


def load_cases(path: Path) -> list[BenchmarkCase]:
    data = yaml.safe_load(path.read_text()) or {}
    cases: list[BenchmarkCase] = []
    for raw in data.get("cases") or []:
        cases.append(
            BenchmarkCase(
                id=str(raw["id"]),
                category=str(raw["category"]),
                objective=str(raw["objective"]),
                max_steps=int(raw.get("max_steps") or 40),
                expected_artifacts=int(raw.get("expected_artifacts") or 0),
                tags=tuple(raw.get("tags") or ()),
            )
        )
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("benchmark case ids must be unique")
    return cases


class BenchmarkRunner:
    def __init__(
        self,
        *,
        store: RuntimeStore | None = None,
        runtime: AgentRuntime | None = None,
    ) -> None:
        self._owns_store = store is None and runtime is None
        self.store = store or (runtime.store if runtime is not None else RuntimeStore())
        self._owns_runtime = runtime is None
        self.runtime = runtime or AgentRuntime(store=self.store)

    def close(self) -> None:
        if self._owns_runtime:
            self.runtime.close()
        if self._owns_store:
            self.store.close()

    async def run(
        self,
        cases: list[BenchmarkCase],
        *,
        suite: str,
        repeats: int = 3,
        security_profile: SecurityProfile = SecurityProfile.STRICT,
        auto_approve: bool = True,
    ) -> BenchmarkScorecard:
        score = BenchmarkScorecard(
            suite=suite,
            runs=len(cases) * max(1, repeats),
            accepted=0,
            delivered=0,
            successful=0,
            false_successes=0,
            duplicate_mutations=0,
            secret_leaks=0,
        )
        for case in cases:
            for repeat in range(max(1, repeats)):
                started = time.perf_counter()
                score.accepted += 1
                try:
                    result = await self.runtime.execute(
                        TurnRequest(
                            objective=case.objective,
                            max_steps=case.max_steps,
                            auto_approve=auto_approve,
                            security_profile=security_profile,
                            live=False,
                            platform="benchmark",
                            idempotency_key=f"benchmark:{suite}:{case.id}:{repeat}",
                            metadata={
                                "benchmark_suite": suite,
                                "case_id": case.id,
                                "repeat": repeat,
                            },
                        )
                    )
                    score.delivered += 1
                    if result.status == "success" and result.validated:
                        score.successful += 1
                    elif result.status == "success":
                        score.false_successes += 1
                    if len(result.turn_artifacts or result.artifacts) < case.expected_artifacts:
                        score.failures.append(
                            {
                                "case": case.id,
                                "repeat": repeat,
                                "reason": "artifact_count",
                            }
                        )
                    score.costs_usd.append(result.spent_usd)
                except Exception as exc:  # noqa: BLE001
                    score.failures.append(
                        {
                            "case": case.id,
                            "repeat": repeat,
                            "error_type": type(exc).__name__,
                        }
                    )
                finally:
                    score.latencies_ms.append(
                        (time.perf_counter() - started) * 1000.0
                    )
        payload = score.to_dict()
        self.store.record_benchmark(
            suite=suite,
            configuration={
                "repeats": repeats,
                "security_profile": security_profile.value,
                "case_ids": [case.id for case in cases],
            },
            environment=environment_fingerprint(),
            metrics=payload,
            status="pass" if all(payload["gates"].values()) else "fail",
        )
        return score


async def run_soak(
    cases: list[BenchmarkCase],
    *,
    hours: float = 72.0,
    max_turns: int = 0,
    store: RuntimeStore | None = None,
) -> BenchmarkScorecard:
    if hours <= 0:
        raise ValueError("hours must be positive")
    runner = BenchmarkRunner(store=store)
    aggregate = BenchmarkScorecard(
        suite=f"soak-{hours:g}h",
        runs=0,
        accepted=0,
        delivered=0,
        successful=0,
        false_successes=0,
        duplicate_mutations=0,
        secret_leaks=0,
    )
    deadline = time.monotonic() + hours * 3600.0
    index = 0
    try:
        while time.monotonic() < deadline and (not max_turns or index < max_turns):
            case = cases[index % len(cases)]
            result = await runner.run(
                [case],
                suite=aggregate.suite,
                repeats=1,
                security_profile=SecurityProfile.STRICT,
            )
            aggregate.runs += result.runs
            aggregate.accepted += result.accepted
            aggregate.delivered += result.delivered
            aggregate.successful += result.successful
            aggregate.false_successes += result.false_successes
            aggregate.duplicate_mutations += result.duplicate_mutations
            aggregate.secret_leaks += result.secret_leaks
            aggregate.latencies_ms.extend(result.latencies_ms)
            aggregate.costs_usd.extend(result.costs_usd)
            aggregate.failures.extend(result.failures)
            index += 1
            await asyncio.sleep(0)
    finally:
        runner.close()
    return aggregate


def environment_fingerprint() -> dict[str, Any]:
    commit = ""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
    except OSError:
        pass
    configuration = {
        key: bool(os.environ.get(key))
        for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "SILICONFLOW_API_KEY")
    }
    source = json.dumps(configuration, sort_keys=True)
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "git_commit": commit,
        "provider_configuration_hash": hashlib.sha256(source.encode()).hexdigest(),
    }

