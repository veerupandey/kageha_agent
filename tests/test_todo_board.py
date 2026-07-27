"""Live Build todo board: parse, runtime payload, WebUI SSE/session wire."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kageha.app_server import AppServer
from kageha.harness.sandbox import SessionWorkspace
from kageha.loop.controller import LoopController
from kageha.loop.todo_board import (
    board_log_lines,
    parse_todo_file,
    parse_todo_markdown,
)
from kageha.memory.service import reset_memory_service_for_tests
from kageha.obs.events import EventLog
from kageha.runtime.engine import _todo_board_payload
from kageha.webui.server import WebUIApp, _stream_event_view, _stream_frame


def test_parse_todo_markdown_items_and_progress():
    board = parse_todo_markdown(
        "# Plan\n\n"
        "- [x] `p1` Scan network\n"
        "- [ ] p2: Write report\n"
        "- not a checkbox\n"
        "- [X] g1 Done goal\n",
        label="todos",
    )
    assert board["done"] == 2
    assert board["total"] == 3
    assert board["items"][0] == {
        "id": "p1",
        "text": "Scan network",
        "done": True,
    }
    assert board["items"][1] == {
        "id": "p2",
        "text": "Write report",
        "done": False,
    }
    assert board["items"][2]["id"] == "g1"
    assert board["items"][2]["done"] is True


def test_parse_todo_file_missing_or_empty(tmp_path: Path):
    assert parse_todo_file(tmp_path / "missing.md") is None
    empty = tmp_path / "empty.md"
    empty.write_text("# Plan\n\nNo checkboxes yet.\n", encoding="utf-8")
    assert parse_todo_file(empty) is None


def test_board_log_lines_match_cli_format():
    board = parse_todo_markdown(
        "- [x] p1: Done\n- [ ] p2: Open\n",
        label="todos",
    )
    text = board_log_lines(board)
    assert "todos: 1/2" in text
    assert "[x] p1: Done" in text
    assert "[ ] p2: Open" in text


def test_controller_emits_structured_todo_board(tmp_path: Path):
    emitted: list[tuple[str, dict]] = []
    controller = LoopController(
        live=True,
        log_handler=lambda _m: None,
        event_sink=lambda kind, data: emitted.append((kind, data)),
    )
    ws = SessionWorkspace(run_id="tb1", root=tmp_path / "tb1")
    ws.root.mkdir(parents=True)
    ws.write_text(
        "todo.md",
        "# Plan\n\n- [x] p1: Done already\n- [ ] p2: Still open\n",
    )
    events = EventLog(sink=controller.event_sink)
    controller._log_todo_board(ws, label="todos", events=events)
    assert any(kind == "todo_board" for kind, _ in emitted)
    board = next(data for kind, data in emitted if kind == "todo_board")
    assert board["done"] == 1
    assert board["total"] == 2
    assert board["items"][1]["done"] is False


def test_refresh_todo_board_if_changed_dedupes(tmp_path: Path):
    emitted: list[tuple[str, dict]] = []
    controller = LoopController(
        live=True,
        log_handler=lambda _m: None,
        event_sink=lambda kind, data: emitted.append((kind, data)),
    )
    ws = SessionWorkspace(run_id="tb-refresh", root=tmp_path / "tb-refresh")
    ws.root.mkdir(parents=True)
    ws.write_text(
        "todo.md",
        "# Plan\n\n- [x] p1: Done\n- [ ] p2: Open\n",
    )
    events = EventLog(sink=controller.event_sink)
    assert controller._refresh_todo_board_if_changed(
        ws, label="todos", events=events
    )
    assert controller._refresh_todo_board_if_changed(
        ws, label="todos", events=events
    ) is False
    assert sum(1 for kind, _ in emitted if kind == "todo_board") == 1

    ws.write_text(
        "todo.md",
        "# Plan\n\n- [x] p1: Done\n- [x] p2: Open\n",
    )
    assert controller._refresh_todo_board_if_changed(
        ws, label="todos", events=events
    )
    boards = [data for kind, data in emitted if kind == "todo_board"]
    assert len(boards) == 2
    assert boards[-1]["done"] == 2


def test_tool_result_touches_todo_detects_write_file():
    from kageha.models.base import ChatMessage

    controller = LoopController(live=True, log_handler=lambda _m: None)
    assert controller._tool_result_touches_todo(
        ChatMessage(role="tool", name="todo_write", content="Updated todo.md")
    )
    assert controller._tool_result_touches_todo(
        ChatMessage(
            role="tool",
            name="write_file",
            content="Wrote 120 bytes to todo.md",
        )
    )
    assert controller._tool_result_touches_todo(
        ChatMessage(role="tool", name="edit_file", content="Edited todo.md")
    )
    assert not controller._tool_result_touches_todo(
        ChatMessage(
            role="tool",
            name="write_file",
            content="Wrote 40 bytes to notes.md",
        )
    )
    assert not controller._tool_result_touches_todo(
        ChatMessage(role="tool", name="web_search", content="ok")
    )


def test_runtime_todo_board_payload_prefers_structured(tmp_path: Path):
    ws = SessionWorkspace(run_id="tb2", root=tmp_path / "tb2")
    ws.root.mkdir(parents=True)
    ws.write_text("todo.md", "- [ ] stale: ignore\n")
    structured = {
        "label": "todos",
        "done": 1,
        "total": 2,
        "items": [
            {"id": "a", "text": "One", "done": True},
            {"id": "b", "text": "Two", "done": False},
        ],
    }
    assert _todo_board_payload(ws, data=structured) == structured
    from_md = _todo_board_payload(
        ws, data={"markdown": "- [x] p1: From markdown\n- [ ] p2: Open\n"}
    )
    assert from_md is not None
    assert from_md["done"] == 1
    assert from_md["total"] == 2


def test_stream_event_view_todo_board_label():
    view = _stream_event_view(
        "todo_board",
        {
            "label": "todos",
            "done": 2,
            "total": 5,
            "items": [
                {"id": "p1", "text": "Scan", "done": True},
                {"id": "p2", "text": "Write", "done": False},
            ],
        },
    )
    assert view["label"] == "Todos 2/5"
    assert view["interesting"] is True
    assert any("[x] p1: Scan" in d for d in view["detail"])
    frame = _stream_frame(
        kind="todo_board",
        payload={
            "label": "todos",
            "done": 2,
            "total": 5,
            "items": [{"id": "p1", "text": "Scan", "done": True}],
            "snapshot": "drop-me",
        },
        sequence=7,
        turn_id="t1",
        session_id="s1",
    )
    assert frame["kind"] == "todo_board"
    assert frame["payload"]["done"] == 2
    assert frame["payload"]["total"] == 5
    assert "items" in frame["payload"]
    assert "snapshot" not in frame["payload"]


@pytest.fixture()
def webui_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WebUIApp:
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    monkeypatch.setenv("KAGEHA_MEMORY_EMBEDDINGS", "off")
    reset_memory_service_for_tests()
    app = WebUIApp(AppServer())
    yield app
    app.close()
    reset_memory_service_for_tests()


def test_session_get_includes_todo_board(webui_app: WebUIApp):
    from kageha.loop.mode_policy import mark_plan_approved
    from kageha.runtime.types import TurnRequest

    sid = "todoboard01"
    ws = SessionWorkspace.create(sid)
    ws.write_text(
        "todo.md",
        "# Plan\n\n- [x] s1: First\n- [ ] s2: Second\n",
    )
    # Design-gated sessions hide the board until Build.
    ws.write_text("plan.md", "# Plan\n\nwaiting\n")
    webui_app.server.runtime.store.start_turn(
        TurnRequest(objective="build todos", session_id=sid, platform="webui"),
        session_id=sid,
    )
    status, data = _call(webui_app, "GET", f"/api/sessions/{sid}")
    assert status == 200
    assert data.get("todo_board") is None

    mark_plan_approved(ws.root)
    status, data = _call(webui_app, "GET", f"/api/sessions/{sid}")
    assert status == 200
    board = data.get("todo_board")
    assert board is not None
    assert board["done"] == 1
    assert board["total"] == 2
    assert board["items"][0]["text"] == "First"


def _call(
    app: WebUIApp,
    method: str,
    path: str,
    *,
    body: dict | None = None,
) -> tuple[int, dict]:
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    status, data, ctype = app.handle(method, path, {}, raw, None)
    assert "json" in ctype
    return status, json.loads(data.decode("utf-8"))
