"""Channel + sandbox extras: Slack HITL, SSH sync, noVNC, Matrix E2EE, IRC/MM/Teams, setup."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kageha.channels.irc import IRCChannel, parse_irc_allowlist
from kageha.channels.mattermost import MattermostChannel, parse_mattermost_allowlist
from kageha.channels.slack import SlackChannel, parse_slack_allowlist
from kageha.channels.teams import TeamsChannel, parse_teams_allowlist
from kageha.harness.browser_sandbox import (
    DockerBrowserSession,
    browser_docker_image,
    browser_novnc_enabled,
    browser_sandbox_status,
)
from kageha.harness.shell_sandbox import wrap_shell_command


# --- Slack HITL ---


def test_slack_allowlist_and_hitl_helpers(monkeypatch):
    monkeypatch.setenv("SLACK_ALLOWED_USERS", "U1,U2")
    assert parse_slack_allowlist() == {"U1", "U2"}
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp")
    monkeypatch.setenv("SLACK_ALLOW_ALL_USERS", "1")
    ch = SlackChannel()
    assert ch.available
    assert ch.consume_if_pending_human("C", "U", "y") is False


@pytest.mark.asyncio
async def test_slack_pending_human_resolves(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp")
    monkeypatch.setenv("SLACK_ALLOW_ALL_USERS", "1")
    ch = SlackChannel(approval_timeout_s=2.0)
    sent: list[str] = []

    async def say(text: str) -> None:
        sent.append(text)

    async def reply_later() -> None:
        await asyncio.sleep(0.05)
        assert ch.consume_if_pending_human("C1", "U1", "y") is True

    task = asyncio.create_task(reply_later())
    answer = await ch.wait_for_human("C1", "U1", "Approve?", say)
    await task
    assert answer == "y"
    assert sent and "Approve?" in sent[0]


# --- SSH bidirectional sync ---


def test_ssh_bidirectional_and_modes(tmp_path: Path, monkeypatch):
    import shutil

    if not shutil.which("ssh"):
        pytest.skip("ssh not installed")
    monkeypatch.setenv("KAGEHA_SANDBOX", "ssh")
    monkeypatch.setenv("KAGEHA_SANDBOX_SSH_HOST", "remote.example")
    monkeypatch.delenv("KAGEHA_SANDBOX_SSH_SYNC", raising=False)
    cmd, _ = wrap_shell_command("echo hi", tmp_path)
    assert " && " in cmd
    assert cmd.count("tar -c") >= 2

    monkeypatch.setenv("KAGEHA_SANDBOX_SSH_SYNC", "push")
    cmd_push, _ = wrap_shell_command("echo hi", tmp_path)
    assert cmd_push.count("tar -c") == 1

    monkeypatch.setenv("KAGEHA_SANDBOX_SSH_SYNC", "none")
    cmd_none, _ = wrap_shell_command("echo hi", tmp_path)
    assert "tar -c" not in cmd_none
    assert "ssh" in cmd_none


# --- Browser noVNC / baked image ---


def test_browser_novnc_status_and_defaults(monkeypatch):
    monkeypatch.delenv("KAGEHA_BROWSER_DOCKER_IMAGE", raising=False)
    monkeypatch.delenv("KAGEHA_SANDBOX_BROWSER_IMAGE", raising=False)
    assert browser_docker_image() == "kageha-browser:local"
    monkeypatch.delenv("KAGEHA_BROWSER_NOVNC", raising=False)
    assert browser_novnc_enabled() is True
    st = browser_sandbox_status()
    assert st["novnc"] is True
    assert "kageha-browser" in str(st["image"])


def test_docker_browser_session_carries_novnc_fields():
    sess = DockerBrowserSession(
        container_id="c1",
        cdp_endpoint="http://127.0.0.1:9222",
        host_port=9222,
        novnc_url="http://127.0.0.1:6080/vnc.html",
        novnc_port=6080,
        novnc_password="secret",
    )
    assert sess.novnc_url.endswith("vnc.html")
    assert sess.novnc_password == "secret"


def test_browser_dockerfile_exists():
    root = Path(__file__).resolve().parents[1]
    assert (root / "docker" / "browser" / "Dockerfile").is_file()
    assert (root / "docker" / "browser" / "entrypoint.sh").is_file()


# --- Matrix E2EE dispatch ---


@pytest.mark.asyncio
async def test_matrix_e2ee_dispatch_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MATRIX_E2EE", "1")
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.example")
    monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("MATRIX_USER_ID", "@bot:example.org")
    monkeypatch.setenv("MATRIX_ALLOW_ALL_USERS", "1")
    from kageha.channels.matrix import MatrixChannel

    ch = MatrixChannel()
    # Without matrix-nio installed, E2EE path should raise ImportError from nio
    with pytest.raises((ImportError, RuntimeError)):
        await asyncio.wait_for(ch.poll_and_run(), timeout=0.5)


def test_matrix_e2ee_channel_available(monkeypatch):
    from kageha.channels.matrix_e2ee import MatrixE2EEChannel

    monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.example")
    monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("MATRIX_USER_ID", "@bot:example.org")
    ch = MatrixE2EEChannel()
    assert ch.available is True


# --- IRC / Mattermost / Teams ---


def test_irc_allowlist_and_available(monkeypatch):
    monkeypatch.delenv("IRC_ALLOW_ALL_USERS", raising=False)
    monkeypatch.setenv("IRC_ALLOWED_USERS", "Alice")
    assert parse_irc_allowlist() == {"alice"}
    monkeypatch.setenv("IRC_HOST", "irc.example")
    monkeypatch.setenv("IRC_NICK", "kageha")
    monkeypatch.setenv("IRC_CHANNELS", "#ai")
    monkeypatch.setenv("IRC_ALLOW_ALL_USERS", "1")
    ch = IRCChannel()
    assert ch.available
    assert ch.consume_if_pending_human("#ai", "y") is False


def test_mattermost_allowlist_and_available(monkeypatch):
    monkeypatch.setenv("MATTERMOST_ALLOWED_USERS", "uid1,uid2")
    assert parse_mattermost_allowlist() == {"uid1", "uid2"}
    monkeypatch.setenv("MATTERMOST_URL", "https://mm.example")
    monkeypatch.setenv("MATTERMOST_TOKEN", "tok")
    monkeypatch.setenv("MATTERMOST_CHANNEL_IDS", "chan1")
    monkeypatch.setenv("MATTERMOST_ALLOW_ALL_USERS", "1")
    ch = MattermostChannel()
    assert ch.available


def test_teams_allowlist_and_inbound(monkeypatch):
    monkeypatch.setenv("TEAMS_ALLOWED_USERS", "a@Ex.Com")
    assert parse_teams_allowlist() == {"a@ex.com"}
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://outlook.office.com/webhook/x")
    monkeypatch.setenv("TEAMS_ALLOW_ALL_USERS", "1")
    ch = TeamsChannel()
    assert ch.available

    async def _run() -> None:
        await ch.handle_inbound("user@ex.com", "hello", external_id="m1")
        identity, text = await asyncio.wait_for(ch._inbound.get(), timeout=1.0)
        assert identity == "user@ex.com"
        assert text == "hello"

    asyncio.run(_run())


# --- Setup wizard + CLI wiring ---


def test_setup_wizard_importable():
    from kageha.setup_wizard import run_setup_wizard

    assert callable(run_setup_wizard)


def test_cli_lists_new_commands():
    from kageha.cli import app

    runner = CliRunner()
    for cmd in ("irc", "mattermost", "teams", "setup", "slack", "matrix"):
        result = runner.invoke(app, [cmd, "--help"])
        assert result.exit_code == 0, f"{cmd}: {result.stdout}\n{result.stderr}"


def test_root_doctor_is_runtime_doctor_and_model_doctor_remains_nested():
    from kageha.cli import app

    runner = CliRunner()
    root = runner.invoke(app, ["doctor", "--help"])
    assert root.exit_code == 0
    assert "--deep" in root.stdout
    assert "--model" not in root.stdout
    models = runner.invoke(app, ["models", "doctor", "--help"])
    assert models.exit_code == 0
    assert "--model" in models.stdout


def test_cli_irc_fails_closed_without_env(monkeypatch):
    monkeypatch.delenv("IRC_HOST", raising=False)
    monkeypatch.delenv("IRC_NICK", raising=False)
    monkeypatch.delenv("IRC_CHANNELS", raising=False)
    from kageha.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["irc"])
    assert result.exit_code == 1
    assert "IRC_HOST" in (result.stdout + result.stderr)


def test_entry_points_register_new_channels():
    from importlib.metadata import entry_points

    eps = entry_points()
    group = eps.select(group="kageha.channels") if hasattr(eps, "select") else eps.get(
        "kageha.channels", []
    )
    ids = {ep.name for ep in group}
    assert {"irc", "mattermost", "teams", "slack", "matrix"} <= ids
