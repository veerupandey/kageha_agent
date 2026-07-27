"""Skill embedding index + Gemini-first config overrides."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kageha.memory.skill_embeddings import (
    SkillEmbeddingIndex,
    SkillEmbedHit,
    _cosine,
)
from kageha.memory.skills import Skill, SkillRegistry
from kageha.models.embeddings import resolve_embedding_config
from kageha.models.registry import ModelRegistry
from kageha.models.voice import resolve_voice_config


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry.load(Path(__file__).resolve().parents[1] / "models.yaml")


def test_env_overrides_embedding_model(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setenv("KAGEHA_EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("KAGEHA_EMBEDDING_MODEL", "gemini-embedding-custom")
    monkeypatch.setenv("KAGEHA_EMBEDDING_DIMENSIONS", "512")
    cfg = resolve_embedding_config(registry)
    assert cfg is not None
    assert cfg.provider == "gemini"
    assert cfg.model == "gemini-embedding-custom"
    assert cfg.dimensions == 512


def test_voice_config_from_yaml(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    cfg = resolve_voice_config(registry)
    assert cfg is not None
    assert cfg.provider == "gemini"
    assert cfg.model == "gemini-3.1-flash-tts-preview"
    assert cfg.voice == "Kore"


def test_voice_env_override(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setenv("KAGEHA_VOICE_MODEL", "gemini-2.5-pro-preview-tts")
    monkeypatch.setenv("KAGEHA_VOICE_NAME", "Puck")
    cfg = resolve_voice_config(registry)
    assert cfg is not None
    assert cfg.model == "gemini-2.5-pro-preview-tts"
    assert cfg.voice == "Puck"


def test_voice_rejects_non_gemini_provider(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setenv("KAGEHA_VOICE_PROVIDER", "openai")
    with pytest.raises(ValueError, match="Unsupported voice provider"):
        resolve_voice_config(registry)


def test_cosine_identical() -> None:
    v = [1.0, 0.0, 0.0]
    assert abs(_cosine(v, v) - 1.0) < 1e-6


def test_skill_index_search_uses_cached_vectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry: ModelRegistry
) -> None:
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.delenv("KAGEHA_SKILL_EMBEDDINGS", raising=False)

    skill = Skill(
        name="network_scan",
        description="LAN Wi-Fi device and TV discovery",
        path=tmp_path / "network_scan" / "SKILL.md",
        body="# network_scan\n",
    )
    (tmp_path / "network_scan").mkdir()
    skill.path.write_text("---\nname: network_scan\n---\n", encoding="utf-8")

    index = SkillEmbeddingIndex(registry)
    # Pretend vectors already cached (skip live Gemini call).
    index._data = {
        "provider": "gemini",
        "model": "gemini-embedding-001",
        "dimensions": 768,
        "skills": {
            "network_scan": {
                "fingerprint": "x",
                "vector": [1.0, 0.0, 0.0],
            },
            "other": {
                "fingerprint": "y",
                "vector": [0.0, 1.0, 0.0],
            },
        },
    }

    # Mock query embedding → align with network_scan
    client = MagicMock()

    async def _embed(texts, task_type="RETRIEVAL_DOCUMENT"):
        return [[1.0, 0.0, 0.0] for _ in texts]

    client.embed = _embed
    monkeypatch.setattr(
        "kageha.memory.skill_embeddings.EmbeddingClient.from_registry",
        lambda *_a, **_k: client,
    )
    # Bypass fingerprint refresh in ensure
    monkeypatch.setattr(index, "ensure", lambda *_a, **_k: True)

    hits = index.search("find devices on my wifi", limit=2)
    assert hits
    assert hits[0].name == "network_scan"
    assert isinstance(hits[0], SkillEmbedHit)


def test_match_blends_embed_boost(monkeypatch: pytest.MonkeyPatch) -> None:
    """Semantic hit without strong tokens still ranks when embeddings boost."""
    import kageha.memory.skill_embeddings as se

    class FakeHit:
        def __init__(self, name: str, score: float) -> None:
            self.name = name
            self.score = score

    class FakeIndex:
        def ensure(self, _skills):
            return True

        def search(self, query, *, limit=5):
            return [FakeHit("web_research", 0.82)]

    monkeypatch.setattr(se, "get_skill_embedding_index", lambda **_k: FakeIndex())

    reg = SkillRegistry()
    if "web_research" not in reg.skills:
        pytest.skip("web_research skill not installed")
    matched = reg.match("look up recent papers on transformers", limit=3)
    names = [s.name for s in matched]
    assert "web_research" in names or "getting_started" in names
