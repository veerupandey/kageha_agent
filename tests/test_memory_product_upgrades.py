"""Low-bloat memory upgrades: digest, usage ranking, prune, rule import."""

from __future__ import annotations

import time
from pathlib import Path

from kageha.memory.models import MemoryMutation, MemoryQuery, MemoryState
from kageha.memory.service import MemoryService, reset_memory_service_for_tests


def _service(tmp_path: Path, monkeypatch) -> MemoryService:
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    monkeypatch.setenv("KAGEHA_MEMORY_ENABLED", "1")
    monkeypatch.setenv("KAGEHA_MEMORY_EMBEDDINGS", "off")
    monkeypatch.setenv("KAGEHA_MEMORY_IDLE_TTL_DAYS", "90")
    monkeypatch.setenv("KAGEHA_MEMORY_CANDIDATE_TTL_DAYS", "14")
    reset_memory_service_for_tests()
    return MemoryService()


def test_memory_digest_render_is_compact(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    service.mutate(
        MemoryMutation(
            action="remember",
            content="Always use uv instead of pip.",
            kind="instruction",
            scope_type="global",
            session_id="s1",
        )
    )
    context = service.recall(
        MemoryQuery(query="package manager", project_root=str(tmp_path), session_id="s1")
    )
    rendered = context.render()
    assert "## Memory digest" in rendered
    assert "Always use uv instead of pip." in rendered
    assert "Confirmed user memory" not in rendered


def test_usage_multiplier_boosts_recently_accessed(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    cold = service.mutate(
        MemoryMutation(
            action="remember",
            content="The release train uses train-alpha codename.",
            project_root="/projects/demo",
            session_id="s1",
        )
    )
    hot = service.mutate(
        MemoryMutation(
            action="remember",
            content="The release train uses train-beta codename.",
            project_root="/projects/demo",
            session_id="s1",
        )
    )
    # Force access timestamps: hot recently used, cold never used and aged.
    with service.store._conn() as conn:
        conn.execute(
            "UPDATE memories SET last_accessed=?, updated_at=?, created_at=? WHERE id=?",
            (time.time() - 86400 * 40, time.time() - 86400 * 40, time.time() - 86400 * 40, cold.id),
        )
        conn.execute(
            "UPDATE memories SET last_accessed=? WHERE id=?",
            (time.time() - 3600, hot.id),
        )

    assert service._usage_multiplier(service.store.get_memory(hot.id)) == 1.05
    assert service._usage_multiplier(service.store.get_memory(cold.id)) < 1.0


def test_prune_idle_expires_old_candidates(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    monkeypatch.setenv("KAGEHA_MEMORY_CANDIDATE_TTL_DAYS", "1")
    record = service.mutate(
        MemoryMutation(
            action="remember",
            content="Maybe remember the staging host is staging.example.",
            project_root="/projects/demo",
            session_id="s1",
            source_role="assistant",
            confidence=0.7,
        )
    )
    assert record.state == MemoryState.CANDIDATE.value
    with service.store._conn() as conn:
        conn.execute(
            "UPDATE memories SET updated_at=?, created_at=?, last_accessed=NULL WHERE id=?",
            (time.time() - 86400 * 3, time.time() - 86400 * 3, record.id),
        )
    report = service.prune_idle()
    assert report["expired_candidates"] >= 1
    assert service.store.get_memory(record.id).state == MemoryState.EXPIRED.value


def test_import_project_rules_from_agents_md(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    project = tmp_path / "repo"
    project.mkdir()
    (project / "AGENTS.md").write_text(
        "# Conventions\n\nAlways run tests with pytest.\n\n"
        "## Packaging\n\nPrefer uv over pip.\n",
        encoding="utf-8",
    )
    (project / ".cursor" / "rules").mkdir(parents=True)
    (project / ".cursor" / "rules" / "style.mdc").write_text(
        "---\ndescription: style\nalwaysApply: true\n---\n\n"
        "# Style\n\nUse typed Python public APIs.\n",
        encoding="utf-8",
    )

    report = service.import_project_rules(str(project), session_id="import-1")
    assert report["imported"] >= 2
    assert report["skipped_duplicates"] == 0

    again = service.import_project_rules(str(project), session_id="import-2")
    assert again["imported"] == 0
    assert again["skipped_duplicates"] >= 2

    context = service.recall(
        MemoryQuery(
            query="how should I run tests and package installs",
            project_root=str(project),
            session_id="s2",
        )
    )
    blob = context.render().lower()
    assert "pytest" in blob or "uv" in blob or "typed python" in blob
