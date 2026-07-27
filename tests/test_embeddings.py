"""Embedding config resolution and Gemini-first defaults."""

from __future__ import annotations

from pathlib import Path

import pytest

from kageha.models.embeddings import EmbeddingClient, resolve_embedding_config
from kageha.models.registry import ModelRegistry


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry.load(Path(__file__).resolve().parents[1] / "models.yaml")


def test_models_yaml_defaults_to_gemini_embedding(registry: ModelRegistry) -> None:
    assert registry.embedding.get("provider") == "gemini"
    assert registry.embedding.get("model") == "gemini-embedding-001"
    assert int(registry.embedding.get("dimensions") or 0) == 768


def test_resolve_prefers_gemini_when_key_present(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    cfg = resolve_embedding_config(registry)
    assert cfg is not None
    assert cfg.provider == "gemini"
    assert cfg.model == "gemini-embedding-001"
    assert cfg.dimensions == 768


def test_resolve_falls_back_to_openai(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "o-test")
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    cfg = resolve_embedding_config(registry)
    assert cfg is not None
    assert cfg.provider == "openai"
    assert "embedding" in cfg.model


def test_resolve_none_without_keys(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.delenv("KAGEHA_EMBEDDING_MODEL", raising=False)
    assert resolve_embedding_config(registry) is None
    assert EmbeddingClient.from_registry(registry) is None


def test_env_model_override(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setenv("KAGEHA_EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("KAGEHA_EMBEDDING_MODEL", "gemini-embedding-exp")
    monkeypatch.setenv("KAGEHA_EMBEDDING_DIMENSIONS", "256")
    cfg = resolve_embedding_config(registry)
    assert cfg is not None
    assert cfg.model == "gemini-embedding-exp"
    assert cfg.dimensions == 256
