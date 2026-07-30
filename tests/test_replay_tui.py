"""Tests for the session replay TUI and static renderer."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from kageha.obs.replay import (
    ReplayEvent,
    SessionTimeline,
    load_timeline_from_events,
)
from kageha.obs.replay_tui import (
    ReplayApp,
    _event_summary,
    _icon_for_kind,
    find_session_events,
    render_replay_static,
)


# ── Fixtures ──────────────────────────────────────────────────────────


def _make_events_file(tmp_path: Path, events: list[dict]) -> Path:
    """Create a session dir with events.jsonl."""
    session_dir = tmp_path / "test-session-001"
    session_dir.mkdir(parents=True)
    events_file = session_dir / "events.jsonl"
    with events_file.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return events_file


def _sample_events() -> list[dict]:
    """Generate a realistic sequence of events."""
    base_ts = 1722300000.0
    return [
        {"ts": base_ts, "kind": "run_start", "step": 0, "data": {"task": "Fix the login bug"}},
        {"ts": base_ts + 0.5, "kind": "plan", "step": 0, "data": {"source": "auto", "steps": 3}},
        {"ts": base_ts + 1.0, "kind": "context", "step": 0, "data": {"prefix_tokens": 5000, "history_tokens": 200}},
        {"ts": base_ts + 2.0, "kind": "model_call", "step": 1, "data": {"model": "claude-sonnet-5", "tokens": {"input": 5200, "output": 400}}},
        {"ts": base_ts + 2.5, "kind": "tool_call", "step": 1, "data": {"tool": "read_file", "args": {"path": "src/auth.py"}, "success": True, "duration_ms": 45}},
        {"ts": base_ts + 3.0, "kind": "tool_call", "step": 1, "data": {"tool": "shell", "args": {"cmd": "pytest tests/"}, "success": False, "duration_ms": 3200}},
        {"ts": base_ts + 4.0, "kind": "decision_trace", "step": 1, "data": {"category": "adaptive_control", "decision": "REPAIR: test failure detected"}},
        {"ts": base_ts + 5.0, "kind": "adaptive_control", "step": 2, "data": {"decision": "REPAIR", "reasoning": "1 test failed, fix is localized"}},
        {"ts": base_ts + 6.0, "kind": "model_call", "step": 2, "data": {"model": "claude-sonnet-5", "tokens": {"input": 6000, "output": 600}}},
        {"ts": base_ts + 7.0, "kind": "tool_call", "step": 2, "data": {"tool": "write_file", "args": {"path": "src/auth.py"}, "success": True, "duration_ms": 12}},
        {"ts": base_ts + 8.0, "kind": "tool_call", "step": 2, "data": {"tool": "shell", "args": {"cmd": "pytest tests/"}, "success": True, "duration_ms": 2800}},
        {"ts": base_ts + 9.0, "kind": "verifier", "step": 3, "data": {"status": "pass", "defects": []}},
        {"ts": base_ts + 9.5, "kind": "stop", "step": 3, "data": {"rule": "all_goals_pass"}},
        {"ts": base_ts + 10.0, "kind": "run_end", "step": 3, "data": {"status": "success", "steps": 3}},
    ]


@pytest.fixture
def sample_timeline(tmp_path: Path) -> SessionTimeline:
    """Create a SessionTimeline from sample events."""
    events_file = _make_events_file(tmp_path, _sample_events())
    timeline = load_timeline_from_events(events_file)
    assert timeline is not None
    return timeline


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    """Create a sessions directory with multiple sessions."""
    import os

    for i in range(3):
        session_dir = tmp_path / f"session-{i:03d}"
        session_dir.mkdir()
        events_file = session_dir / "events.jsonl"
        ts = time.time() - (3 - i) * 100
        with events_file.open("w") as f:
            f.write(json.dumps({"ts": ts, "kind": "run_start", "step": 0, "data": {"task": f"task-{i}"}}) + "\n")
            f.write(json.dumps({"ts": ts + 5, "kind": "run_end", "step": 1, "data": {"status": "success"}}) + "\n")
        # Set explicit mtime so session-002 is newest
        os.utime(events_file, (ts, ts))
    return tmp_path


# ── Unit Tests ────────────────────────────────────────────────────────


class TestIconForKind:
    def test_known_kinds(self):
        assert _icon_for_kind("tool_call") == "🔧"
        assert _icon_for_kind("model_call") == "🤖"
        assert _icon_for_kind("error") == "❌"
        assert _icon_for_kind("stop") == "⏹"

    def test_unknown_kind(self):
        assert _icon_for_kind("some_random_event") == "·"


class TestEventSummary:
    def test_tool_call_success(self):
        event = ReplayEvent(
            timestamp=0, kind="tool_call", step=1,
            data={"tool": "read_file", "args": {"path": "x.py"}, "success": True, "duration_ms": 50}
        )
        summary = _event_summary(event)
        assert "✓" in summary
        assert "read_file" in summary
        assert "50ms" in summary

    def test_tool_call_failure(self):
        event = ReplayEvent(
            timestamp=0, kind="tool_call", step=1,
            data={"tool": "shell", "success": False, "duration_ms": 100}
        )
        summary = _event_summary(event)
        assert "✗" in summary
        assert "shell" in summary

    def test_model_call(self):
        event = ReplayEvent(
            timestamp=0, kind="model_call", step=1,
            data={"model": "gpt-4", "tokens": {"input": 1000, "output": 200}}
        )
        summary = _event_summary(event)
        assert "gpt-4" in summary
        assert "1000" in summary

    def test_verifier_pass(self):
        event = ReplayEvent(
            timestamp=0, kind="verifier", step=1,
            data={"status": "pass", "defects": []}
        )
        summary = _event_summary(event)
        assert "pass" in summary
        assert "defects=0" in summary

    def test_error(self):
        event = ReplayEvent(
            timestamp=0, kind="error", step=1,
            data={"message": "Something went wrong"}
        )
        summary = _event_summary(event)
        assert "Something went wrong" in summary

    def test_unknown_kind_falls_back_to_json(self):
        event = ReplayEvent(
            timestamp=0, kind="custom_event", step=1,
            data={"foo": "bar"}
        )
        summary = _event_summary(event)
        assert "foo" in summary or "bar" in summary


class TestSessionTimeline:
    def test_load_and_basic_properties(self, sample_timeline: SessionTimeline):
        assert sample_timeline.session_id == "test-session-001"
        assert sample_timeline.total_steps == 3
        assert sample_timeline.duration_s == pytest.approx(10.0, abs=0.1)
        assert len(sample_timeline.events) == 14

    def test_tool_calls_property(self, sample_timeline: SessionTimeline):
        tools = sample_timeline.tool_calls
        assert len(tools) == 4

    def test_decisions_property(self, sample_timeline: SessionTimeline):
        decisions = sample_timeline.decisions
        assert len(decisions) == 2  # adaptive_control + decision_trace

    def test_render_summary(self, sample_timeline: SessionTimeline):
        summary = sample_timeline.render_summary()
        assert "test-session-001" in summary
        assert "Tool calls: 4" in summary

    def test_render_timeline(self, sample_timeline: SessionTimeline):
        rendered = sample_timeline.render_timeline(width=100)
        assert "Step 0" in rendered
        assert "Step 1" in rendered
        assert "Step 2" in rendered
        assert "Step 3" in rendered
        assert "End of timeline" in rendered

    def test_render_timeline_filtered_by_kind(self, sample_timeline: SessionTimeline):
        rendered = sample_timeline.render_timeline(kinds={"tool_call"}, width=100)
        assert "read_file" in rendered
        assert "write_file" in rendered
        # Should NOT contain model_call events
        assert "claude-sonnet" not in rendered

    def test_render_timeline_filtered_by_step(self, sample_timeline: SessionTimeline):
        rendered = sample_timeline.render_timeline(step_range=(2, 3), width=100)
        assert "Step 2" in rendered
        assert "Step 3" in rendered
        # Step 0 and 1 should not appear
        assert "Step 0" not in rendered
        assert "Step 1" not in rendered

    def test_render_step_detail(self, sample_timeline: SessionTimeline):
        detail = sample_timeline.render_step_detail(1)
        assert "Step 1 Detail" in detail
        assert "tool_call" in detail or "model_call" in detail

    def test_render_step_detail_empty(self, sample_timeline: SessionTimeline):
        detail = sample_timeline.render_step_detail(99)
        assert "No events" in detail


class TestFindSessionEvents:
    def test_find_by_id(self, sessions_dir: Path):
        result = find_session_events("session-001", sessions_path=sessions_dir)
        assert result is not None
        assert result.name == "events.jsonl"
        assert "session-001" in str(result)

    def test_find_by_partial_id(self, sessions_dir: Path):
        result = find_session_events("session-00", sessions_path=sessions_dir)
        assert result is not None  # Should match first partial

    def test_find_last(self, sessions_dir: Path):
        result = find_session_events(None, sessions_path=sessions_dir, last=True)
        assert result is not None
        # Should be session-002 (most recent)
        assert "session-002" in str(result)

    def test_find_nonexistent(self, sessions_dir: Path):
        result = find_session_events("nonexistent", sessions_path=sessions_dir)
        assert result is None


class TestRenderReplayStatic:
    def test_static_render_last(self, sessions_dir: Path):
        output = render_replay_static(None, last=True, sessions_path=sessions_dir)
        assert "session-002" in output
        assert "Timeline" in output

    def test_static_render_specific(self, sessions_dir: Path):
        output = render_replay_static("session-001", sessions_path=sessions_dir)
        assert "session-001" in output

    def test_static_render_not_found(self, sessions_dir: Path):
        output = render_replay_static("nonexistent", sessions_path=sessions_dir)
        assert "No events found" in output


class TestReplayAppCreation:
    """Test that the TUI app can be instantiated (not run)."""

    def test_app_creation(self, sample_timeline: SessionTimeline):
        app = ReplayApp(timeline=sample_timeline)
        assert app is not None
        assert app._initial_timeline == sample_timeline

    def test_app_creation_without_timeline(self):
        app = ReplayApp()
        assert app._initial_timeline is None


# ── Edge Cases ─────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_events_file(self, tmp_path: Path):
        session_dir = tmp_path / "empty-session"
        session_dir.mkdir()
        events_file = session_dir / "events.jsonl"
        events_file.write_text("")
        timeline = load_timeline_from_events(events_file)
        assert timeline is not None
        assert len(timeline.events) == 0

    def test_malformed_json_lines(self, tmp_path: Path):
        session_dir = tmp_path / "bad-session"
        session_dir.mkdir()
        events_file = session_dir / "events.jsonl"
        events_file.write_text(
            '{"ts": 1.0, "kind": "run_start", "step": 0}\n'
            'NOT JSON\n'
            '{"ts": 2.0, "kind": "run_end", "step": 1}\n'
        )
        timeline = load_timeline_from_events(events_file)
        assert timeline is not None
        assert len(timeline.events) == 2  # Skips the bad line

    def test_very_long_event_data(self, tmp_path: Path):
        long_text = "x" * 10000
        events = [{"ts": 1.0, "kind": "user_message", "step": 0, "data": {"text": long_text}}]
        events_file = _make_events_file(tmp_path, events)
        timeline = load_timeline_from_events(events_file)
        assert timeline is not None
        # Summary should truncate
        summary = _event_summary(timeline.events[0])
        assert len(summary) < 200

    def test_timeline_with_single_event(self, tmp_path: Path):
        events = [{"ts": 1.0, "kind": "run_start", "step": 0, "data": {"task": "hello"}}]
        events_file = _make_events_file(tmp_path, events)
        timeline = load_timeline_from_events(events_file)
        assert timeline is not None
        assert timeline.duration_s == 0.0
        assert timeline.total_steps == 0
