"""Guided first-run setup wizard."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from kageha.cli import app
from kageha.setup_wizard import run_setup


def test_setup_help():
    runner = CliRunner()
    result = runner.invoke(app, ["setup", "--help"])
    assert result.exit_code == 0
    assert "first-run" in result.stdout.lower() or "setup" in result.stdout.lower()
    top = runner.invoke(app, ["--help"])
    assert "setup" in top.stdout


def test_run_setup_openai_compat_and_packs(tmp_path: Path, monkeypatch):
    home = tmp_path / "khome"
    home.mkdir()
    workspace = tmp_path / "proj"
    workspace.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    monkeypatch.chdir(workspace)

    # surface=both(3), workspace default, provider=compat(5),
    # base_url, api_key_env, api_key, model, provider_name, model_id,
    # browser=y, media=y, fal key, computer skipped on Linux,
    # smoke=n (via smoke_test=False)
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
            # computer skipped non-Darwin
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
