"""Minimal tests for Kageha Web UI HTTP routes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from kageha.app_server import AppServer
from kageha.memory.models import MemoryKind, MemoryMutation, MemoryState
from kageha.memory.service import get_memory_service, reset_memory_service_for_tests
from kageha.runtime.types import RunEvent, RunEventKind
from kageha.webui.server import (
    MEMORY_KINDS,
    WebUIApp,
    _emit_text_deltas,
    _sse_bytes,
    _sse_payload_view,
    _stream_event_view,
    _stream_frame,
)


@pytest.fixture()
def webui_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WebUIApp:
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    monkeypatch.setenv("KAGEHA_MEMORY_EMBEDDINGS", "off")
    reset_memory_service_for_tests()
    app = WebUIApp(AppServer())
    yield app
    app.close()
    reset_memory_service_for_tests()


def _call(
    app: WebUIApp,
    method: str,
    path: str,
    *,
    query: dict[str, list[str]] | None = None,
    body: dict | None = None,
    raw_body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    if raw_body is not None:
        raw = raw_body
    elif body is not None:
        raw = json.dumps(body).encode("utf-8")
    else:
        raw = b""
    status, data, ctype = app.handle(method, path, query or {}, raw, headers)
    assert "json" in ctype
    return status, json.loads(data.decode("utf-8"))


def _multipart(
    field: str, filename: str, content: bytes, content_type: str = "application/octet-stream"
) -> tuple[bytes, str]:
    boundary = "----KagehaTestBoundary7MA4YWxk"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n"
        f"\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


def test_computer_frame_throttle_allows_first_frame(webui_app: WebUIApp):
    assert webui_app._allow_computer_frame_emit() is True
    assert webui_app._allow_computer_frame_emit() is False


def test_meta_exposes_memory_kinds(webui_app: WebUIApp):
    status, payload = _call(webui_app, "GET", "/api/meta")
    assert status == 200
    assert payload["brand"] == "Kageha"
    assert "episodic" in payload["memory_kinds"]
    assert MemoryKind.INSTRUCTION.value in payload["memory_kinds"]
    assert MemoryState.CANDIDATE.value in payload["memory_states"]
    assert set(MEMORY_KINDS) == set(payload["memory_kinds"])


def test_health_and_react_index(webui_app: WebUIApp):
    status, payload = _call(webui_app, "GET", "/api/health")
    assert status == 200
    assert payload["ok"] is True

    st, body, ctype = webui_app.handle("GET", "/", {}, b"")
    # 200 when frontend is built; 503 when dist/ is absent (dev/CI without npm build).
    assert st in {200, 503}
    if st == 200:
        assert "text/html" in ctype
        assert b"Kageha" in body or b"root" in body

    # Legacy vanilla SPA removed.
    st, _, _ = webui_app.handle("GET", "/legacy/", {}, b"")
    assert st in {404, 503}
    st, _, _ = webui_app.handle("GET", "/app.js", {}, b"")
    assert st in {404, 503}


def test_api_slash_catalog_and_models(webui_app: WebUIApp):
    st, catalog = _call(webui_app, "GET", "/api/slash-catalog")
    assert st == 200
    assert catalog.get("ok") is True
    ids = {c["id"] for c in catalog.get("commands") or []}
    assert "comet" in ids
    assert "model" in ids
    assert "permissions" in ids
    assert "multitask" in ids
    assert "plan" in ids
    assert "browser" in ids
    plan = next(c for c in catalog["commands"] if c["id"] == "plan")
    assert plan.get("title") == "Plan"
    assert plan.get("kind") == "mode"

    st, models = _call(webui_app, "GET", "/api/models")
    assert st == 200
    assert models.get("ok") is True
    assert "models" in models
    assert "all" in models
    assert models.get("default_model") == "glm-5.2"
    assert isinstance(models.get("text"), str)


def test_sessions_new_and_list(webui_app: WebUIApp):
    status, created = _call(webui_app, "POST", "/api/sessions", body={})
    assert status == 200
    assert created["thread_id"]
    assert created["session_id"]
    assert created["session_id"] == webui_app.server.threads[created["thread_id"]]["run_id"]
    assert created["thread_id"] == f"web-{created['session_id']}"

    status, listed = _call(webui_app, "GET", "/api/sessions", query={"limit": ["10"]})
    assert status == 200
    assert "sessions" in listed


def test_open_session_loads_turn_history_and_binds_thread(webui_app: WebUIApp):
    from kageha.config import sessions_dir
    from kageha.runtime.types import TurnRequest

    sid = "openhist001"
    root = sessions_dir() / sid
    root.mkdir(parents=True)
    (root / "artifacts").mkdir(exist_ok=True)
    (root / "_turns").mkdir(exist_ok=True)
    (root / "_turns" / "t1.json").write_text(
        '{"turn_id":"t1","request":"hello there","answer":"Hi back","status":"success"}\n',
        encoding="utf-8",
    )
    # Register session in runtime store so inspect succeeds.
    webui_app.server.runtime.store.start_turn(
        TurnRequest(objective="hello there", session_id=sid, platform="webui"),
        session_id=sid,
    )

    status, opened = _call(webui_app, "GET", f"/api/sessions/{sid}")
    assert status == 200
    assert opened["session_id"] == sid
    assert opened["thread_id"] == f"web-{sid}"
    assert opened["messages"]
    assert opened["messages"][0]["role"] == "user"
    assert "hello there" in opened["messages"][0]["text"]
    assert opened["messages"][1]["role"] == "assistant"
    assert webui_app.server.threads[opened["thread_id"]]["run_id"] == sid
    meta = root / "session.json"
    assert meta.is_file()
    assert '"thread_id"' in meta.read_text(encoding="utf-8")


def test_open_missing_session_returns_error(webui_app: WebUIApp):
    status, payload = _call(webui_app, "GET", "/api/sessions/does-not-exist-xyz")
    assert status == 404
    assert "error" in payload


def test_memory_recall_uses_real_recall(webui_app: WebUIApp, tmp_path: Path):
    mem = get_memory_service()
    mem.mutate(
        MemoryMutation(
            action="remember",
            content="Prefer concise answers with provenance.",
            kind=MemoryKind.PREFERENCE.value,
            project_root=str(tmp_path / "proj"),
            session_id="web-test-session",
        )
    )

    status, payload = _call(
        webui_app,
        "POST",
        "/api/memory/search",
        body={
            "query": "concise answers provenance",
            "kinds": ["preference", "instruction", "episodic"],
            "project_root": str(tmp_path / "proj"),
            "max_results": 10,
        },
    )
    assert status == 200
    assert payload["trace_id"]
    assert any(
        "concise" in str(item.get("content") or "").lower() for item in payload["items"]
    )
    hit = next(
        item
        for item in payload["items"]
        if "concise" in str(item.get("content") or "").lower()
    )
    assert hit["provenance"]["source_role"]
    assert "source_session_id" in hit["provenance"]


def test_memory_list_kind_filter_episodic(webui_app: WebUIApp):
    status, payload = _call(
        webui_app,
        "GET",
        "/api/memory/list",
        query={"kind": ["episodic"], "limit": ["5"]},
    )
    assert status == 200
    assert payload["kind_filter"] == "episodic"
    assert isinstance(payload["items"], list)


def test_chat_requires_message(webui_app: WebUIApp):
    status, payload = _call(webui_app, "POST", "/api/chat", body={"thread_id": "t1"})
    assert status == 400
    assert "message" in payload["error"]


def test_chat_passes_followup_loop_mode(webui_app: WebUIApp, monkeypatch):
    captured: dict = {}

    def fake_rpc(method: str, params: dict | None = None):
        captured["method"] = method
        captured["params"] = params or {}
        return {
            "run_id": "abc123",
            "status": "success",
            "message": "ok",
            "artifacts": [],
            "turn_id": "t1",
        }

    monkeypatch.setattr(webui_app, "rpc", fake_rpc)
    status, payload = _call(
        webui_app,
        "POST",
        "/api/chat",
        body={"thread_id": "web-1", "message": "ping"},
    )
    assert status == 200
    assert captured["method"] == "thread/turn"
    assert captured["params"]["loop_mode"] == "followup"
    assert captured["params"]["agent_mode"] == "normal"
    assert captured["params"]["max_steps"] == 24
    assert payload["loop_mode"] == "followup"


@pytest.mark.parametrize(
    "message,agent_mode",
    [
        ("/plan research the API", "plan"),
        ("/goal ship the feature", "goal"),
    ],
)
def test_chat_deep_mode_slash_forces_full_loop(
    webui_app: WebUIApp, monkeypatch, message: str, agent_mode: str
):
    captured: dict = {}

    def fake_rpc(method: str, params: dict | None = None):
        captured["method"] = method
        captured["params"] = params or {}
        return {
            "run_id": "abc123",
            "status": "success",
            "message": "ok",
            "artifacts": [],
            "turn_id": "t1",
        }

    monkeypatch.setattr(webui_app, "rpc", fake_rpc)
    status, payload = _call(
        webui_app,
        "POST",
        "/api/chat",
        body={
            "thread_id": "web-deep",
            "message": message,
            # Older clients sent followup for everything except /plan.
            "loop_mode": "followup",
        },
    )
    assert status == 200
    assert captured["params"]["agent_mode"] == agent_mode
    assert captured["params"]["loop_mode"] == "full"
    assert captured["params"]["max_steps"] == 40
    assert payload["loop_mode"] == "full"


def test_chat_agent_mode_payload_forces_full_loop(
    webui_app: WebUIApp, monkeypatch
):
    captured: dict = {}

    def fake_rpc(method: str, params: dict | None = None):
        captured["params"] = params or {}
        return {
            "run_id": "abc123",
            "status": "success",
            "message": "ok",
            "artifacts": [],
            "turn_id": "t1",
        }

    monkeypatch.setattr(webui_app, "rpc", fake_rpc)
    status, _payload = _call(
        webui_app,
        "POST",
        "/api/chat",
        body={
            "thread_id": "web-goal",
            "message": "finish the migration",
            "agent_mode": "goal",
            "loop_mode": "followup",
        },
    )
    assert status == 200
    assert captured["params"]["agent_mode"] == "goal"
    assert captured["params"]["loop_mode"] == "full"


def test_upload_and_serve_session_file(webui_app: WebUIApp):
    status, created = _call(webui_app, "POST", "/api/sessions", body={})
    assert status == 200
    sid = created["session_id"]
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
        b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    raw, ctype = _multipart("file", "shot.png", png, "image/png")
    status, uploaded = _call(
        webui_app,
        "POST",
        f"/api/sessions/{sid}/upload",
        raw_body=raw,
        headers={"Content-Type": ctype},
    )
    assert status == 200
    assert uploaded["path"] == "inputs/shot.png"
    assert uploaded["kind"] == "image"
    assert uploaded["bytes"] == len(png)

    st, data, out_ctype, extra = webui_app.handle(
        "GET", f"/api/sessions/{sid}/files/inputs/shot.png", {}, b""
    )
    assert st == 200
    assert data == png
    assert "image/png" in out_ctype


def test_serve_session_pptx_download_disposition(webui_app: WebUIApp):
    status, created = _call(webui_app, "POST", "/api/sessions", body={})
    sid = created["session_id"]
    ws = webui_app._session_workspace(sid)
    rel = "artifacts/deck.pptx"
    path = ws.path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PKpptx-stub")
    st, data, ctype, extra = webui_app.handle(
        "GET", f"/api/sessions/{sid}/files/{rel}", {}, b""
    )
    assert st == 200
    assert data.startswith(b"PK")
    assert "presentationml" in ctype or "octet-stream" in ctype
    assert "attachment" in extra.get("Content-Disposition", "")
    assert "deck.pptx" in extra.get("Content-Disposition", "")


def test_artifacts_syncs_project_root_pptx(webui_app: WebUIApp, tmp_path, monkeypatch):
    """Deliverables written under project_root appear in Artifacts + /files/."""
    project = tmp_path / "proj"
    project.mkdir()
    deck = project / "bare_fair_investor_pitch.pptx"
    deck.write_bytes(b"PK-project-deck")
    webui_app.project_root = str(project)

    status, created = _call(webui_app, "POST", "/api/sessions", body={})
    assert status == 200
    sid = created["session_id"]
    ws = webui_app._session_workspace(sid)
    (ws.root / "result.md").write_text(
        "Created `bare_fair_investor_pitch.pptx`\n",
        encoding="utf-8",
    )

    st, payload = _call(webui_app, "GET", f"/api/sessions/{sid}/artifacts")
    assert st == 200
    paths = [a["path"] for a in payload.get("artifacts") or []]
    assert "artifacts/bare_fair_investor_pitch.pptx" in paths

    st, data, ctype, extra = webui_app.handle(
        "GET",
        f"/api/sessions/{sid}/files/artifacts/bare_fair_investor_pitch.pptx",
        {},
        b"",
    )
    assert st == 200
    assert data == b"PK-project-deck"
    assert "attachment" in extra.get("Content-Disposition", "")


def test_serve_session_file_blocks_traversal(webui_app: WebUIApp):
    status, created = _call(webui_app, "POST", "/api/sessions", body={})
    sid = created["session_id"]
    st, payload = _call(
        webui_app,
        "GET",
        f"/api/sessions/{sid}/files/../{sid}/session.json",
    )
    assert st in {400, 404}
    assert "error" in payload


def test_chat_includes_attachments_in_message(webui_app: WebUIApp, monkeypatch):
    captured: dict = {}

    def fake_rpc(method: str, params: dict | None = None):
        captured["method"] = method
        captured["params"] = params or {}
        return {
            "run_id": "sess-attach",
            "status": "success",
            "message": "got it",
            "artifacts": [],
            "turn_id": "t1",
        }

    monkeypatch.setattr(webui_app, "rpc", fake_rpc)
    status, payload = _call(
        webui_app,
        "POST",
        "/api/chat",
        body={
            "thread_id": "web-attach",
            "session_id": "sess-attach",
            "message": "use this",
            "attachments": ["inputs/brief.pdf", "inputs/hero.png"],
        },
    )
    assert status == 200
    msg = captured["params"]["message"]
    assert "use this" in msg
    assert "`inputs/brief.pdf`" in msg
    assert "`inputs/hero.png`" in msg
    assert payload["attachments"] == ["inputs/brief.pdf", "inputs/hero.png"]


def test_chat_attachments_only_ok(webui_app: WebUIApp, monkeypatch):
    captured: dict = {}

    def fake_rpc(method: str, params: dict | None = None):
        captured["params"] = params or {}
        return {
            "run_id": "s1",
            "status": "success",
            "message": "ok",
            "artifacts": [],
            "turn_id": "t1",
        }

    monkeypatch.setattr(webui_app, "rpc", fake_rpc)
    status, _payload = _call(
        webui_app,
        "POST",
        "/api/chat",
        body={
            "thread_id": "web-a",
            "message": "",
            "attachments": ["inputs/doc.pdf"],
        },
    )
    assert status == 200
    assert "`inputs/doc.pdf`" in captured["params"]["message"]
    assert captured["params"]["loop_mode"] == "followup"


def test_sse_bytes_format():
    raw = _sse_bytes("status", {"label": "Working…", "phase": "running"}).decode()
    assert raw.startswith("event: status\n")
    assert '"label": "Working…"' in raw or '"label": "Working\\u2026"' in raw
    assert raw.endswith("\n\n")


def test_chat_stream_buffered_router_returns_405(webui_app: WebUIApp):
    status, payload = _call(
        webui_app,
        "POST",
        "/api/chat/stream",
        body={"thread_id": "t1", "message": "hello"},
    )
    assert status == 405
    assert "stream" in payload["error"].lower()


def test_chat_stream_requires_message(webui_app: WebUIApp):
    frames: list[tuple[str, dict]] = []
    webui_app.stream_chat(
        json.dumps({"thread_id": "t1"}).encode(),
        lambda event, data: frames.append((event, data)),
    )
    assert frames
    assert frames[0][0] == "error"
    assert "message" in frames[0][1]["error"]


def test_chat_stream_emits_status_event_message_done(
    webui_app: WebUIApp, monkeypatch: pytest.MonkeyPatch
):
    async def fake_handle(req: dict):
        assert req["method"] == "thread/turn"
        tid = req["params"]["thread_id"]
        webui_app.server.threads[tid] = {
            "run_id": "sess-stream",
            "turn_id": "turn-stream",
        }
        await asyncio.sleep(0.18)
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "run_id": "sess-stream",
                "status": "success",
                "message": "streamed reply",
                "artifacts": ["artifacts/a.png"],
                "turn_id": "turn-stream",
            },
        }

    monkeypatch.setattr(webui_app.server, "handle", fake_handle)

    emitted = {"once": False}

    def fake_events(turn_id: str, *, after_sequence: int = 0):
        if after_sequence > 0 or emitted["once"]:
            return []
        emitted["once"] = True
        return [
            RunEvent.create(
                session_id="sess-stream",
                turn_id=turn_id,
                kind=RunEventKind.TOOL_STARTED,
                payload={"tool": "read_file"},
                sequence=1,
            )
        ]

    monkeypatch.setattr(webui_app.server.runtime.store, "events", fake_events)

    frames: list[tuple[str, dict]] = []
    webui_app.stream_chat(
        json.dumps(
            {
                "thread_id": "web-stream",
                "session_id": "sess-stream",
                "message": "research this topic carefully",
            }
        ).encode(),
        lambda event, data: frames.append((event, data)),
        poll_interval=0.05,
    )
    types = [event for event, _ in frames]
    assert "status" in types
    assert "event" in types
    assert "delta" in types
    assert "message" in types
    assert "done" in types
    assert "error" not in types

    deltas = [data["text"] for name, data in frames if name == "delta"]
    assert "".join(deltas) == "streamed reply"

    event = next(data for name, data in frames if name == "event")
    assert event["kind"] == "tool_started"
    label = str(event.get("label") or "").lower()
    assert "read" in label  # friendly "Reading file…" or raw read_file

    done = next(data for name, data in frames if name == "done")
    assert done["message"] == "streamed reply"
    assert done["session_id"] == "sess-stream"
    assert done["artifacts"] == ["artifacts/a.png"]
    assert done["loop_mode"] == "followup"


def test_chat_stream_isolates_turn_journal_across_sequential_streams(
    webui_app: WebUIApp, monkeypatch: pytest.MonkeyPatch
):
    """Turn-2 SSE must not replay turn-1 tool sequences (stale cards bug)."""
    thread_id = "web-stream-iso"
    webui_app.server.threads[thread_id] = {
        "run_id": "sess-iso",
        "turn_id": "turn-1",
    }

    async def fake_handle(req: dict):
        assert req["method"] == "thread/turn"
        tid = req["params"]["thread_id"]
        # Simulate AppServer publishing the *new* turn id mid-flight
        # without wiping prepare-chat stash keys.
        state = webui_app.server.threads.setdefault(tid, {})
        state["run_id"] = "sess-iso"
        state["turn_id"] = "turn-2"
        await asyncio.sleep(0.18)
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "run_id": "sess-iso",
                "status": "success",
                "message": "turn two reply",
                "artifacts": [],
                "turn_id": "turn-2",
            },
        }

    monkeypatch.setattr(webui_app.server, "handle", fake_handle)

    def fake_events(turn_id: str, *, after_sequence: int = 0):
        if turn_id == "turn-1":
            # Prior-turn journal — must never appear in the new stream.
            return [
                RunEvent.create(
                    session_id="sess-iso",
                    turn_id="turn-1",
                    kind=RunEventKind.TOOL_STARTED,
                    payload={"tool": "parallel_web_search"},
                    sequence=1,
                ),
                RunEvent.create(
                    session_id="sess-iso",
                    turn_id="turn-1",
                    kind=RunEventKind.TOOL_COMPLETED,
                    payload={"tool": "parallel_web_search", "ok": True},
                    sequence=2,
                ),
            ]
        if turn_id == "turn-2" and after_sequence == 0:
            return [
                RunEvent.create(
                    session_id="sess-iso",
                    turn_id="turn-2",
                    kind=RunEventKind.TOOL_STARTED,
                    payload={"tool": "list_dir"},
                    sequence=1,
                )
            ]
        return []

    monkeypatch.setattr(webui_app.server.runtime.store, "events", fake_events)

    frames: list[tuple[str, dict]] = []
    webui_app.stream_chat(
        json.dumps(
            {
                "thread_id": thread_id,
                "session_id": "sess-iso",
                "message": "please use the original ceremonial tin",
            }
        ).encode(),
        lambda event, data: frames.append((event, data)),
        poll_interval=0.05,
    )

    event_frames = [data for name, data in frames if name == "event"]
    assert event_frames, "expected turn-2 journal events"
    assert all(data.get("turn_id") == "turn-2" for data in event_frames)
    labels = " ".join(str(data.get("label") or "") for data in event_frames).lower()
    assert "parallel_web_search" not in labels
    assert "list" in labels  # friendly "Listing directory…" or raw list_dir
    # Prepare cleared leftover; run_id preserved.
    assert webui_app.server.threads[thread_id].get("run_id") == "sess-iso"
    assert webui_app.server.threads[thread_id].get("_prev_turn_id") == "turn-1"


def test_emit_text_deltas_chunks_and_message(monkeypatch: pytest.MonkeyPatch):
    sleeps: list[float] = []
    monkeypatch.setattr("kageha.webui.server.time.sleep", sleeps.append)

    text = "word " * 20
    frames: list[tuple[str, dict]] = []

    def emit(event: str, data: dict) -> None:
        frames.append((event, data))

    _emit_text_deltas(emit, text, pause_seconds=0.02)

    delta_text = "".join(data["text"] for name, data in frames if name == "delta")
    assert delta_text == text
    assert len(frames) >= 2
    assert sleeps


def test_chat_stream_preserves_quick_flag(
    webui_app: WebUIApp, monkeypatch: pytest.MonkeyPatch
):
    async def fake_handle(req: dict):
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "run_id": "sess-quick",
                "status": "success",
                "message": "Hey! I'm here — what do you want to work on?",
                "artifacts": [],
                "quick": True,
            },
        }

    monkeypatch.setattr(webui_app.server, "handle", fake_handle)
    frames: list[tuple[str, dict]] = []
    webui_app.stream_chat(
        json.dumps(
            {
                "thread_id": "web-quick",
                "session_id": "sess-quick",
                "message": "hi",
            }
        ).encode(),
        lambda event, data: frames.append((event, data)),
        poll_interval=0.05,
    )
    done = next(data for name, data in frames if name == "done")
    assert done["quick"] is True
    assert done["loop_mode"] == "quick"


def test_chat_cancel_route(webui_app: WebUIApp, monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    def fake_rpc(method: str, params: dict | None = None):
        captured["method"] = method
        captured["params"] = params or {}
        return {"ok": True}

    monkeypatch.setattr(webui_app, "rpc", fake_rpc)
    status, payload = _call(
        webui_app,
        "POST",
        "/api/chat/cancel",
        body={"thread_id": "web-cancel-1"},
    )
    assert status == 200
    assert payload == {"ok": True, "thread_id": "web-cancel-1"}
    assert captured["method"] == "thread/cancel"
    assert captured["params"]["thread_id"] == "web-cancel-1"

    status, err = _call(webui_app, "POST", "/api/chat/cancel", body={})
    assert status == 400
    assert "thread_id" in err["error"]


def test_session_artifacts_list(webui_app: WebUIApp):
    from kageha.config import sessions_dir

    sid = "artlist001"
    root = sessions_dir() / sid
    (root / "artifacts").mkdir(parents=True)
    (root / "_turns").mkdir(exist_ok=True)
    (root / "checkpoints").mkdir(exist_ok=True)
    (root / "artifacts" / "chart.png").write_bytes(b"png-bytes")
    (root / "outputs").mkdir(exist_ok=True)
    (root / "outputs" / "report.pdf").write_bytes(b"%PDF")
    junk = (
        root
        / "artifacts"
        / "bridges"
        / "whatsapp-baileys"
        / "node_modules"
        / "pkg"
    )
    junk.mkdir(parents=True)
    (junk / "index.js").write_text("module.exports={}\n", encoding="utf-8")
    (root / "session.json").write_text('{"session_id":"artlist001"}\n', encoding="utf-8")
    (root / "chat.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "_turns" / "hidden.json").write_text("{}", encoding="utf-8")
    (root / "noise.txt").write_bytes(b"skip-me")

    status, payload = _call(webui_app, "GET", f"/api/sessions/{sid}/artifacts")
    assert status == 200
    assert payload["session_id"] == sid
    paths = [row["path"] for row in payload["artifacts"]]
    assert "artifacts/chart.png" in paths
    assert "outputs/report.pdf" in paths
    assert "noise.txt" in paths
    assert "session.json" not in paths
    assert "chat.jsonl" not in paths
    assert not any("_turns" in p for p in paths)
    assert not any("checkpoints" in p for p in paths)
    assert not any("node_modules" in p for p in paths)
    chart = next(row for row in payload["artifacts"] if row["path"] == "artifacts/chart.png")
    assert chart["kind"] == "image"
    assert chart["name"] == "chart.png"
    assert chart["size"] == len(b"png-bytes")
    assert chart["mtime"] > 0
    assert chart["url"].endswith(f"/api/sessions/{sid}/files/artifacts/chart.png")
    assert paths.index("artifacts/chart.png") < paths.index("noise.txt")


def test_session_title_auto_and_patch(webui_app: WebUIApp, monkeypatch: pytest.MonkeyPatch):
    from kageha.config import sessions_dir

    def fake_rpc(method: str, params: dict | None = None):
        if method == "thread/start":
            return {}
        if method == "thread/turn":
            return {
                "run_id": "title001",
                "status": "success",
                "message": "done",
                "artifacts": [],
                "turn_id": "t1",
            }
        if method == "runtime/list":
            return [{"session_id": "title001", "objective": "fallback objective"}]
        if method == "runtime/inspect":
            return {"session": {"status": "success"}, "turns": [], "uncertain_tools": []}
        raise AssertionError(f"unexpected rpc {method}")

    monkeypatch.setattr(webui_app, "rpc", fake_rpc)

    status, created = _call(
        webui_app,
        "POST",
        "/api/sessions",
        body={"session_id": "title001"},
    )
    assert status == 200

    long_msg = "Explain quantum entanglement for a curious teenager in plain language"
    status, chat = _call(
        webui_app,
        "POST",
        "/api/chat",
        body={
            "thread_id": created["thread_id"],
            "session_id": "title001",
            "message": long_msg,
        },
    )
    assert status == 200
    meta = json.loads((sessions_dir() / "title001" / "session.json").read_text())
    assert meta["title"] == long_msg[:59].rstrip() + "…"

    status, opened = _call(webui_app, "GET", "/api/sessions/title001")
    assert status == 200
    assert opened["title"] == meta["title"]

    status, patched = _call(
        webui_app,
        "PATCH",
        "/api/sessions/title001",
        body={"title": "Renamed chat"},
    )
    assert status == 200
    assert patched["title"] == "Renamed chat"

    status, chat2 = _call(
        webui_app,
        "POST",
        "/api/chat",
        body={
            "thread_id": created["thread_id"],
            "session_id": "title001",
            "message": "another message should not change title",
        },
    )
    assert status == 200
    meta2 = json.loads((sessions_dir() / "title001" / "session.json").read_text())
    assert meta2["title"] == "Renamed chat"

    status, listed = _call(webui_app, "GET", "/api/sessions", query={"limit": ["10"]})
    assert status == 200
    row = next(s for s in listed["sessions"] if s.get("session_id") == "title001")
    assert row["title"] == "Renamed chat"


def test_session_title_upgrades_from_weak_greeting(
    webui_app: WebUIApp, monkeypatch: pytest.MonkeyPatch
):
    from kageha.config import sessions_dir

    def fake_rpc(method: str, params: dict | None = None):
        if method == "thread/start":
            return {}
        if method == "thread/turn":
            msg = str((params or {}).get("message") or (params or {}).get("task") or "")
            arts = []
            reply = "done"
            if "research" in msg.lower() or msg == "P":
                arts = [{"path": "artifacts/market_research.md"}]
                reply = "Finished market research for the KAGEHA Classic Ceremonial ad."
            return {
                "run_id": "titlehey01",
                "status": "success",
                "message": reply,
                "artifacts": arts,
                "turn_id": "t1",
            }
        if method == "runtime/list":
            return [{"session_id": "titlehey01", "objective": "hey"}]
        if method == "runtime/inspect":
            return {"session": {"status": "success"}, "turns": [], "uncertain_tools": []}
        raise AssertionError(f"unexpected rpc {method}")

    monkeypatch.setattr(webui_app, "rpc", fake_rpc)

    status, created = _call(
        webui_app,
        "POST",
        "/api/sessions",
        body={"session_id": "titlehey01"},
    )
    assert status == 200

    status, _ = _call(
        webui_app,
        "POST",
        "/api/chat",
        body={
            "thread_id": created["thread_id"],
            "session_id": "titlehey01",
            "message": "hey",
        },
    )
    assert status == 200
    meta = json.loads((sessions_dir() / "titlehey01" / "session.json").read_text())
    assert meta["title"] == "hey"
    assert meta.get("title_source") == "auto"

    # Drop a deliverable so a weak follow-up can still upgrade the title.
    art = sessions_dir() / "titlehey01" / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    (art / "market_research.md").write_text("# research\n", encoding="utf-8")

    status, chat = _call(
        webui_app,
        "POST",
        "/api/chat",
        body={
            "thread_id": created["thread_id"],
            "session_id": "titlehey01",
            "message": "P",
        },
    )
    assert status == 200
    assert chat.get("title") == "Market Research"
    meta2 = json.loads((sessions_dir() / "titlehey01" / "session.json").read_text())
    assert meta2["title"] == "Market Research"


def test_session_pin_archive_and_delete(webui_app: WebUIApp, monkeypatch: pytest.MonkeyPatch):
    from kageha.config import sessions_dir

    def fake_rpc(method: str, params: dict | None = None):
        if method == "thread/start":
            return {}
        if method == "runtime/list":
            return [
                {"session_id": "flag001", "objective": "one"},
                {"session_id": "flag002", "objective": "two"},
            ]
        if method == "runtime/inspect":
            return {"session": {"status": "idle"}, "turns": [], "uncertain_tools": []}
        raise AssertionError(f"unexpected rpc {method}")

    monkeypatch.setattr(webui_app, "rpc", fake_rpc)

    st, created = _call(
        webui_app, "POST", "/api/sessions", body={"session_id": "flag001"}
    )
    assert st == 200
    _call(webui_app, "POST", "/api/sessions", body={"session_id": "flag002"})

    st, pinned = _call(
        webui_app,
        "PATCH",
        "/api/sessions/flag001",
        body={"pinned": True},
    )
    assert st == 200
    assert pinned["pinned"] is True
    assert pinned["archived"] is False

    st, archived = _call(
        webui_app,
        "PATCH",
        "/api/sessions/flag002",
        body={"archived": True},
    )
    assert st == 200
    assert archived["archived"] is True

    meta1 = json.loads((sessions_dir() / "flag001" / "session.json").read_text())
    meta2 = json.loads((sessions_dir() / "flag002" / "session.json").read_text())
    assert meta1.get("pinned") is True
    assert meta2.get("archived") is True

    st, listed = _call(webui_app, "GET", "/api/sessions", query={"limit": ["20"]})
    assert st == 200
    by_id = {s["session_id"]: s for s in listed["sessions"]}
    assert by_id["flag001"]["pinned"] is True
    assert by_id["flag002"]["archived"] is True

    st, opened = _call(webui_app, "GET", "/api/sessions/flag001")
    assert st == 200
    assert opened["pinned"] is True

    st, deleted = _call(webui_app, "DELETE", "/api/sessions/flag002")
    assert st == 200
    assert deleted.get("ok") is True
    assert not (sessions_dir() / "flag002").exists()

    # Title-only patch still works and preserves flags.
    st, renamed = _call(
        webui_app,
        "PATCH",
        "/api/sessions/flag001",
        body={"title": "Pinned chat"},
    )
    assert st == 200
    assert renamed["title"] == "Pinned chat"
    assert renamed["pinned"] is True


def test_session_events_maps_stream_views(
    webui_app: WebUIApp, monkeypatch: pytest.MonkeyPatch
):
    status, created = _call(webui_app, "POST", "/api/sessions", body={})
    assert status == 200
    sid = created["session_id"]
    tid = created["thread_id"]
    webui_app.server.threads[tid] = {
        "run_id": sid,
        "turn_id": "turn-ev1",
    }

    def fake_events(turn_id: str, *, after_sequence: int = 0):
        if after_sequence > 0:
            return []
        return [
            RunEvent.create(
                session_id=sid,
                turn_id=turn_id,
                kind=RunEventKind.TOOL_STARTED,
                payload={"tool": "read_file", "path": "README.md"},
                sequence=1,
            )
        ]

    monkeypatch.setattr(webui_app.server.runtime.store, "events", fake_events)
    status, payload = _call(
        webui_app,
        "GET",
        f"/api/sessions/{sid}/events",
        query={"turn_id": ["turn-ev1"], "after_sequence": ["0"]},
    )
    assert status == 200
    assert payload["events"]
    ev = payload["events"][0]
    assert ev["kind"] == "tool_started"
    assert "label" in ev
    assert "detail" in ev
    assert "interesting" in ev


def test_stream_event_view_computer_tools_are_pulse_only():
    # Mutations show in Activity so users see desktop work; observe stays pulse.
    started = _stream_event_view(
        "tool_started", {"tool": "computer_click_sequence", "side_effect": "external_mutation"}
    )
    assert started["label"] == "Clicking…"
    assert started["interesting"] is True

    typed = _stream_event_view("tool_started", {"tool": "computer_type"})
    assert typed["label"] == "Typing…"
    assert typed["interesting"] is True

    state = _stream_event_view("tool_started", {"tool": "computer_get_state"})
    assert state["label"] == "Reading UI…"
    assert state["interesting"] is False
    assert "tool_card" not in state

    done = _stream_event_view(
        "tool_completed", {"tool": "computer_click", "state": "completed"}
    )
    assert done["label"] == "Clicking…"
    assert done["interesting"] is True
    assert "computer_frame" not in done

    slim = _sse_payload_view(
        "tool_started",
        {
            "tool": "computer_get_state",
            "snapshot": "e0 " * 500,
            "side_effect": "read",
            "attempt_id": "a1",
        },
    )
    assert slim == {
        "tool": "computer_get_state",
        "side_effect": "read",
        "attempt_id": "a1",
    }
    assert "snapshot" not in slim


def test_stream_event_view_tool_card_fields():
    started = _stream_event_view(
        "tool_started",
        {
            "tool": "read_file",
            "args_preview": "path=README.md",
            "status": "running",
            "attempt_id": "a1",
            "tool_card": {
                "name": "read_file",
                "args_preview": "path=README.md",
                "status": "running",
                "duration_ms": None,
                "artifact_refs": [],
                "attempt_id": "a1",
            },
        },
    )
    assert started["interesting"] is True
    assert started["tool_card"]["name"] == "read_file"
    assert started["tool_card"]["args_preview"] == "path=README.md"
    assert started["tool_card"]["status"] == "running"
    assert "path=README.md" in " ".join(started["detail"])

    done = _stream_event_view(
        "tool_completed",
        {
            "tool": "write_file",
            "state": "completed",
            "args_preview": "path=artifacts/out.md",
            "status": "ok",
            "duration_ms": 42.5,
            "artifact_refs": ["artifacts/out.md"],
            "tool_card": {
                "name": "write_file",
                "args_preview": "path=artifacts/out.md",
                "status": "ok",
                "duration_ms": 42.5,
                "artifact_refs": ["artifacts/out.md"],
                "attempt_id": "a2",
            },
        },
    )
    assert done["interesting"] is False
    card = done["tool_card"]
    assert card["status"] == "ok"
    assert card["duration_ms"] == 42.5
    assert card["artifact_refs"] == ["artifacts/out.md"]


def test_stream_frame_computer_screenshot_emits_frame_and_card():
    frame = _stream_frame(
        kind="tool_completed",
        payload={
            "tool": "computer_screenshot",
            "state": "completed",
            "status": "ok",
            "duration_ms": 120,
            "args_preview": "path=artifacts/computer/screen.png",
            "artifact_refs": [
                "artifacts/computer/screen.png",
                "artifacts/computer/thumbs/screen_thumb.jpg",
            ],
            "tool_card": {
                "name": "computer_screenshot",
                "args_preview": "path=artifacts/computer/screen.png",
                "status": "ok",
                "duration_ms": 120,
                "artifact_refs": [
                    "artifacts/computer/screen.png",
                    "artifacts/computer/thumbs/screen_thumb.jpg",
                ],
                "attempt_id": "shot1",
            },
            "computer_frame": {
                "path": "artifacts/computer/thumbs/screen_thumb.jpg",
                "thumb_path": "artifacts/computer/thumbs/screen_thumb.jpg",
                "app": "",
                "action": "computer_screenshot",
            },
        },
        sequence=9,
        turn_id="t1",
        session_id="s1",
    )
    # Screenshots are Activity milestones (frame + card), not pulse-only.
    assert frame["interesting"] is True
    assert frame["label"] == "Capturing…"
    assert frame["tool_card"]["name"] == "computer_screenshot"
    assert frame["computer_frame"]["thumb_path"].endswith("screen_thumb.jpg")
    assert frame["computer_frame"]["thumb_url"].startswith(
        "/api/sessions/s1/files/artifacts/computer/thumbs/"
    )
    assert "snapshot" not in frame["payload"]
    assert frame["payload"]["computer_frame"]["path"].endswith(".jpg")
    assert frame["payload"]["computer_frame"]["thumb_url"] == frame["computer_frame"][
        "thumb_url"
    ]


def test_stream_frame_strips_bulky_keys_and_pulse_card_spam():
    frame = _stream_frame(
        kind="tool_completed",
        payload={
            "tool": "computer_click",
            "state": "completed",
            "status": "ok",
            "args_preview": "ref=e3",
            "tool_card": {
                "name": "computer_click",
                "args_preview": "ref=e3",
                "status": "ok",
                "duration_ms": 8,
                "artifact_refs": [],
            },
            "snapshot": "huge " * 200,
            "elements": [{"ref": "e0"}] * 50,
        },
        sequence=3,
    )
    # Mutations are Activity-visible; bulky AX dumps still stripped from SSE.
    assert frame["interesting"] is True
    assert "tool_card" in frame
    assert "snapshot" not in frame["payload"]
    assert "elements" not in frame["payload"]

    observe = _stream_frame(
        kind="tool_completed",
        payload={
            "tool": "computer_get_state",
            "state": "completed",
            "snapshot": "huge " * 200,
            "elements": [{"ref": "e0"}] * 50,
        },
        sequence=4,
    )
    assert observe["interesting"] is False
    assert "tool_card" not in observe
    assert "snapshot" not in observe["payload"]
    assert "elements" not in observe["payload"]


def test_approvals_route_requires_id(webui_app: WebUIApp):
    status, payload = _call(webui_app, "POST", "/api/approvals", body={})
    assert status == 400
    assert "approval_id" in payload.get("error", "").lower()


def test_stream_frame_approval_required_keeps_approval_id():
    """UI needs approval_id on the payload to render Approve/Deny controls."""
    frame = _stream_frame(
        kind="approval_required",
        payload={
            "approval_id": "aid-plan-xyz",
            "action": "approve_plan",
            "detail": "Plan (plan)\n## Steps",
            "risk_class": "plan",
        },
        sequence=9,
        turn_id="t1",
        session_id="s1",
    )
    assert frame["kind"] == "approval_required"
    assert frame["interesting"] is True
    assert "Waiting for approval" in frame["label"]
    assert frame["payload"]["approval_id"] == "aid-plan-xyz"
    assert frame["payload"]["action"] == "approve_plan"
    assert frame["payload"]["risk_class"] == "plan"


def test_jobs_list_attach_cancel_routes(webui_app: WebUIApp, monkeypatch: pytest.MonkeyPatch):
    from kageha.project.async_jobs import save_job

    # Avoid spawning a real worker process during create.
    monkeypatch.setattr(
        "kageha.project.async_jobs.start_job",
        lambda job_id: __import__(
            "kageha.project.async_jobs", fromlist=["load_job"]
        ).load_job(job_id),
    )

    status, created = _call(
        webui_app,
        "POST",
        "/api/jobs",
        body={"objective": "phase-d dashboard job", "max_steps": 1},
    )
    assert status == 200
    job_id = created["id"]
    assert created["session_id"] == job_id
    # Queued jobs pre-bind session_id but are not attachable until a turn exists.
    assert created["attachable"] is False
    assert created["can_cancel"] is True

    status, listed = _call(
        webui_app, "GET", "/api/jobs", query={"limit": ["10"], "status": ["active"]}
    )
    assert status == 200
    assert "counts" in listed
    assert any(j["id"] == job_id for j in listed["jobs"])

    # Simulate mid-run attach fields.
    from kageha.project.async_jobs import load_job

    job = load_job(job_id)
    assert job is not None
    job.status = "running"
    job.turn_id = "turn-webui-1"
    save_job(job)

    status, attach = _call(webui_app, "GET", f"/api/jobs/{job_id}/attach")
    assert status == 200
    assert attach["session_id"] == job_id
    assert attach["turn_id"] == "turn-webui-1"
    assert attach["thread_id"] == f"job-{job_id}"
    assert attach["attachable"] is True
    assert attach["job"]["attachable"] is True

    status, cancelled = _call(
        webui_app, "POST", f"/api/jobs/{job_id}/cancel", body={}
    )
    assert status == 200
    assert cancelled["status"] == "cancelled"
    assert cancelled["can_cancel"] is False

    status, missing = _call(webui_app, "GET", "/api/jobs/does-not-exist/attach")
    assert status == 404


def test_sessions_active_filter_includes_turn_phase(webui_app: WebUIApp):
    from kageha.runtime.types import TurnRequest

    sid = "activesess01"
    webui_app.server.runtime.store.start_turn(
        TurnRequest(objective="live turn", session_id=sid, platform="webui"),
        session_id=sid,
    )
    status, listed = _call(
        webui_app, "GET", "/api/sessions", query={"active": ["1"], "limit": ["20"]}
    )
    assert status == 200
    assert listed["active"] is True
    row = next(s for s in listed["sessions"] if s.get("session_id") == sid or s.get("id") == sid)
    assert row["active"] is True
    assert "turn_phase" in row
    assert row.get("turn_status") in {"running", "accepted", "active", ""} or row["active"]


def test_non_dict_thread_state_prevents_attribute_error(webui_app: WebUIApp):
    thread_id = "test-nondict-thread"
    # Corrupt thread state with non-dict values (string, None, etc.)
    webui_app.server.threads[thread_id] = "corrupted_string_state"  # type: ignore[assignment]
    st = webui_app._thread_state(thread_id)
    assert isinstance(st, dict)
    assert st.get("turn_id") is None
    status, payload = _call(
        webui_app,
        "GET",
        "/api/sessions/testnondict01/events",
        query={"thread_id": [thread_id]},
    )
    assert status == 200
    assert payload["thread_id"] == thread_id


def test_review_result_includes_diff_fields():
    from kageha.project.review import ReviewResult

    result = ReviewResult(
        ok=True,
        base="main",
        head="HEAD",
        diff_stat="1 file changed",
        diff="diff --git a/x b/x\n+hello\n",
        message="ok",
    )
    data = result.to_dict()
    assert data["diff_stat"] == "1 file changed"
    assert data["diff"].startswith("diff --git")
