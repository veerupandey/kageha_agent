"""Session replay — terminal-based timeline visualization.

Provides a step-through view of agent sessions showing:
- Timeline of decisions, tool calls, and state transitions
- Tool call inputs/outputs with timing
- Adaptive control decisions with reasoning
- Verification results
- Budget consumption

Usage:
    kageha runtime replay <session_id>
    kageha runtime replay --last
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ReplayEvent:
    """A single event in the replay timeline."""

    timestamp: float
    kind: str
    step: int = 0
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def time_str(self) -> str:
        dt = datetime.fromtimestamp(self.timestamp, tz=timezone.utc)
        return dt.strftime("%H:%M:%S.%f")[:-3]

    def render_compact(self, width: int = 100) -> str:
        """Render a compact one-line summary."""
        prefix = f"  {self.time_str} │ step={self.step:02d} │ "
        content = self._render_content()
        max_content = width - len(prefix)
        if len(content) > max_content:
            content = content[: max_content - 1] + "…"
        return prefix + content

    def _render_content(self) -> str:
        kind = self.kind
        d = self.data

        if kind == "tool_call":
            tool = d.get("tool", "?")
            dur = d.get("duration_ms", 0)
            status = "✓" if d.get("success") else "✗"
            return f"{status} {tool}({_truncate(str(d.get('args', '')), 40)}) [{dur}ms]"

        if kind == "model_call":
            model = d.get("model", "?")
            tokens = d.get("tokens", {})
            return f"🤖 {model} (in={tokens.get('input', '?')} out={tokens.get('output', '?')})"

        if kind == "adaptive_control":
            decision = d.get("decision", "?")
            reason = d.get("reasoning", "")
            return f"⚙ {decision}: {_truncate(reason, 60)}"

        if kind == "verifier":
            status = d.get("status", "?")
            icon = "✓" if status == "pass" else "✗" if status == "fail" else "⟳"
            return f"{icon} verify={status} defects={len(d.get('defects', []))}"

        if kind == "stop":
            rule = d.get("rule", "?")
            return f"⏹ STOP: {rule}"

        if kind == "decision_trace":
            cat = d.get("category", "?")
            decision = d.get("decision", "?")
            return f"📝 [{cat}] {_truncate(decision, 60)}"

        if kind == "user_message":
            text = d.get("text", "")
            return f"👤 {_truncate(text, 70)}"

        if kind == "assistant_message":
            text = d.get("text", "")
            return f"🤖 {_truncate(text, 70)}"

        if kind == "error":
            msg = d.get("message", "?")
            return f"❌ {_truncate(msg, 70)}"

        if kind == "checkpoint":
            return f"📌 checkpoint at step {self.step}"

        return f"{kind}: {_truncate(json.dumps(d, default=str), 60)}"


@dataclass
class SessionTimeline:
    """A complete session timeline for replay."""

    session_id: str
    events: list[ReplayEvent] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        if len(self.events) < 2:
            return 0.0
        return self.events[-1].timestamp - self.events[0].timestamp

    @property
    def total_steps(self) -> int:
        if not self.events:
            return 0
        return max(e.step for e in self.events)

    @property
    def tool_calls(self) -> list[ReplayEvent]:
        return [e for e in self.events if e.kind == "tool_call"]

    @property
    def decisions(self) -> list[ReplayEvent]:
        return [e for e in self.events if e.kind in ("adaptive_control", "decision_trace")]

    def render_summary(self) -> str:
        """Render a high-level session summary."""
        tools = self.tool_calls
        successes = sum(1 for t in tools if t.data.get("success"))
        failures = len(tools) - successes

        lines = [
            f"Session: {self.session_id}",
            f"Duration: {self.duration_s:.1f}s | Steps: {self.total_steps}",
            f"Tool calls: {len(tools)} (✓{successes} ✗{failures})",
            f"Decisions: {len(self.decisions)}",
            f"Events: {len(self.events)} total",
            "",
        ]
        return "\n".join(lines)

    def render_timeline(
        self,
        *,
        step_range: tuple[int, int] | None = None,
        kinds: set[str] | None = None,
        width: int = 100,
    ) -> str:
        """Render the full timeline with optional filtering."""
        lines = [self.render_summary(), "Timeline:", "─" * width]

        current_step = -1
        for event in self.events:
            # Filter by step range
            if step_range:
                if event.step < step_range[0] or event.step > step_range[1]:
                    continue
            # Filter by kind
            if kinds and event.kind not in kinds:
                continue

            # Step separator
            if event.step != current_step:
                current_step = event.step
                lines.append(f"")
                lines.append(f"  ═══ Step {current_step} ═══")

            lines.append(event.render_compact(width))

        lines.append("")
        lines.append("─" * width)
        lines.append(f"End of timeline ({len(self.events)} events)")
        return "\n".join(lines)

    def render_step_detail(self, step: int) -> str:
        """Render detailed view of a single step."""
        step_events = [e for e in self.events if e.step == step]
        if not step_events:
            return f"No events at step {step}"

        lines = [f"Step {step} Detail ({len(step_events)} events):", ""]
        for event in step_events:
            lines.append(f"  {event.time_str} [{event.kind}]")
            for key, value in event.data.items():
                val_str = _truncate(str(value), 80)
                lines.append(f"    {key}: {val_str}")
            lines.append("")
        return "\n".join(lines)


def load_timeline_from_events(events_path: Path) -> SessionTimeline | None:
    """Load a timeline from an events.jsonl file."""
    if not events_path.is_file():
        return None

    session_id = events_path.parent.name
    timeline = SessionTimeline(session_id=session_id)

    try:
        with events_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    # Support both formats: {timestamp, kind, step, data}
                    # and the Kageha native: {ts, kind, step, ...rest as data}
                    ts = raw.get("timestamp") or raw.get("ts") or 0
                    kind = raw.get("kind", "unknown")
                    step = raw.get("step", 0)
                    # Everything except ts/kind/step goes into data
                    data = raw.get("data") or {
                        k: v for k, v in raw.items()
                        if k not in ("ts", "timestamp", "kind", "step")
                    }
                    event = ReplayEvent(
                        timestamp=float(ts),
                        kind=kind,
                        step=step,
                        data=data if isinstance(data, dict) else {},
                    )
                    timeline.events.append(event)
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
    except OSError:
        return None

    return timeline


def load_timeline_from_store(store_path: Path) -> SessionTimeline | None:
    """Load a timeline from a RuntimeStore SQLite database."""
    try:
        import sqlite3
    except ImportError:
        return None

    if not store_path.is_file():
        return None

    session_id = store_path.stem
    timeline = SessionTimeline(session_id=session_id)

    try:
        conn = sqlite3.connect(str(store_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM events ORDER BY timestamp ASC"
        )
        for row in cursor:
            data = {}
            try:
                data = json.loads(row["data"]) if row["data"] else {}
            except (json.JSONDecodeError, KeyError):
                pass
            event = ReplayEvent(
                timestamp=row["timestamp"],
                kind=row["kind"],
                step=data.get("step", 0),
                data=data,
            )
            timeline.events.append(event)
        conn.close()
    except Exception:  # noqa: BLE001
        return None

    return timeline


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis."""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"
