"""Interactive session replay TUI — terminal-based timeline visualization.

A Textual app that displays agent session events in a navigable,
filterable timeline. Supports:
- Session list selection
- Timeline scrolling with event detail
- Filtering by event kind, step range
- Summary statistics (tools, decisions, errors)
- Step-through mode with keyboard navigation

Usage (via CLI):
    kageha runtime replay [session_id]
    kageha runtime replay --last
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Static,
)

from kageha.obs.replay import (
    ReplayEvent,
    SessionTimeline,
    load_timeline_from_events,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _icon_for_kind(kind: str) -> str:
    """Map event kind to a terminal icon."""
    icons = {
        "tool_call": "🔧",
        "model_call": "🤖",
        "adaptive_control": "⚙",
        "verifier": "✓",
        "stop": "⏹",
        "decision_trace": "📝",
        "user_message": "👤",
        "assistant_message": "💬",
        "error": "❌",
        "checkpoint": "📌",
        "run_start": "▶",
        "run_end": "■",
        "hook": "🪝",
        "permission": "🔒",
    }
    return icons.get(kind, "·")


def _format_ts(ts: float) -> str:
    """Format timestamp as HH:MM:SS.mmm."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%H:%M:%S.%f")[:-3]


def _truncate(text: str, max_len: int) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _event_summary(event: ReplayEvent) -> str:
    """One-line summary for the event table."""
    d = event.data
    kind = event.kind

    if kind == "tool_call":
        tool = d.get("tool", "?")
        dur = d.get("duration_ms", 0)
        status = "✓" if d.get("success") else "✗"
        args_str = _truncate(str(d.get("args", "")), 30)
        return f"{status} {tool}({args_str}) [{dur}ms]"

    if kind == "model_call":
        model = d.get("model", "?")
        tokens = d.get("tokens", {})
        return f"{model} (in={tokens.get('input', '?')} out={tokens.get('output', '?')})"

    if kind == "adaptive_control":
        decision = d.get("decision", "?")
        reason = _truncate(d.get("reasoning", ""), 50)
        return f"{decision}: {reason}"

    if kind == "verifier":
        status = d.get("status", "?")
        defects = len(d.get("defects", []))
        return f"verdict={status} defects={defects}"

    if kind == "stop":
        return d.get("rule", "unknown rule")

    if kind == "decision_trace":
        cat = d.get("category", "?")
        decision = _truncate(d.get("decision", ""), 50)
        return f"[{cat}] {decision}"

    if kind == "user_message":
        return _truncate(d.get("text", ""), 60)

    if kind == "assistant_message":
        return _truncate(d.get("text", ""), 60)

    if kind == "error":
        return _truncate(d.get("message", "?"), 60)

    if kind == "run_start":
        return _truncate(d.get("task", d.get("turn_task", "")), 60)

    if kind == "run_end":
        return f"status={d.get('status', '?')} steps={d.get('steps', '?')}"

    return _truncate(json.dumps(d, default=str), 60)


# ── Widgets ────────────────────────────────────────────────────────────


class StatsPanel(Static):
    """Summary statistics for the loaded session."""

    def render_stats(self, timeline: SessionTimeline) -> None:
        tools = timeline.tool_calls
        successes = sum(1 for t in tools if t.data.get("success"))
        failures = len(tools) - successes
        decisions = timeline.decisions
        errors = [e for e in timeline.events if e.kind == "error"]
        model_calls = [e for e in timeline.events if e.kind == "model_call"]

        total_tool_ms = sum(
            t.data.get("duration_ms", 0) for t in tools
        )
        total_tokens_in = sum(
            m.data.get("tokens", {}).get("input", 0) for m in model_calls
        )
        total_tokens_out = sum(
            m.data.get("tokens", {}).get("output", 0) for m in model_calls
        )

        text = (
            f"[bold]Session:[/bold] {timeline.session_id}\n"
            f"[bold]Duration:[/bold] {timeline.duration_s:.1f}s  │  "
            f"[bold]Steps:[/bold] {timeline.total_steps}\n"
            f"[bold]Events:[/bold] {len(timeline.events)} total\n"
            f"\n"
            f"[bold cyan]Tools:[/bold cyan] {len(tools)} "
            f"([green]✓{successes}[/green] [red]✗{failures}[/red]) "
            f"total={total_tool_ms}ms\n"
            f"[bold magenta]Model calls:[/bold magenta] {len(model_calls)} "
            f"(in={total_tokens_in:,} out={total_tokens_out:,})\n"
            f"[bold yellow]Decisions:[/bold yellow] {len(decisions)}\n"
            f"[bold red]Errors:[/bold red] {len(errors)}\n"
        )
        self.update(text)


class EventDetail(Static):
    """Detailed view of a single event."""

    def render_event(self, event: ReplayEvent | None) -> None:
        if event is None:
            self.update("[dim]Select an event to view details[/dim]")
            return

        lines = [
            f"[bold]{_icon_for_kind(event.kind)} {event.kind}[/bold]",
            f"[dim]Time:[/dim] {_format_ts(event.timestamp)}  "
            f"[dim]Step:[/dim] {event.step}",
            "",
        ]

        for key, value in event.data.items():
            if isinstance(value, dict):
                lines.append(f"[cyan]{key}:[/cyan]")
                for k, v in value.items():
                    lines.append(f"  {k}: {_truncate(str(v), 80)}")
            elif isinstance(value, list):
                lines.append(f"[cyan]{key}:[/cyan] ({len(value)} items)")
                for i, item in enumerate(value[:10]):
                    lines.append(f"  [{i}] {_truncate(str(item), 80)}")
                if len(value) > 10:
                    lines.append(f"  ... +{len(value) - 10} more")
            else:
                val_str = str(value)
                if len(val_str) > 200:
                    val_str = val_str[:200] + "…"
                lines.append(f"[cyan]{key}:[/cyan] {val_str}")

        self.update("\n".join(lines))


class FilterBar(Static):
    """Shows active filters."""

    def set_filter(
        self,
        kinds: set[str] | None = None,
        step_range: tuple[int, int] | None = None,
    ) -> None:
        parts = []
        if kinds:
            parts.append(f"kinds={','.join(sorted(kinds))}")
        if step_range:
            parts.append(f"steps={step_range[0]}-{step_range[1]}")
        if parts:
            self.update(f"[yellow]Filters:[/yellow] {' │ '.join(parts)}")
        else:
            self.update("[dim]No filters (press 'f' to filter, 'c' to clear)[/dim]")


# ── Main TUI App ──────────────────────────────────────────────────────


class ReplayApp(App):
    """Interactive session replay viewer."""

    CSS = """
    Screen {
        layout: horizontal;
    }
    #left-panel {
        width: 70%;
        height: 100%;
    }
    #right-panel {
        width: 30%;
        height: 100%;
        border-left: solid $accent;
    }
    #stats-panel {
        height: auto;
        max-height: 10;
        padding: 1;
        border-bottom: solid $surface;
    }
    #filter-bar {
        height: 1;
        padding: 0 1;
        background: $surface;
    }
    #event-table {
        height: 1fr;
    }
    #event-detail {
        height: 1fr;
        padding: 1;
        overflow-y: scroll;
    }
    #session-list {
        height: 100%;
    }
    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("f", "filter_menu", "Filter"),
        Binding("c", "clear_filter", "Clear Filter"),
        Binding("t", "toggle_tools_only", "Tools Only"),
        Binding("d", "toggle_decisions_only", "Decisions Only"),
        Binding("e", "toggle_errors_only", "Errors Only"),
        Binding("j", "next_event", "Next"),
        Binding("k", "prev_event", "Prev"),
        Binding("n", "next_step", "Next Step"),
        Binding("p", "prev_step", "Prev Step"),
        Binding("r", "refresh", "Refresh"),
    ]

    # State
    timeline: reactive[SessionTimeline | None] = reactive(None)
    active_kinds: set[str] | None = None
    active_step_range: tuple[int, int] | None = None
    _filtered_events: list[ReplayEvent] = []

    def __init__(
        self,
        timeline: SessionTimeline | None = None,
        sessions_path: Path | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._initial_timeline = timeline
        self._sessions_path = sessions_path

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="left-panel"):
                yield StatsPanel(id="stats-panel")
                yield FilterBar(id="filter-bar")
                yield DataTable(id="event-table")
            with Vertical(id="right-panel"):
                yield EventDetail(id="event-detail")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#event-table", DataTable)
        table.add_columns("Time", "Step", "Kind", "Summary")
        table.cursor_type = "row"
        table.zebra_stripes = True

        if self._initial_timeline:
            self.timeline = self._initial_timeline

    def watch_timeline(self, timeline: SessionTimeline | None) -> None:
        """React to timeline being set."""
        if timeline is None:
            return
        self._apply_filters()
        stats = self.query_one("#stats-panel", StatsPanel)
        stats.render_stats(timeline)
        self.title = f"Replay: {timeline.session_id}"

    def _apply_filters(self) -> None:
        """Recompute filtered events and update the table."""
        if self.timeline is None:
            return

        events = self.timeline.events
        if self.active_kinds:
            events = [e for e in events if e.kind in self.active_kinds]
        if self.active_step_range:
            lo, hi = self.active_step_range
            events = [e for e in events if lo <= e.step <= hi]

        self._filtered_events = events
        self._rebuild_table()

        filter_bar = self.query_one("#filter-bar", FilterBar)
        filter_bar.set_filter(self.active_kinds, self.active_step_range)

    def _rebuild_table(self) -> None:
        """Rebuild the DataTable from filtered events."""
        table = self.query_one("#event-table", DataTable)
        table.clear()
        for event in self._filtered_events:
            icon = _icon_for_kind(event.kind)
            table.add_row(
                _format_ts(event.timestamp),
                str(event.step),
                f"{icon} {event.kind}",
                _event_summary(event),
            )

    @on(DataTable.RowHighlighted)
    def on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Show detail for the highlighted row."""
        if event.cursor_row is not None and event.cursor_row < len(self._filtered_events):
            selected = self._filtered_events[event.cursor_row]
            detail = self.query_one("#event-detail", EventDetail)
            detail.render_event(selected)

    # ── Actions ────────────────────────────────────────────────────

    def action_toggle_tools_only(self) -> None:
        if self.active_kinds == {"tool_call"}:
            self.active_kinds = None
        else:
            self.active_kinds = {"tool_call"}
        self._apply_filters()

    def action_toggle_decisions_only(self) -> None:
        decision_kinds = {"adaptive_control", "decision_trace", "verifier"}
        if self.active_kinds == decision_kinds:
            self.active_kinds = None
        else:
            self.active_kinds = decision_kinds
        self._apply_filters()

    def action_toggle_errors_only(self) -> None:
        if self.active_kinds == {"error"}:
            self.active_kinds = None
        else:
            self.active_kinds = {"error"}
        self._apply_filters()

    def action_clear_filter(self) -> None:
        self.active_kinds = None
        self.active_step_range = None
        self._apply_filters()

    def action_filter_menu(self) -> None:
        """Placeholder — could open a filter input modal."""
        self.notify("Filters: t=tools, d=decisions, e=errors, c=clear")

    def action_next_event(self) -> None:
        table = self.query_one("#event-table", DataTable)
        table.action_cursor_down()

    def action_prev_event(self) -> None:
        table = self.query_one("#event-table", DataTable)
        table.action_cursor_up()

    def action_next_step(self) -> None:
        """Jump to the next step boundary."""
        table = self.query_one("#event-table", DataTable)
        current_row = table.cursor_row
        if current_row is None or current_row >= len(self._filtered_events):
            return
        current_step = self._filtered_events[current_row].step
        for i in range(current_row + 1, len(self._filtered_events)):
            if self._filtered_events[i].step > current_step:
                table.move_cursor(row=i)
                break

    def action_prev_step(self) -> None:
        """Jump to the previous step boundary."""
        table = self.query_one("#event-table", DataTable)
        current_row = table.cursor_row
        if current_row is None or current_row <= 0:
            return
        current_step = self._filtered_events[current_row].step
        for i in range(current_row - 1, -1, -1):
            if self._filtered_events[i].step < current_step:
                table.move_cursor(row=i)
                break

    def action_refresh(self) -> None:
        """Reload timeline from source."""
        if self._initial_timeline:
            # Re-read from file if path available
            self.notify("Refreshed")
            self._apply_filters()


# ── Session Picker ─────────────────────────────────────────────────────


class SessionPickerApp(App):
    """Pick a session to replay from available sessions."""

    CSS = """
    #session-list {
        height: 100%;
    }
    ListView {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("enter", "select", "Open"),
    ]

    def __init__(self, sessions_dir: Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._sessions_dir = sessions_dir
        self._sessions: list[tuple[str, Path]] = []
        self.selected_session: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield ListView(id="session-list")
        yield Footer()

    def on_mount(self) -> None:
        lv = self.query_one("#session-list", ListView)
        # Find all sessions with events.jsonl
        candidates: list[tuple[str, Path, float]] = []
        if self._sessions_dir.is_dir():
            for d in self._sessions_dir.iterdir():
                evf = d / "events.jsonl"
                if evf.is_file():
                    candidates.append((d.name, evf, evf.stat().st_mtime))
        candidates.sort(key=lambda x: x[2], reverse=True)
        for name, path, mtime in candidates[:50]:
            dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
            label = f"{name}  ({dt.strftime('%Y-%m-%d %H:%M')})"
            lv.append(ListItem(Label(label), name=name))
            self._sessions.append((name, path))

        if not candidates:
            lv.append(ListItem(Label("[dim]No sessions with events found[/dim]")))

        self.title = "Session Replay — Select a Session"

    def action_select(self) -> None:
        lv = self.query_one("#session-list", ListView)
        idx = lv.index
        if idx is not None and idx < len(self._sessions):
            self.selected_session = self._sessions[idx][0]
            self.exit()


# ── Entry Point ────────────────────────────────────────────────────────


def find_session_events(
    session_id: str | None = None,
    *,
    sessions_path: Path | None = None,
    last: bool = False,
) -> Path | None:
    """Locate events.jsonl for a session."""
    from kageha.config import sessions_dir as get_sessions_dir

    base = sessions_path or get_sessions_dir()
    if not base.is_dir():
        return None

    if session_id:
        candidate = base / session_id / "events.jsonl"
        if candidate.is_file():
            return candidate
        # Partial match
        for d in base.iterdir():
            if d.name.startswith(session_id) and (d / "events.jsonl").is_file():
                return d / "events.jsonl"
        return None

    if last:
        # Find most recently modified events.jsonl
        best: tuple[float, Path] | None = None
        for d in base.iterdir():
            evf = d / "events.jsonl"
            if evf.is_file():
                mtime = evf.stat().st_mtime
                if best is None or mtime > best[0]:
                    best = (mtime, evf)
        return best[1] if best else None

    return None


def run_replay_tui(
    session_id: str | None = None,
    *,
    last: bool = False,
    sessions_path: Path | None = None,
) -> None:
    """Main entry point for the replay TUI."""
    from kageha.config import sessions_dir as get_sessions_dir

    base = sessions_path or get_sessions_dir()

    if not session_id and not last:
        # Show session picker
        picker = SessionPickerApp(sessions_dir=base)
        picker.run()
        session_id = picker.selected_session
        if not session_id:
            return

    events_path = find_session_events(session_id, sessions_path=base, last=last)
    if events_path is None:
        from rich.console import Console
        Console().print(
            f"[red]No events found for session: {session_id or '(last)'}[/red]"
        )
        return

    timeline = load_timeline_from_events(events_path)
    if timeline is None or not timeline.events:
        from rich.console import Console
        Console().print("[red]Could not load timeline or no events in file[/red]")
        return

    app = ReplayApp(timeline=timeline, sessions_path=base)
    app.run()


def render_replay_static(
    session_id: str | None = None,
    *,
    last: bool = False,
    sessions_path: Path | None = None,
    step: int | None = None,
    kinds: set[str] | None = None,
    width: int = 120,
) -> str:
    """Non-interactive render — returns the timeline as a string.

    Use this for piping/scripting or when Textual is unavailable.
    """
    from kageha.config import sessions_dir as get_sessions_dir

    base = sessions_path or get_sessions_dir()
    events_path = find_session_events(session_id, sessions_path=base, last=last)
    if events_path is None:
        return f"No events found for session: {session_id or '(last)'}"

    timeline = load_timeline_from_events(events_path)
    if timeline is None:
        return "Could not load timeline"

    if step is not None:
        return timeline.render_step_detail(step)

    return timeline.render_timeline(kinds=kinds, width=width)
