"""Session model override: /model command + router pin."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kageha.chat.model_commands import (
    expand_role_overrides,
    handle_model_command,
    model_policy_allow,
    persist_global_model,
    resolve_model_id,
)
from kageha.harness.sandbox import SessionWorkspace
from kageha.models.base import ChatMessage
from kageha.models.registry import ModelConfig, ModelRegistry, ProviderConfig
from kageha.models.router import ModelRouter


def _registry(model_policy: dict | None = None) -> ModelRegistry:
    return ModelRegistry(
        providers={
            "gemini": ProviderConfig(
                name="gemini",
                protocol="gemini",
                base_url="https://example",
                api_key_env="GEMINI_API_KEY",
            ),
            "openai": ProviderConfig(
                name="openai",
                protocol="openai_compat",
                base_url="https://example",
                api_key_env="OPENAI_API_KEY",
            ),
            "antigravity": ProviderConfig(
                name="antigravity",
                protocol="gemini_cli",
                base_url="",
                api_key_env="ANTIGRAVITY_CLI",
            ),
            "openai-codex": ProviderConfig(
                name="openai-codex",
                protocol="openai_compat",
                base_url="https://example",
                api_key_env="OPENAI_CODEX_OAUTH",
            ),
        },
        models={
            "gemini-flash": ModelConfig(
                id="gemini-flash",
                provider="gemini",
                model="gemini-3.6-flash",
                roles=["tool_calling", "fast_worker"],
            ),
            "gemini-pro": ModelConfig(
                id="gemini-pro",
                provider="gemini",
                model="gemini-3.1-pro-preview",
                roles=["planning"],
            ),
            "gpt-fast": ModelConfig(
                id="gpt-fast",
                provider="openai",
                model="gpt-4.1-mini",
                roles=["tool_calling"],
            ),
            "antigravity": ModelConfig(
                id="antigravity",
                provider="antigravity",
                model="gemini-3.1-pro-preview",
                roles=["planning"],
            ),
            "antigravity-flash": ModelConfig(
                id="antigravity-flash",
                provider="antigravity",
                model="gemini-3.6-flash",
                roles=["fast_worker", "tool_calling"],
            ),
            "antigravity-3-flash": ModelConfig(
                id="antigravity-3-flash",
                provider="antigravity",
                model="gemini-3-flash-preview",
                roles=["fast_worker", "tool_calling"],
            ),
            "gpt-codex": ModelConfig(
                id="gpt-codex",
                provider="openai-codex",
                model="gpt-5.6-sol",
                roles=["coding"],
            ),
        },
        roles={
            "tool_calling": ["gemini-flash", "gpt-fast"],
            "fast_worker": ["gemini-flash", "gpt-fast"],
            "planning": ["gemini-pro"],
            "default": ["gemini-pro"],
        },
        model_policy=dict(model_policy or {}),
    )


def test_resolve_model_id_fuzzy():
    reg = _registry()
    assert resolve_model_id("gemini-flash", reg) == "gemini-flash"
    assert resolve_model_id("flash", reg) == "gemini-flash"
    assert resolve_model_id("gemini-3.6", reg) == "gemini-flash"


def test_workspace_persists_model_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ws = SessionWorkspace.create("mod1")
    assert ws.get_model_override() is None
    ws.set_model_override("gemini-pro")
    assert ws.get_model_override() == "gemini-pro"
    reopened = SessionWorkspace.open("mod1")
    assert reopened.get_model_override() == "gemini-pro"
    reopened.set_model_override(None)
    assert SessionWorkspace.open("mod1").get_model_override() is None


def test_handle_model_command_set_and_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reg = _registry()
    ws = SessionWorkspace.create("mod2")
    result = handle_model_command(
        "/model flash",
        override=None,
        workspace=ws,
        registry=reg,
    )
    assert result.handled
    assert result.override == "gemini-flash"
    assert "gemini-flash" in result.message
    assert "OK —" in result.message
    assert "planner + executor" in result.message
    assert ws.get_model_override() == "gemini-flash"

    result = handle_model_command(
        "/model reset",
        override=result.override,
        workspace=ws,
        registry=reg,
    )
    assert result.handled
    assert result.override is None
    assert ws.get_model_override() is None
    assert "auto" in result.message.lower()


def test_model_once_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    reg = _registry()
    ws = SessionWorkspace.create("mod-once")
    result = handle_model_command(
        "/model gemini-pro --once",
        override=None,
        workspace=ws,
        registry=reg,
    )
    assert result.once == "gemini-pro"
    assert result.override is None
    assert ws.get_model_once() == "gemini-pro"
    assert ws.get_model_override() is None


def test_model_global_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    reg = _registry()
    ws = SessionWorkspace.create("mod-global")
    result = handle_model_command(
        "/model gemini-flash --global",
        override=None,
        workspace=ws,
        registry=reg,
    )
    assert result.override == "gemini-flash"
    path = tmp_path / "models.yaml"
    assert path.is_file()
    assert "gemini-flash" in path.read_text()


def test_model_policy_allow_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setenv("OPENAI_API_KEY", "o-test")
    reg = _registry(model_policy={"allow": ["gemini-flash"]})
    assert model_policy_allow(reg) == ["gemini-flash"]
    result = handle_model_command(
        "/model gpt-fast",
        override=None,
        registry=reg,
    )
    assert result.override is None
    assert "outside model_policy.allow" in result.message


def test_router_prefers_session_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setenv("OPENAI_API_KEY", "o-test")

    def build(mid: str):
        m = MagicMock()
        m.model_id = mid
        m.provider = "gemini" if mid.startswith("gemini") else "openai"
        m.chat = AsyncMock(
            return_value=MagicMock(
                message=ChatMessage(role="assistant", content="ok"),
                stop_reason="stop",
            )
        )
        return m

    registry = MagicMock()
    registry.roles = {"tool_calling": ["gemini-flash", "gpt-fast"]}
    registry.available_models.return_value = [
        MagicMock(id="gemini-flash"),
        MagicMock(id="gpt-fast"),
        MagicMock(id="gemini-pro"),
    ]
    registry.build.side_effect = build

    router = ModelRouter(registry)
    router.set_session_override("gpt-fast")
    model, _ = asyncio.run(
        router.chat(
            [ChatMessage(role="user", content="hi")],
            role="tool_calling",
            task_id="t1",
        )
    )
    assert model.model_id == "gpt-fast"
    assert any(h.reason == "session_override" for h in router.history)


def test_router_once_clears_after_success(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setenv("OPENAI_API_KEY", "o-test")
    consumed: list[str] = []

    def build(mid: str):
        m = MagicMock()
        m.model_id = mid
        m.provider = "openai" if mid == "gpt-fast" else "gemini"
        m.chat = AsyncMock(
            return_value=MagicMock(
                message=ChatMessage(role="assistant", content="ok"),
                stop_reason="stop",
            )
        )
        return m

    registry = MagicMock()
    registry.roles = {"tool_calling": ["gemini-flash", "gpt-fast"]}
    registry.available_models.return_value = [
        MagicMock(id="gemini-flash"),
        MagicMock(id="gpt-fast"),
    ]
    registry.build.side_effect = build

    router = ModelRouter(registry)
    router.set_once_override("gpt-fast")
    router.on_once_consumed = lambda mid: consumed.append(mid)
    model, _ = asyncio.run(
        router.chat(
            [ChatMessage(role="user", content="hi")],
            role="tool_calling",
            task_id="t1",
        )
    )
    assert model.model_id == "gpt-fast"
    assert router.once_override is None
    assert consumed == ["gpt-fast"]


def test_persist_global_model_writes_roles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    reg = _registry()
    path = persist_global_model("gemini-flash", reg)
    text = path.read_text()
    assert "gemini-flash" in text
    assert "tool_calling" in text


def test_resolve_natural_antigravity_and_codex(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "kageha.models.gemini_cli.gemini_cli_available", lambda: True
    )
    monkeypatch.setattr(
        "kageha.models.gemini_cli.antigravity_session_present", lambda: True
    )
    reg = _registry()
    assert resolve_model_id("antigravity 3.1 pro", reg) == "antigravity"
    assert resolve_model_id("3.1-pro", reg) == "antigravity"
    assert resolve_model_id("antigravity-3.1-pro", reg) == "antigravity"
    assert resolve_model_id("gemini 3.6 flash", reg) == "gemini-flash"
    assert resolve_model_id("antigravity 3.6 flash", reg) == "antigravity-flash"
    assert resolve_model_id("antigravity-flash", reg) == "antigravity-flash"
    assert resolve_model_id("antigravity-3.6-flash", reg) == "antigravity-flash"
    assert resolve_model_id("3.6-flash", reg) == "antigravity-flash"
    assert resolve_model_id("3-flash", reg) == "antigravity-3-flash"
    assert resolve_model_id("sol", reg) == "gpt-codex"
    assert resolve_model_id("gpt-5.6-sol", reg) == "gpt-codex"
    assert resolve_model_id("codex", reg) == "gpt-codex"


def test_planner_executor_pins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setattr(
        "kageha.models.gemini_cli.gemini_cli_available", lambda: True
    )
    monkeypatch.setattr(
        "kageha.models.gemini_cli.antigravity_session_present", lambda: True
    )
    reg = _registry()
    ws = SessionWorkspace.create("mod-roles")
    ws.set_model_override("gpt-fast")
    result = handle_model_command(
        "/model planner antigravity",
        override="gpt-fast",
        workspace=ws,
        registry=reg,
    )
    assert result.changed
    assert result.override is None
    assert result.role_overrides["planner"] == "antigravity"
    assert ws.get_model_override() is None
    assert ws.get_model_role_overrides()["planner"] == "antigravity"

    result = handle_model_command(
        "/model executor gemini-flash",
        override=None,
        role_overrides=result.role_overrides,
        workspace=ws,
        registry=reg,
    )
    assert result.role_overrides["planner"] == "antigravity"
    assert result.role_overrides["executor"] == "gemini-flash"
    assert "planner=" in result.message or "OK —" in result.message

    expanded = expand_role_overrides(result.role_overrides)
    assert expanded["planning"] == "antigravity"
    assert expanded["tool_calling"] == "gemini-flash"

    result = handle_model_command(
        "/model reset planner",
        override=None,
        role_overrides=result.role_overrides,
        workspace=ws,
        registry=reg,
    )
    assert "planner" not in result.role_overrides
    assert result.role_overrides["executor"] == "gemini-flash"


def test_router_prefers_role_override_over_session(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setenv("OPENAI_API_KEY", "o-test")

    def build(mid: str):
        m = MagicMock()
        m.model_id = mid
        m.provider = "gemini"
        m.chat = AsyncMock(
            return_value=MagicMock(
                message=ChatMessage(role="assistant", content="ok"),
                stop_reason="stop",
            )
        )
        return m

    registry = MagicMock()
    registry.roles = {
        "planning": ["gemini-pro"],
        "tool_calling": ["gemini-flash"],
    }
    registry.available_models.return_value = [
        MagicMock(id="gemini-flash"),
        MagicMock(id="gemini-pro"),
        MagicMock(id="gpt-fast"),
    ]
    registry.build.side_effect = build
    registry.models = {}

    router = ModelRouter(registry)
    router.set_session_override("gpt-fast")
    router.set_role_overrides({"planning": "gemini-pro", "tool_calling": "gemini-flash"})
    plan_model, _ = asyncio.run(
        router.chat(
            [ChatMessage(role="user", content="plan")],
            role="planning",
            task_id="t-plan",
        )
    )
    exec_model, _ = asyncio.run(
        router.chat(
            [ChatMessage(role="user", content="exec")],
            role="tool_calling",
            task_id="t-exec",
        )
    )
    assert plan_model.model_id == "gemini-pro"
    assert exec_model.model_id == "gemini-flash"
    assert any(h.reason == "role_override" for h in router.history)
