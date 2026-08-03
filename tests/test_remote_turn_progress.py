"""Attached CLI turns map runtime events into progress telemetry."""

from kageha.chat.remote_turn import _event_to_status


def test_event_to_status_covers_common_kinds():
    assert _event_to_status("planning_started", {}) == "[kageha] planning…"
    assert "action: read_file" in _event_to_status(
        "tool_started",
        {"tool": "read_file", "args_preview": "README.md"},
    )
    assert _event_to_status(
        "tool_completed", {"tool": "bash", "status": "ok"}
    ) == "[kageha]   ← bash: ok"
    assert "verify=" in _event_to_status("verification_started", {})
    assert _event_to_status("approval_required", {}) == "[kageha] tools: ask_human"
    assert _event_to_status("unknown_kind", {}) == ""
