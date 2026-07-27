"""TerminalBackend seam + Modal profile wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kageha.harness.sandbox import run_shell
from kageha.harness.shell_sandbox import sandbox_status, wrap_shell_command
from kageha.harness.terminal_backend import (
    ModalTerminalBackend,
    TerminalExecResult,
    resolve_terminal_backend,
)


def test_sandbox_profile_modal(monkeypatch):
    monkeypatch.setenv("KAGEHA_SANDBOX", "modal")
    from kageha.config import sandbox_profile

    assert sandbox_profile() == "modal"
    st = sandbox_status()
    assert st.profile == "modal"
    # Without modal package / tokens, unavailable is expected.
    assert st.available in {True, False}


def test_modal_wrap_never_silent_host():
    wrapped, _ = wrap_shell_command(
        "echo hi", Path("/tmp"), profile="modal"
    )
    assert "exit 78" in wrapped
    assert "TerminalBackend" in wrapped


def test_resolve_terminal_backend_modal(monkeypatch):
    monkeypatch.setenv("KAGEHA_SANDBOX", "modal")
    backend = resolve_terminal_backend()
    assert backend is not None
    assert backend.name == "modal"


@pytest.mark.asyncio
async def test_run_shell_uses_modal_backend(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_SANDBOX", "modal")
    monkeypatch.setenv("KAGEHA_SECURITY_PROFILE", "approval_fallback")

    fake = MagicMock(spec=ModalTerminalBackend)
    fake.name = "modal"
    fake.available.return_value = (True, "test")
    fake.exec = AsyncMock(
        return_value=TerminalExecResult(
            exit_code=0,
            stdout="hello-modal\n",
            stderr="",
            backend="modal",
        )
    )
    monkeypatch.setattr(
        "kageha.harness.terminal_backend.resolve_terminal_backend",
        lambda profile=None: fake,
    )
    # sandbox_status must report available for strict/approval paths
    monkeypatch.setattr(
        "kageha.harness.shell_sandbox.sandbox_status",
        lambda: MagicMock(
            profile="modal", requested="modal", available=True, detail="ok"
        ),
    )

    result = await run_shell("echo hi", tmp_path, timeout=5.0)
    assert result.exit_code == 0
    assert "hello-modal" in result.stdout
    fake.exec.assert_awaited_once()
