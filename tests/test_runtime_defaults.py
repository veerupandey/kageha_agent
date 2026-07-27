"""Contracts for the single journaled runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kageha.app_server import AppServer
from kageha.config import security_profile
from kageha.loop.controller import LoopController


def test_security_defaults_to_strict(monkeypatch):
    monkeypatch.delenv("KAGEHA_SECURITY_PROFILE", raising=False)
    assert security_profile() == "strict"


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
