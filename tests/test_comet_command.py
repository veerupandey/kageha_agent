from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kageha.chat import comet


@pytest.mark.asyncio
async def test_comet_reports_existing_cdp(monkeypatch) -> None:
    monkeypatch.setenv("KAGEHA_COMET_CDP", "http://127.0.0.1:9333")
    probe = AsyncMock(return_value="Chrome/150")
    launch = AsyncMock()
    monkeypatch.setattr(comet, "_probe_cdp", probe)
    monkeypatch.setattr(comet, "_launch_comet_detached", launch)
    monkeypatch.setattr(comet, "_comet_process_running", AsyncMock(return_value=False))

    message = await comet.ensure_comet()

    assert "Comet ready" in message
    assert "9333" in message
    assert not launch.await_count
    assert comet.os.environ["KAGEHA_BROWSER_MODE"] == "comet"


@pytest.mark.asyncio
async def test_comet_launches_binary_when_not_running(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KAGEHA_COMET_CDP", "http://127.0.0.1:9222")
    monkeypatch.setattr(comet.platform, "system", lambda: "Darwin")
    binary = tmp_path / "Comet"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr(comet, "_comet_binary", lambda: binary)
    monkeypatch.setattr(comet, "_comet_process_running", AsyncMock(return_value=False))
    probe = AsyncMock(side_effect=[None, "Chrome/150"])
    process = SimpleNamespace(returncode=None)
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(comet, "_probe_cdp", probe)
    monkeypatch.setattr(comet.asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(comet.asyncio, "sleep", AsyncMock())

    message = await comet.ensure_comet(timeout_s=0.25)

    assert "Comet started" in message
    command = launch.await_args.args
    assert command[0] == str(binary)
    assert "--remote-debugging-port=9222" in command
    assert "--remote-allow-origins=*" in command
    kwargs = launch.await_args.kwargs
    assert kwargs.get("stdin") == comet.asyncio.subprocess.DEVNULL
    assert kwargs.get("start_new_session") is True


@pytest.mark.asyncio
async def test_comet_restarts_running_instance_without_cdp(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KAGEHA_COMET_CDP", "http://127.0.0.1:9222")
    monkeypatch.setattr(comet.platform, "system", lambda: "Darwin")
    binary = tmp_path / "Comet"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr(comet, "_comet_binary", lambda: binary)
    monkeypatch.setattr(comet, "_comet_process_running", AsyncMock(return_value=True))
    quit_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(comet, "_quit_comet", quit_mock)
    probe = AsyncMock(side_effect=[None, "Chrome/150"])
    process = SimpleNamespace(returncode=None)
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(comet, "_probe_cdp", probe)
    monkeypatch.setattr(comet.asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(comet.asyncio, "sleep", AsyncMock())

    message = await comet.ensure_comet(timeout_s=0.25)

    assert quit_mock.await_count == 1
    assert "Comet restarted" in message
    assert "--remote-debugging-port=9222" in launch.await_args.args


@pytest.mark.asyncio
async def test_comet_status_does_not_launch(monkeypatch) -> None:
    monkeypatch.setenv("KAGEHA_COMET_CDP", "http://127.0.0.1:9222")
    monkeypatch.setattr(comet, "_probe_cdp", AsyncMock(return_value=None))
    launch = AsyncMock()
    monkeypatch.setattr(comet, "_launch_comet_detached", launch)

    handled, message = await comet.handle_comet_command("/comet status")

    assert handled
    assert "not reachable" in message
    assert not launch.await_count


@pytest.mark.asyncio
async def test_comet_command_rejects_unknown_action() -> None:
    handled, message = await comet.handle_comet_command("/comet stop now")

    assert handled
    assert message == "Usage: /comet [start|status]"
