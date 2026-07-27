"""Chat session listing helpers."""

from __future__ import annotations

import time

from kageha.chat.repl import list_sessions
from kageha.runtime import RuntimeStore, RunEventKind, TurnRequest


def test_list_sessions_orders_recent(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    store = RuntimeStore()
    old_event, _ = store.start_turn(
        TurnRequest(objective="older task"),
        session_id="aaa111",
    )
    store.append_event(
        session_id=old_event.session_id,
        turn_id=old_event.turn_id,
        kind=RunEventKind.FAILED,
        payload={"status": "success"},
    )
    time.sleep(0.02)
    new_event, _ = store.start_turn(
        TurnRequest(objective="newer task"),
        session_id="bbb222",
    )
    store.append_event(
        session_id=new_event.session_id,
        turn_id=new_event.turn_id,
        kind=RunEventKind.FAILED,
        payload={"status": "max_steps"},
    )
    store.close()

    rows = list_sessions(10)
    assert rows[0]["run_id"] == "bbb222"
    assert "newer" in rows[0]["task"]
    assert rows[0]["status"] == "max_steps"
