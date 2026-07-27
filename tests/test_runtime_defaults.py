"""Contracts for the single journaled runtime."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from kageha.app_server import AppServer
from kageha.config import security_profile
from kageha.loop.controller import LoopController


def test_security_defaults_to_approval_fallback(monkeypatch):
    monkeypatch.delenv("KAGEHA_SECURITY_PROFILE", raising=False)
    assert security_profile() == "approval_fallback"
    assert security_profile("permissive") == "approval_fallback"
    assert security_profile("strict") == "strict"


def test_apply_sandbox_cli(monkeypatch):
    from kageha.config import apply_sandbox_cli, sandbox_profile

    monkeypatch.delenv("KAGEHA_SANDBOX", raising=False)
    assert apply_sandbox_cli(None) == sandbox_profile()
    assert apply_sandbox_cli("docker") == "docker"
    assert os.environ.get("KAGEHA_SANDBOX") == "docker"
    assert apply_sandbox_cli("container") == "docker"
    assert apply_sandbox_cli("off") == "off"
    with pytest.raises(ValueError, match="sandbox must be"):
        apply_sandbox_cli("nope")


def test_core_loop_cannot_start_unjournaled_session():
    controller = LoopController(live=False)
    with pytest.raises(RuntimeError, match="AgentRuntime"):
        asyncio.run(controller.run("do work"))


def test_app_server_thread_start():
    async def run() -> dict:
        server = AppServer()
        try:
            return await server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "thread/start",
                    "params": {"thread_id": "t1"},
                }
            )
        finally:
            server.close()

    response = asyncio.run(run())
    assert "error" not in response
    assert "runtime" not in response.get("result", {})


def test_session_workspace_uses_sessions(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    from kageha.harness.sandbox import SessionWorkspace

    ws = SessionWorkspace.create("s1")
    assert ws.root == tmp_path / "sessions" / "s1"
    assert ws.root.is_dir()
