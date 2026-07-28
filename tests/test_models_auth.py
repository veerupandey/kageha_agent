"""Tests for subscription model auth store + setup step (no real tokens printed)."""

from __future__ import annotations

import json
import stat

import pytest
from typer.testing import CliRunner


@pytest.fixture
def auth_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    return tmp_path


def test_import_chatgpt_from_mock_codex(auth_home, monkeypatch, tmp_path):
    from kageha.models.auth_store import import_chatgpt_codex, load_profile

    codex = tmp_path / "auth.json"
    codex.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "account_id": "acct-1",
                    "id_token": "id-secret",
                },
                "last_refresh": "2026-07-25T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    prof = import_chatgpt_codex(codex)
    assert prof.provider == "chatgpt"
    assert prof.account_id == "acct-1"
    pub = prof.as_public_dict()
    assert pub["has_access_token"] is True
    assert "access-secret" not in json.dumps(pub)

    stored = auth_home / "auth" / "chatgpt.json"
    assert stored.is_file()
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    assert load_profile("openai-codex") is not None
    # file contains token but public API must not expose it in as_public_dict
    alias = load_profile("openai-codex")
    assert alias is not None
    assert alias.access_token == "access-secret"


def test_import_gemini_cli_and_antigravity_alias(auth_home, tmp_path):
    from kageha.models.auth_store import import_gemini_cli, load_profile

    gem = tmp_path / "oauth_creds.json"
    gem.write_text(
        json.dumps(
            {
                "access_token": "g-access",
                "refresh_token": "g-refresh",
                "expiry_date": 9999999999999,
                "scope": "openid",
                "token_type": "Bearer",
            }
        ),
        encoding="utf-8",
    )
    prof = import_gemini_cli(gem)
    assert prof.provider == "gemini-cli"
    assert load_profile("antigravity") is not None
    assert load_profile("antigravity").access_token == "g-access"


def test_resolve_credentials_prefers_env_then_auth(auth_home, monkeypatch, tmp_path):
    from kageha.models.auth_store import import_chatgpt_codex
    from kageha.models.registry import ModelRegistry, ProviderConfig

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_CODEX_OAUTH", raising=False)
    codex = tmp_path / "auth.json"
    codex.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "tok-from-auth",
                    "refresh_token": "r",
                    "account_id": "acct",
                },
                "last_refresh": "2099-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    import_chatgpt_codex(codex)
    reg = ModelRegistry(providers={}, models={}, roles={})
    pc = ProviderConfig(
        name="openai-codex",
        protocol="openai_compat",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_CODEX_OAUTH",
    )
    key, headers = reg._resolve_credentials(pc)
    assert key == "tok-from-auth"
    assert headers.get("ChatGPT-Account-ID") == "acct"

    monkeypatch.setenv("OPENAI_CODEX_OAUTH", "env-key-wins")
    key2, headers2 = reg._resolve_credentials(pc)
    assert key2 == "env-key-wins"
    assert headers2 == {}


def test_lazy_import_codex_from_home(auth_home, monkeypatch, tmp_path):
    """openai-codex resolves via ~/.codex/auth.json without prior import."""
    from kageha.models.auth_store import resolve_access_token

    monkeypatch.setattr(
        "kageha.models.auth_store.Path.home",
        lambda: tmp_path,
    )
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "auth.json").write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "lazy-tok",
                    "refresh_token": "r",
                    "account_id": "acct-lazy",
                },
                "last_refresh": "2099-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    tok, headers = resolve_access_token("openai-codex")
    assert tok == "lazy-tok"
    assert headers.get("ChatGPT-Account-ID") == "acct-lazy"


def test_model_command_hint_mentions_import(auth_home, monkeypatch):
    from kageha.chat.model_commands import handle_model_command
    from kageha.models.registry import ModelConfig, ModelRegistry, ProviderConfig

    monkeypatch.delenv("OPENAI_CODEX_OAUTH", raising=False)
    reg = ModelRegistry(
        providers={
            "openai-codex": ProviderConfig(
                name="openai-codex",
                protocol="openai_compat",
                base_url="https://api.openai.com/v1",
                api_key_env="OPENAI_CODEX_OAUTH",
            )
        },
        models={
            "gpt-codex": ModelConfig(
                id="gpt-codex",
                provider="openai-codex",
                model="gpt-5.6-sol",
            )
        },
        roles={},
    )
    # Prevent lazy import from real ~/.codex during this unit test
    monkeypatch.setattr(
        "kageha.models.auth_store.Path.home",
        lambda: auth_home,
    )
    result = handle_model_command(
        "/model gpt-codex",
        override=None,
        registry=reg,
    )
    assert result.handled
    assert "credentials are missing" in (result.message or "")
    assert "models auth import chatgpt" in (result.message or "")


def test_probe_cursor_false(auth_home):
    from kageha.models.auth_store import probe_local_logins

    probe = probe_local_logins()
    assert probe["cursor_oauth"] is False
    assert "Cursor" in probe["note_cursor"]


def test_gemini_api_ignores_oauth_tokens(auth_home, monkeypatch, tmp_path):
    """Public Gemini models must not use Antigravity OAuth as API keys."""
    from kageha.models.auth_store import import_gemini_cli
    from kageha.models.registry import ModelRegistry, ProviderConfig

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    gem = tmp_path / "oauth_creds.json"
    gem.write_text(
        json.dumps(
            {
                "access_token": "oauth-not-an-api-key",
                "refresh_token": "r",
                "expiry_date": 9999999999999,
            }
        ),
        encoding="utf-8",
    )
    import_gemini_cli(gem)
    reg = ModelRegistry(providers={}, models={}, roles={})
    pc = ProviderConfig(
        name="gemini",
        protocol="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key_env="GEMINI_API_KEY",
    )
    key, _ = reg._resolve_credentials(pc)
    assert key == ""


def test_antigravity_cli_provider_ready(monkeypatch):
    from kageha.models.registry import ModelRegistry, ProviderConfig

    monkeypatch.setattr(
        "kageha.models.gemini_cli.gemini_cli_available", lambda: True
    )
    monkeypatch.setattr(
        "kageha.models.gemini_cli.antigravity_session_present", lambda: True
    )
    reg = ModelRegistry(providers={}, models={}, roles={})
    pc = ProviderConfig(
        name="antigravity",
        protocol="gemini_cli",
        base_url="",
        api_key_env="ANTIGRAVITY_CLI",
    )
    assert reg._provider_ready(pc) is True
    assert reg.auth_source  # method exists
    key, _ = reg._resolve_credentials(pc)
    assert key == "gemini-cli-session"


def test_auth_cli_import_and_logout(auth_home, tmp_path):
    from kageha.models.auth_cli import auth_status_payload, run_import, run_logout

    src = tmp_path / "oauth_creds.json"
    src.write_text(
        json.dumps({"access_token": "x", "refresh_token": "y", "expiry_date": 9e12}),
        encoding="utf-8",
    )
    run_import("antigravity", path=src)
    payload = auth_status_payload()
    ids = {p["provider"] for p in payload["profiles"]}
    assert "gemini-cli" in ids
    assert "antigravity" in ids
    assert run_logout("gemini-cli") is True
    assert load_empty()


def load_empty():
    from kageha.models.auth_store import list_profiles

    return list_profiles() == []


def test_cli_models_auth_help():
    from kageha.cli import app

    runner = CliRunner()
    for args in (
        ["models", "auth", "--help"],
        ["models", "auth", "probe"],
        ["models", "auth", "list"],
    ):
        result = runner.invoke(app, list(args))
        assert result.exit_code == 0, result.stdout + result.stderr
        # never leak obvious token patterns from our mocks in help/probe
        assert "sk-" not in result.stdout
