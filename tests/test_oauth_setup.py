"""OpenAI Codex + Antigravity OAuth library helpers."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from kageha.cli import app


def test_models_auth_setup_removed():
    runner = CliRunner()
    result = runner.invoke(app, ["models", "auth", "setup", "--help"])
    assert result.exit_code != 0


def test_run_oauth_setup_import_only(tmp_path: Path, monkeypatch):
    from kageha.models import oauth_setup as mod

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))

    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "auth.json").write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "codex-access",
                    "refresh_token": "codex-refresh",
                    "account_id": "acct-1",
                },
                "auth_mode": "chatgpt",
            }
        ),
        encoding="utf-8",
    )

    gemini_dir = tmp_path / ".gemini"
    gemini_dir.mkdir()
    (gemini_dir / "oauth_creds.json").write_text(
        json.dumps(
            {
                "access_token": "gem-access",
                "refresh_token": "gem-refresh",
                "expiry_date": 9999999999999,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(mod.Path, "home", lambda: tmp_path)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(mod.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(mod, "_codex_status_ok", lambda: True)

    result = mod.run_oauth_setup(target="both", launch_login=False)
    assert result["ok"] is True
    assert "chatgpt" in result["imported"]
    assert "antigravity" in result["imported"]
    assert (home / "auth" / "chatgpt.json").is_file()
    assert (home / "auth" / "antigravity.json").is_file()
    dumped = json.dumps(result)
    assert "codex-access" not in dumped
    assert "gem-access" not in dumped


def test_setup_codex_missing_cli(monkeypatch, tmp_path):
    from kageha.models import oauth_setup as mod

    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    monkeypatch.setattr(
        mod,
        "detect_tools",
        lambda: {
            "has_codex_cli": False,
            "has_agy_cli": False,
            "has_gemini_cli": False,
            "chatgpt_codex_cli": False,
            "gemini_cli_oauth": False,
            "antigravity_data_dir": False,
            "codex_bin": "",
            "agy_bin": "",
            "gemini_bin": "",
            "note_cursor": "",
        },
    )
    result = mod.setup_codex_oauth(launch_login=True)
    assert result["ok"] is False
    assert result["error"] == "codex_cli_missing"


def test_setup_wizard_menu_includes_oauth():
    from kageha.setup_wizard import _MENU

    keys = {k for k, _ in _MENU}
    assert "codex_oauth" in keys
    assert "antigravity_oauth" in keys
    assert "both_oauth" in keys
