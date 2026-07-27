from __future__ import annotations

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
    monkeypatch.setattr(comet.asyncio, "create_subprocess_exec", launch)

    message = await comet.ensure_comet()

    assert "Comet ready" in message
    assert "9333" in message
    assert not launch.await_count
    assert comet.os.environ["KAGEHA_BROWSER_MODE"] == "comet"


@pytest.mark.asyncio
async def test_comet_launches_then_waits_for_cdp(monkeypatch) -> None:
    monkeypatch.setenv("KAGEHA_COMET_CDP", "http://127.0.0.1:9222")
    monkeypatch.setattr(comet.platform, "system", lambda: "Darwin")
    probe = AsyncMock(side_effect=[None, "Chrome/150"])
    process = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(return_value=(b"", b"")),
    )
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(comet, "_probe_cdp", probe)
    monkeypatch.setattr(comet.asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(comet.asyncio, "sleep", AsyncMock())

    message = await comet.ensure_comet(timeout_s=0.25)

    assert "Comet started" in message
    command = launch.await_args.args
    assert command[:4] == ("open", "-na", "Comet", "--args")
    assert "--remote-debugging-port=9222" in command


@pytest.mark.asyncio
async def test_comet_status_does_not_launch(monkeypatch) -> None:
    monkeypatch.setenv("KAGEHA_COMET_CDP", "http://127.0.0.1:9222")
    monkeypatch.setattr(comet, "_probe_cdp", AsyncMock(return_value=None))
    launch = AsyncMock()
    monkeypatch.setattr(comet.asyncio, "create_subprocess_exec", launch)

    handled, message = await comet.handle_comet_command("/comet status")

    assert handled
    assert "not reachable" in message
    assert not launch.await_count


@pytest.mark.asyncio
async def test_comet_command_rejects_unknown_action() -> None:
    handled, message = await comet.handle_comet_command("/comet stop now")

    assert handled
    assert message == "Usage: /comet [start|status]"
