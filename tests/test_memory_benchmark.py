"""Small deterministic release-gate benchmark for FTS retrieval."""

from __future__ import annotations

import statistics
import time
from pathlib import Path

from kageha.memory.models import MemoryMutation, MemoryQuery
from kageha.memory.service import MemoryService, reset_memory_service_for_tests


def test_retrieval_precision_recall_and_fts_latency(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    monkeypatch.setenv("KAGEHA_MEMORY_EMBEDDINGS", "off")
    monkeypatch.setenv("KAGEHA_MEMORY_MAX_RESULTS", "5")
    reset_memory_service_for_tests()
    service = MemoryService()
    root = "/projects/benchmark"
    topics = [
        "albatross",
        "banyan",
        "cinnabar",
        "dogwood",
        "evergreen",
        "firefly",
        "gossamer",
        "hemlock",
        "indigo",
        "juniper",
    ]
    relevant: dict[str, set[str]] = {}
    for topic in topics:
        relevant[topic] = set()
        for index in range(5):
            record = service.mutate(
                MemoryMutation(
                    action="remember",
                    content=(
                        f"{topic} component {index} stores verified setting "
                        f"{topic}-{index}."
                    ),
                    scope_type="project",
                    project_root=root,
                    session_id="benchmark",
                )
            )
            relevant[topic].add(record.id)

    precisions: list[float] = []
    recalls: list[float] = []
    latencies: list[float] = []
    for topic in topics:
        start = time.perf_counter()
        context = service.recall(
            MemoryQuery(
                query=f"{topic} verified setting",
                project_root=root,
                session_id="benchmark",
                max_results=5,
            )
        )
        latencies.append(time.perf_counter() - start)
        returned = {item.record.id for item in context.project}
        true_positive = len(returned & relevant[topic])
        precisions.append(true_positive / max(1, len(returned)))
        recalls.append(true_positive / len(relevant[topic]))

    assert statistics.mean(precisions) >= 0.90
    assert statistics.mean(recalls) >= 0.85
    p95 = sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]
    assert p95 < 0.100
