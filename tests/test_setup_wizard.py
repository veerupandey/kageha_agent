"""Guided first-run setup wizard."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from kageha.cli import app
from kageha.setup_wizard import run_setup


def test_setup_help():
    runner = CliRunner()
    result = runner.invoke(app, ["setup", "--help"])
    assert result.exit_code == 0
    assert "setup" in result.stdout.lower()
    top = runner.invoke(app, ["--help"])
    assert "setup" in top.stdout


def test_models_setup_is_alias():
    runner = CliRunner()
    result = runner.invoke(app, ["models", "setup", "--help"])
    assert result.exit_code == 0
    assert "alias" in result.stdout.lower() or "kageha setup" in result.stdout.lower()


def test_run_setup_openai_compat_and_packs(tmp_path: Path, monkeypatch):
    home = tmp_path / "khome"
    home.mkdir()
    workspace = tmp_path / "proj"
    workspace.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    monkeypatch.chdir(workspace)

    # surface=both(3), workspace default, provider=compat(5),
    # base_url, api_key_env, api_key, model, provider_name, model_id,
    # browser=y, media=y, fal key, computer=n, smoke=n (via smoke_test=False)
    answers = iter(
        [
            "3",  # both
            str(workspace),
            "5",  # OpenAI-compatible
            "https://example.com/v1",
            "OPENAI_API_KEY",
            "sk-test-compat",
            "gpt-test",
            "mycompat",
            "compat-local",
            "y",  # browser
            "y",  # media
            "fal-test-key",
            "n",  # computer
        ]
    )
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(answers))

    result = run_setup(smoke_test=False)
    assert result["ok"] is True
    assert result["surface"] == "both"
    assert result["provider"] == "mycompat"
    assert result["model_id"] == "compat-local"
    assert set(result["packs"]) == {"browser", "media"}

    env_text = Path(result["env_path"]).read_text()
    assert "OPENAI_API_KEY=sk-test-compat" in env_text
    assert "FAL_API_KEY=fal-test-key" in env_text
    assert "KAGEHA_TOOL_PACKS=browser,media" in env_text

    yaml_text = (home / "models.yaml").read_text()
    assert "mycompat" in yaml_text
    assert "compat-local" in yaml_text
    assert "https://example.com/v1" in yaml_text
    data = yaml.safe_load(yaml_text)
    assert data.get("session_default_model") == "compat-local"


def test_run_setup_packs_overwrite_clears(tmp_path: Path, monkeypatch):
    home = tmp_path / "khome"
    home.mkdir()
    workspace = tmp_path / "proj"
    workspace.mkdir()
    env = workspace / ".env"
    env.write_text("KAGEHA_TOOL_PACKS=browser,media,computer\nOPENAI_API_KEY=old\n")
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    monkeypatch.chdir(workspace)

    answers = iter(
        [
            "1",  # chat
            str(workspace),
            "2",  # OpenAI API key
            "gpt-4.1-mini",
            "openai-default",
            "sk-new",
            "n",  # browser
            "n",  # media
            "n",  # computer
        ]
    )
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(answers))

    result = run_setup(smoke_test=False)
    assert result["ok"] is True
    assert result["packs"] == []
    env_text = Path(result["env_path"]).read_text()
    assert "KAGEHA_TOOL_PACKS=\n" in env_text or "KAGEHA_TOOL_PACKS=\r\n" in env_text.replace(
        "\r\n", "\n"
    )
    # normalize: key present with empty value
    assert "KAGEHA_TOOL_PACKS=" in env_text
    assert "browser,media" not in env_text.split("KAGEHA_TOOL_PACKS=")[1].splitlines()[0]
    assert "OPENAI_API_KEY=sk-new" in env_text


def test_run_setup_oauth_codex(tmp_path: Path, monkeypatch):
    home = tmp_path / "khome"
    home.mkdir()
    workspace = tmp_path / "proj"
    workspace.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    monkeypatch.chdir(workspace)

    monkeypatch.setattr(
        "kageha.setup_wizard.setup_codex_oauth",
        lambda launch_login=True: {
            "ok": True,
            "imported": True,
            "provider": "chatgpt",
            "profile": {"provider": "chatgpt", "has_access_token": True},
        },
    )
    monkeypatch.setattr(
        "kageha.setup_wizard.detect_tools",
        lambda: {
            "has_codex_cli": True,
            "has_agy_cli": False,
            "has_gemini_cli": False,
            "chatgpt_codex_cli": True,
            "gemini_cli_oauth": False,
            "antigravity_data_dir": False,
        },
    )

    answers = iter(
        [
            "1",  # chat
            str(workspace),
            "6",  # Codex OAuth
            "n",  # browser
            "n",  # media
            "n",  # computer
        ]
    )
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(answers))

    result = run_setup(smoke_test=False)
    assert result["ok"] is True
    assert result["provider"] == "openai-codex"
    assert result["model_id"] == "gpt-codex"
    data = yaml.safe_load((home / "models.yaml").read_text())
    assert data.get("session_default_model") == "gpt-codex"
    assert "KAGEHA_TOOL_PACKS=" in Path(result["env_path"]).read_text()


def test_run_setup_azure(tmp_path: Path, monkeypatch):
    home = tmp_path / "khome"
    home.mkdir()
    workspace = tmp_path / "proj"
    workspace.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    monkeypatch.chdir(workspace)

    answers = iter(
        [
            "1",  # chat
            str(workspace),
            "4",  # Azure
            "https://demo.openai.azure.com",
            "azure-key-123",
            "2024-12-01-preview",
            "gpt-5.4-mini",
            "azure-local",
            "n",  # browser
            "n",  # media
            "n",  # computer
        ]
    )
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(answers))

    result = run_setup(smoke_test=False)
    assert result["ok"] is True
    assert result["provider"] == "azure"
    assert result["model_id"] == "azure-local"
    env_text = Path(result["env_path"]).read_text()
    assert "AZURE_OPENAI_API_KEY=azure-key-123" in env_text
    assert "AZURE_OPENAI_ENDPOINT=https://demo.openai.azure.com" in env_text
    assert "AZURE_OPENAI_DEPLOYMENT=gpt-5.4-mini" in env_text
    yaml_text = (home / "models.yaml").read_text()
    assert "azure" in yaml_text
    assert "azure-local" in yaml_text


def test_run_setup_missing_key_stops(tmp_path: Path, monkeypatch):
    home = tmp_path / "khome"
    home.mkdir()
    workspace = tmp_path / "proj"
    workspace.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(workspace)

    answers = iter(
        [
            "1",
            str(workspace),
            "2",  # OpenAI
            "gpt-4.1-mini",
            "openai-default",
            "",  # empty key — no existing env either
        ]
    )
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(answers))

    result = run_setup(smoke_test=False)
    assert result["ok"] is False
    assert result.get("error") == "missing_api_key"
