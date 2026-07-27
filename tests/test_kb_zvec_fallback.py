import json

import httpx

from kageha.knowledge.facade import KnowledgeFacade
from kageha.knowledge import zvec_engine


def test_zvec_fallback_ingest_search(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "SILICONFLOW_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    facade = KnowledgeFacade()
    doc = tmp_path / "policy.txt"
    doc.write_text("Refunds are available within 30 days of purchase.")
    kb = facade.create("policies", engine="zvec", sources=[str(doc)])
    assert kb.engine == "zvec"
    hits = facade.search("policies", "refund window", top_k=3)
    assert hits
    assert any("30" in h["text"] or "Refund" in h["text"] for h in hits)


def test_zvec_falls_back_when_embedding_provider_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    monkeypatch.setattr(
        zvec_engine,
        "_resolve_embed_backend",
        lambda: {"provider": "gemini", "model": "test", "dimensions": 64},
    )
    original_embed_many = zvec_engine._embed_many

    def fail_remote_then_hash(texts, *, meta, task_type="RETRIEVAL_DOCUMENT"):
        if meta.get("provider") != "hash":
            raise httpx.ConnectError("offline")
        return original_embed_many(texts, meta=meta, task_type=task_type)

    monkeypatch.setattr(zvec_engine, "_embed_many", fail_remote_then_hash)
    doc = tmp_path / "policy.txt"
    doc.write_text("Refunds are available within 30 days of purchase.")

    facade = KnowledgeFacade()
    facade.create("fallback", engine="zvec", sources=[str(doc)])

    meta_path = tmp_path / "khome" / "kb" / "fallback" / "zvec" / "embed_meta.json"
    meta = json.loads(meta_path.read_text())
    assert meta["provider"] == "hash"
    assert meta["fallback_reason"] == "ConnectError"
    assert facade.search("fallback", "refund window", top_k=3)
