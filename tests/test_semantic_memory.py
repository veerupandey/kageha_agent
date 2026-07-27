"""FTS-only and optional-vector behavior for canonical memory retrieval."""

from __future__ import annotations

from pathlib import Path

from kageha.memory.models import MemoryMutation, MemoryQuery
from kageha.memory.service import MemoryService, reset_memory_service_for_tests
from kageha.memory.vector import MemoryVectorIndex, vector_mode


def test_auto_enables_vector_when_embedding_model_defined(monkeypatch):
    monkeypatch.setenv("KAGEHA_MEMORY_EMBEDDINGS", "auto")
    monkeypatch.setattr(
        "kageha.memory.vector.embedding_model_defined",
        lambda: True,
    )
    assert vector_mode() == "zvec"
    index = MemoryVectorIndex()
    assert index.enabled is True
    assert index.mode == "zvec"
    assert index.requested_mode == "auto"


def test_auto_disables_vector_without_embedding_model(monkeypatch):
    monkeypatch.setenv("KAGEHA_MEMORY_EMBEDDINGS", "auto")
    monkeypatch.setattr(
        "kageha.memory.vector.embedding_model_defined",
        lambda: False,
    )
    assert vector_mode() == "off"
    index = MemoryVectorIndex()
    assert index.enabled is False
    assert index.mode == "off"


def test_fts_only_recall_and_reindex(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    monkeypatch.setenv("KAGEHA_MEMORY_EMBEDDINGS", "off")
    reset_memory_service_for_tests()
    service = MemoryService()
    record = service.mutate(
        MemoryMutation(
            action="remember",
            content="Kageha uses SQLite WAL mode for canonical memory.",
            project_root="/projects/kageha",
            session_id="s1",
        )
    )

    context = service.recall(
        MemoryQuery(
            query="SQLite WAL canonical storage",
            project_root="/projects/kageha",
            session_id="s2",
        )
    )
    assert record.id in {item.record.id for item in context.project}
    report = service.rebuild_index()
    assert report.ok is True
    assert report.engine == "fts5"
    assert report.lexical_records == 1


class _LowSimilarityVector:
    mode = "fake"
    engine = "fake"
    enabled = True

    def __init__(self, memory_id: str = "") -> None:
        self.memory_id = memory_id

    def index(self, record) -> bool:
        self.memory_id = record.id
        return True

    def search(self, query: str, *, top_k: int = 12):
        return [{"memory_id": self.memory_id, "score": 0.64}]

    def rebuild(self, records) -> int:
        return len(list(records))


def test_vector_only_hit_below_similarity_floor_is_excluded(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    monkeypatch.setenv("KAGEHA_MEMORY_EMBEDDINGS", "off")
    reset_memory_service_for_tests()
    vector = _LowSimilarityVector()
    service = MemoryService(vector_index=vector)
    record = service.mutate(
        MemoryMutation(
            action="remember",
            content="Project codename is Cinnabar.",
            project_root="/projects/kageha",
            session_id="s1",
        )
    )
    vector.memory_id = record.id

    context = service.recall(
        MemoryQuery(
            query="totally unrelated vocabulary",
            project_root="/projects/kageha",
            session_id="s1",
        )
    )
    # Low-similarity vector hits must not enter scored recall sections.
    # Same-project index pointers may still list the claim (Claude-style).
    assert not context.instructions
    assert not context.project
    assert not context.episodes
    assert all(item.record.id != record.id for item in context.project)
    trace = service.explain(context.trace_id)
    assert record.id not in {row["id"] for row in trace.selected}
    assert any(
        row["reason"] == "vector_similarity_below_0.65"
        for row in trace.excluded
    )
