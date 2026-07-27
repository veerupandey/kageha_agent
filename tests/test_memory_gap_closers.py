"""Gap-closers: consolidate, fetch, forgotten, rule sync, LLM extract parse, eval."""

from __future__ import annotations

import time
from pathlib import Path

from kageha.memory.extract import _parse_payload
from kageha.memory.models import MemoryMutation, MemoryQuery, TurnMemoryInput
from kageha.memory.service import MemoryService, reset_memory_service_for_tests


def _service(tmp_path: Path, monkeypatch) -> MemoryService:
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    monkeypatch.setenv("KAGEHA_MEMORY_ENABLED", "1")
    monkeypatch.setenv("KAGEHA_MEMORY_EMBEDDINGS", "off")
    monkeypatch.setenv("KAGEHA_MEMORY_LLM_EXTRACT", "off")
    monkeypatch.setenv("KAGEHA_MEMORY_CONSOLIDATE_HOURS", "0")
    reset_memory_service_for_tests()
    return MemoryService()


def test_llm_extract_parser_filters_bad_kinds_and_secrets():
    items = _parse_payload(
        """
        {"memories":[
          {"content":"Prefer uv over pip","kind":"preference","confidence":0.9},
          {"content":"api_key=sk-abcdefghijklmnopqrstuvwxyz012345","kind":"project_fact","confidence":0.9},
          {"content":"Weird","kind":"not_a_kind","confidence":0.9}
        ]}
        """
    )
    assert len(items) == 1
    assert items[0]["kind"] == "preference"
    assert "uv" in items[0]["content"]


def test_consolidate_supersedes_near_duplicates_and_writes_digest(
    tmp_path: Path, monkeypatch
):
    from kageha.memory.models import MemoryRecord, MemoryState

    service = _service(tmp_path, monkeypatch)
    now = time.time()

    def _rec(mid: str, content: str, claim: str, updated: float) -> MemoryRecord:
        return MemoryRecord(
            id=mid,
            kind="project_fact",
            content=content,
            claim_key=claim,
            content_hash=content,
            scope_type="project",
            scope_key="proj",
            state=MemoryState.CONFIRMED.value,
            source_role="user",
            source_session_id="s1",
            source_turn_id="t",
            source_artifact="",
            verification_evidence="test",
            confidence=0.9,
            sensitivity="normal",
            user_id="local",
            agent_id="main",
            project_key="proj",
            channel_key="",
            created_at=updated,
            updated_at=updated,
        )

    a = _rec(
        "a" * 32,
        "Validated artifact path is dist/app.tgz for release builds.",
        "claim-a",
        now - 10,
    )
    b = _rec(
        "b" * 32,
        "Validated artifact path is dist/app.tgz for release build.",
        "claim-b",
        now,
    )
    service.store.insert_memory(a)
    service.store.insert_memory(b)
    report = service.consolidate(force=True)
    assert report["superseded_duplicates"] >= 1
    digest = Path(report["digest_path"])
    assert digest.is_file()
    assert "MEMORY.md" in digest.read_text(encoding="utf-8")
    states = {
        service.store.get_memory(a.id).state,
        service.store.get_memory(b.id).state,
    }
    assert "superseded" in states
    assert "confirmed" in states


def test_fetch_and_forgotten_trail(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    monkeypatch.setenv("KAGEHA_MEMORY_CANDIDATE_TTL_DAYS", "1")
    rec = service.mutate(
        MemoryMutation(
            action="remember",
            content="Staging hostname is staging.example.test",
            project_root=str(tmp_path / "proj"),
            session_id="s1",
        )
    )
    fetched = service.fetch(rec.id)
    assert fetched["type"] == "memory"
    assert fetched["record"]["id"] == rec.id
    assert service.fetch(f"memory:{rec.id}")["record"]["id"] == rec.id

    cand = service.mutate(
        MemoryMutation(
            action="remember",
            content="Maybe remember the canary host is canary.example",
            project_root=str(tmp_path / "proj"),
            session_id="s1",
            source_role="assistant",
            confidence=0.7,
        )
    )
    with service.store._conn() as conn:
        conn.execute(
            "UPDATE memories SET updated_at=?, created_at=?, last_accessed=NULL WHERE id=?",
            (time.time() - 86400 * 3, time.time() - 86400 * 3, cand.id),
        )
    pruned = service.prune_idle()
    assert pruned["expired_candidates"] >= 1
    trail = service.forgotten(limit=10)
    assert any(row.get("id") == cand.id for row in trail)


def test_sync_rules_retracts_removed_imports(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    project = tmp_path / "repo"
    project.mkdir()
    agents = project / "AGENTS.md"
    agents.write_text("# Pack\n\nPrefer uv over pip.\n", encoding="utf-8")
    first = service.import_project_rules(str(project), session_id="i1")
    assert first["imported"] >= 1
    agents.write_text("# Pack\n\nPrefer pnpm over npm.\n", encoding="utf-8")
    second = service.import_project_rules(str(project), session_id="i2", sync=True)
    assert second["imported"] >= 1
    assert second["retracted_stale"] >= 1


def test_hybrid_eval_fixture_lexical_and_vector_paths(tmp_path: Path, monkeypatch):
    """Micro eval: lexical exact + vector paraphrase (mocked vector)."""
    service = _service(tmp_path, monkeypatch)

    class _Vec:
        mode = "fake"
        engine = "fake"
        enabled = True

        def __init__(self) -> None:
            self.ids: list[str] = []

        def index(self, record) -> bool:
            self.ids.append(record.id)
            return True

        def search(self, query: str, *, top_k: int = 12):
            if "nickname" in query.lower() or "release" in query.lower():
                return [{"memory_id": self.ids[0], "score": 0.78}]
            return []

        def rebuild(self, records) -> int:
            self.ids = [r.id for r in records]
            return len(self.ids)

    vector = _Vec()
    service.vector = vector
    cinnabar = service.mutate(
        MemoryMutation(
            action="remember",
            content="Release train codename is Cinnabar.",
            project_root="/projects/eval",
            session_id="e1",
        )
    )
    service.mutate(
        MemoryMutation(
            action="remember",
            content="Prefer uv over pip.",
            kind="preference",
            scope_type="global",
            session_id="e1",
        )
    )
    vector.ids = [cinnabar.id]

    lexical = service.recall(
        MemoryQuery(
            query="Prefer uv over pip",
            project_root="/projects/eval",
            session_id="e2",
        )
    )
    assert any("uv" in i.record.content.lower() for i in lexical.instructions)

    semantic = service.recall(
        MemoryQuery(
            query="what is the nickname of our release train?",
            project_root="/projects/eval",
            session_id="e3",
        )
    )
    assert any(i.record.id == cinnabar.id for i in semantic.project)
    trace = service.explain(semantic.trace_id)
    assert any(
        c.get("id") == cinnabar.id and "vector" in (c.get("sources") or [])
        for c in trace.candidates
    )


def test_capture_uses_regex_when_llm_extract_off(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    turn = TurnMemoryInput(
        session_id="s1",
        turn_id="t1",
        task="setup",
        user_text="Remember that we always use pytest for tests.",
        assistant_text="Noted and configured.",
        status="success",
        verified=True,
        project_root=str(tmp_path / "proj"),
    )
    service.capture_turn(turn)
    service.drain_jobs(max_seconds=2.0)
    rows = service.inspect(limit=20)
    assert any("pytest" in r.content.lower() for r in rows)
