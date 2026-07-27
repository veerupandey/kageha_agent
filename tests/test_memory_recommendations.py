"""P1/P2 audit recommendations: auto rule sync, index pointers, LLM dream parse."""

from __future__ import annotations

import time
from pathlib import Path

from kageha.memory.bootstrap import maybe_sync_project_rules, prepare_turn_memory
from kageha.memory.dream import apply_dream_actions, parse_dream_payload
from kageha.memory.import_rules import rule_files_fingerprint
from kageha.memory.models import MemoryMutation, MemoryQuery, MemoryRecord, MemoryState
from kageha.memory.service import MemoryService, reset_memory_service_for_tests


def _service(tmp_path: Path, monkeypatch) -> MemoryService:
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    monkeypatch.setenv("KAGEHA_MEMORY_ENABLED", "1")
    monkeypatch.setenv("KAGEHA_MEMORY_EMBEDDINGS", "off")
    monkeypatch.setenv("KAGEHA_MEMORY_LLM_EXTRACT", "off")
    monkeypatch.setenv("KAGEHA_MEMORY_LLM_DREAM", "off")
    monkeypatch.setenv("KAGEHA_MEMORY_AUTO_SYNC_RULES", "on")
    monkeypatch.setenv("KAGEHA_MEMORY_CONSOLIDATE_HOURS", "0")
    reset_memory_service_for_tests()
    return MemoryService()


def test_hash_gated_auto_sync_imports_then_skips(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "AGENTS.md").write_text(
        "# Rules\n\nAlways use uv instead of pip.\n",
        encoding="utf-8",
    )
    first = maybe_sync_project_rules(service, str(root), session_id="s1")
    assert first is not None
    assert first.get("skipped") is not True
    assert first["imported"] >= 1
    assert first["fingerprint"] == rule_files_fingerprint(root)

    second = maybe_sync_project_rules(service, str(root), session_id="s1")
    assert second is not None
    assert second["skipped"] is True
    assert second["reason"] == "fingerprint_match"

    (root / "AGENTS.md").write_text(
        "# Rules\n\nAlways use uv instead of pip.\nNever commit secrets.\n",
        encoding="utf-8",
    )
    third = maybe_sync_project_rules(service, str(root), session_id="s1")
    assert third is not None
    assert third.get("skipped") is not True
    assert third["imported"] >= 1


def test_prepare_turn_memory_syncs_and_renders_digest(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    root = tmp_path / "app"
    root.mkdir()
    (root / "CLAUDE.md").write_text(
        "## Style\nPrefer concise answers with provenance.\n",
        encoding="utf-8",
    )
    text = prepare_turn_memory(
        service,
        query="how should answers look",
        project_root=str(root),
        session_id="s1",
    )
    assert "## Memory digest" in text
    assert "concise" in text.lower()
    # Second call is fingerprint-gated (no new imports) but still recalls.
    again = prepare_turn_memory(
        service,
        query="concise provenance",
        project_root=str(root),
        session_id="s1",
    )
    assert "concise" in again.lower()


def test_auto_sync_can_be_disabled(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    monkeypatch.setenv("KAGEHA_MEMORY_AUTO_SYNC_RULES", "off")
    root = tmp_path / "proj"
    root.mkdir()
    (root / "AGENTS.md").write_text("Always pin dependencies.\n", encoding="utf-8")
    assert maybe_sync_project_rules(service, str(root)) is None


def test_empty_fingerprint_retracts_prior_imports(tmp_path: Path, monkeypatch):
    """Deleting all rule files must sync+retract, not idle forever on empty fp."""
    service = _service(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    agents = root / "AGENTS.md"
    agents.write_text("# Rules\n\nAlways use uv instead of pip.\n", encoding="utf-8")
    first = maybe_sync_project_rules(service, str(root), session_id="s1")
    assert first is not None and first.get("imported", 0) >= 1
    memory_ids = list(first.get("memory_ids") or [])
    assert memory_ids
    agents.unlink()
    retracted = maybe_sync_project_rules(service, str(root), session_id="s1")
    assert retracted is not None
    assert retracted.get("skipped") is not True
    assert retracted.get("fingerprint") == ""
    assert retracted.get("retracted_stale", 0) >= 1
    for mid in memory_ids:
        rec = service.store.get_memory(mid)
        assert rec is not None
        assert rec.state == MemoryState.RETRACTED.value


def test_memory_index_pointers_for_unmatched_project_facts(
    tmp_path: Path, monkeypatch
):
    service = _service(tmp_path, monkeypatch)
    monkeypatch.setenv("KAGEHA_MEMORY_AUTO_SYNC_RULES", "off")
    proj = str(tmp_path / "repo")
    Path(proj).mkdir()
    service.mutate(
        MemoryMutation(
            action="remember",
            content="Always prefer typed Python public APIs.",
            kind="instruction",
            scope_type="global",
            session_id="s1",
        )
    )
    service.mutate(
        MemoryMutation(
            action="remember",
            content="Release artifact path is dist/kageha.tgz for nightly builds.",
            kind="project_fact",
            project_root=proj,
            session_id="s1",
        )
    )
    # Query hits the instruction but not the project fact → fact should land in index.
    ctx = service.recall(
        MemoryQuery(
            query="typed Python public APIs",
            project_root=proj,
            session_id="s1",
        )
    )
    rendered = ctx.render()
    assert "## Memory digest" in rendered
    assert "## Memory index" in rendered
    assert "dist/kageha.tgz" in rendered
    assert ctx.index_path.endswith("MEMORY.md")


def test_dream_parser_and_apply(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    now = time.time()

    def _rec(mid: str, content: str) -> MemoryRecord:
        return MemoryRecord(
            id=mid,
            kind="project_fact",
            content=content,
            claim_key=mid,
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
            created_at=now,
            updated_at=now,
        )

    keep = _rec("k" * 32, "Deploy target is us-west1-a staging cluster.")
    drop = _rec("d" * 32, "Deploy target is us-west1-a staging.")
    service.store.insert_memory(keep)
    service.store.insert_memory(drop)

    actions = parse_dream_payload(
        '{"supersede":[{"keep_id":"'
        + keep.id
        + '","drop_id":"'
        + drop.id
        + '","reason":"near duplicate"}]}'
    )
    assert len(actions) == 1
    applied = apply_dream_actions(service.store, actions)
    assert len(applied) == 1
    assert service.store.get_memory(drop.id).state == MemoryState.SUPERSEDED.value
    assert service.store.get_memory(keep.id).state == MemoryState.CONFIRMED.value


def test_consolidate_dream_stays_off_by_default(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    service.mutate(
        MemoryMutation(
            action="remember",
            content="Prefer uv for all Python installs.",
            kind="preference",
            session_id="s1",
        )
    )
    report = service.consolidate(force=True)
    assert report["dream"]["enabled"] is False
    assert report["dream_superseded"] == 0
    assert Path(report["digest_path"]).is_file()
