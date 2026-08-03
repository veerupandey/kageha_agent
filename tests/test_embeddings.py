"""Embedding config resolution (Azure-first when configured)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kageha.models.embeddings import EmbeddingClient, resolve_embedding_config
from kageha.models.registry import ModelRegistry


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry.load(Path(__file__).resolve().parents[1] / "models.yaml")


def _clear_embedding_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "SILICONFLOW_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "KAGEHA_EMBEDDING_PROVIDER",
        "KAGEHA_EMBEDDING_MODEL",
        "KAGEHA_EMBEDDING_DIMENSIONS",
    ):
        monkeypatch.delenv(key, raising=False)


def test_models_yaml_defaults_to_gemini_embedding(registry: ModelRegistry) -> None:
    assert registry.embedding.get("provider") == "gemini"
    assert registry.embedding.get("model") == "text-embedding-004"
    assert int(registry.embedding.get("dimensions") or 0) == 768


def test_resolve_prefers_gemini_when_key_present(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_embedding_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    cfg = resolve_embedding_config(registry)
    assert cfg is not None
    assert cfg.provider == "gemini"
    assert cfg.model == "text-embedding-004"
    assert cfg.dimensions == 768


def test_resolve_prefers_gemini_when_preferred(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_embedding_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setenv("KAGEHA_EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("KAGEHA_EMBEDDING_MODEL", "gemini-embedding-001")
    monkeypatch.setenv("KAGEHA_EMBEDDING_DIMENSIONS", "768")
    cfg = resolve_embedding_config(registry)
    assert cfg is not None
    assert cfg.provider == "gemini"
    assert cfg.model == "gemini-embedding-001"
    assert cfg.dimensions == 768


def test_resolve_falls_back_to_openai(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_embedding_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "o-test")
    monkeypatch.setenv("KAGEHA_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("KAGEHA_EMBEDDING_MODEL", "text-embedding-3-small")
    cfg = resolve_embedding_config(registry)
    assert cfg is not None
    assert cfg.provider == "openai"
    assert "embedding" in cfg.model


def test_resolve_none_without_keys(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_embedding_env(monkeypatch)
    assert resolve_embedding_config(registry) is None
    assert EmbeddingClient.from_registry(registry) is None


def test_env_model_override(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_embedding_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setenv("KAGEHA_EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("KAGEHA_EMBEDDING_MODEL", "gemini-embedding-exp")
    monkeypatch.setenv("KAGEHA_EMBEDDING_DIMENSIONS", "256")
    cfg = resolve_embedding_config(registry)
    assert cfg is not None
    assert cfg.model == "gemini-embedding-exp"
    assert cfg.dimensions == 256
