"""AppServer short-circuits micro greetings without AgentRuntime."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

from kageha.app_server import AppServer
from kageha.harness.sandbox import SessionWorkspace


def test_thread_turn_hey_skips_runtime_submit(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    server = AppServer()
    runtime = MagicMock()
    runtime.submit = MagicMock(
        side_effect=AssertionError("submit must not run for greetings")
    )
    runtime.resume = MagicMock(
        side_effect=AssertionError("resume must not run for greetings")
    )
    server._runtime = runtime

    async def _run():
        resp = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "thread/turn",
                "params": {
                    "thread_id": "web-hey",
                    "message": "hey",
                    "channel_key": "webui",
                    "platform": "webui",
                    "loop_mode": "followup",
                },
            }
        )
        assert "error" not in resp, resp
        result = resp["result"]
        assert result["status"] == "success"
        assert result.get("quick") is True
        assert "here" in result["message"].lower()
        runtime.submit.assert_not_called()
        runtime.resume.assert_not_called()

    asyncio.run(_run())
    server.close()


def test_thread_turn_hey_appends_chat_log_when_session_exists(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ws = SessionWorkspace.create("quick-sess")
    server = AppServer()
    runtime = MagicMock()
    runtime.submit = MagicMock(
        side_effect=AssertionError("submit must not run for greetings")
    )
    runtime.resume = MagicMock(
        side_effect=AssertionError("resume must not run for greetings")
    )
    runtime.store = MagicMock()
    runtime.store.inspect_session = MagicMock(return_value=None)
    server._runtime = runtime
    server.threads["web-hey"] = {"run_id": "quick-sess"}

    def _open_ws(run_id: str):
        assert run_id == "quick-sess"
        return ws

    monkeypatch.setattr(
        "kageha.harness.sandbox.SessionWorkspace.create", _open_ws
    )

    async def _run():
        resp = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "thread/turn",
                "params": {
                    "thread_id": "web-hey",
                    "message": "hi",
                    "channel_key": "webui",
                },
            }
        )
        assert "error" not in resp, resp
        result = resp["result"]
        assert result["run_id"] == "quick-sess"
        assert result.get("quick") is True
        runtime.submit.assert_not_called()
        runtime.resume.assert_not_called()

    asyncio.run(_run())
    log_path = Path(ws.root) / "chat.jsonl"
    assert log_path.is_file()
    lines = [json.loads(line) for line in log_path.read_text().splitlines() if line]
    assert lines[0]["role"] == "user"
    assert lines[0]["text"] == "hi"
    assert lines[1]["role"] == "assistant"
    assert "here" in lines[1]["text"].lower()
    server.close()


def test_thread_turn_real_task_still_uses_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    server = AppServer()

    class _Handle:
        session_id = "run-real"
        turn_id = "t1"

        async def result(self):
            return MagicMock(
                run_id="run-real",
                status="success",
                message="done",
                artifacts=[],
            )

    runtime = MagicMock()
    runtime.submit = MagicMock(return_value=_Handle())
    runtime.resume = MagicMock(
        side_effect=AssertionError("resume unexpected")
    )
    server._runtime = runtime
    monkeypatch.setattr(
        "kageha.memory.skills.SkillRegistry.catalog",
        lambda self, limit=40: "",
    )
    monkeypatch.setattr(
        server.memory,
        "apply_explicit_user_action",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        server.memory,
        "apply_natural_correction",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        server.memory,
        "recall",
        lambda *a, **k: MagicMock(render=lambda: ""),
    )
    monkeypatch.setattr(
        server.memory,
        "capture_turn",
        lambda *a, **k: None,
    )

    async def _run():
        resp = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "thread/turn",
                "params": {
                    "thread_id": "web-task",
                    "message": "create a short summary of quantum computing",
                    "channel_key": "webui",
                    "loop_mode": "followup",
                },
            }
        )
        assert "error" not in resp, resp
        assert resp["result"]["run_id"] == "run-real"
        runtime.submit.assert_called_once()
        runtime.resume.assert_not_called()

    asyncio.run(_run())
    server.close()
