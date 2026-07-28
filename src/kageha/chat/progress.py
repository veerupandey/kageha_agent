"""Transient terminal progress for interactive chat turns."""

from __future__ import annotations

import re
import signal
import weakref
from types import FrameType, TracebackType

from rich.console import Console
from rich.live import Live
from rich.text import Text


_CHECK_RE = re.compile(r"^[-*]\s+\[([ xX])\]\s+(.*)$")

# Active Live progress instances — refreshed on terminal resize (SIGWINCH).
_ACTIVE: weakref.WeakSet["TransientProgress"] = weakref.WeakSet()
_PREV_WINCH: object | None = None
_WINCH_INSTALLED = False


def _install_winch_handler() -> None:
    global _PREV_WINCH, _WINCH_INSTALLED
    if _WINCH_INSTALLED or not hasattr(signal, "SIGWINCH"):
        return
    try:
        _PREV_WINCH = signal.getsignal(signal.SIGWINCH)
    except Exception:  # noqa: BLE001
        _PREV_WINCH = None

    def _on_winch(signum: int, frame: FrameType | None) -> None:
        for progress in list(_ACTIVE):
            try:
                progress._on_terminal_resize()
            except Exception:  # noqa: BLE001
                pass
        prev = _PREV_WINCH
        if callable(prev):
            try:
                prev(signum, frame)
            except Exception:  # noqa: BLE001
                pass

    try:
        signal.signal(signal.SIGWINCH, _on_winch)
        _WINCH_INSTALLED = True
    except Exception:  # noqa: BLE001
        # Not on main thread / unsupported — Live still truncates on updates.
        pass


def _progress_text(message: str, *, max_width: int | None = None) -> Text:
    """Colorful one-line live status (truncated to the current terminal width)."""
    msg = (message or "").strip() or "…"
    low = msg.lower()
    if "waiting" in low:
        style = "bold #fbbf24"
        glyph = "⏸"
    elif "think" in low or "reason" in low:
        style = "bold #38bdf8"
        glyph = "✦"
    elif "step" in low or "tool" in low or "working" in low:
        style = "bold #2dd4bf"
        glyph = "▸"
    elif "error" in low or "fail" in low:
        style = "bold #fb7185"
        glyph = "!"
    else:
        style = "bold #22d3ee"
        glyph = "·"
    # Glyph + spaces take ~3 cells; keep the bar on one line after resize.
    if max_width is not None and max_width > 8 and len(msg) > max_width - 4:
        msg = msg[: max_width - 5].rstrip() + "…"
    return Text.assemble((f" {glyph} ", style), (msg, style))


class TransientProgress:
    """Show agent status live; detailed mode keeps reasoning + todo checkmarks."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        detailed: bool = False,
        console: Console | None = None,
    ) -> None:
        self.console = console or Console()
        self.enabled = enabled
        self.detailed = detailed
        self._live: Live | None = None
        self._last_status = ""
        self._waiting_for_input = False
        self._todo_done = 0
        self._todo_total = 0
        self._step_label = ""

    @property
    def transient(self) -> bool:
        return self.enabled and not self.detailed and self.console.is_terminal

    def _status_width(self) -> int:
        try:
            pinned = getattr(self.console, "_width", None)
            width = int(pinned if pinned is not None else self.console.width)
            return max(24, width - 2)
        except Exception:  # noqa: BLE001
            return 78

    def _on_terminal_resize(self) -> None:
        """Re-measure and redraw the Live line after the pane changes size."""
        if self._live is None or not self._last_status:
            return
        self._live.update(
            _progress_text(self._last_status, max_width=self._status_width()),
            refresh=True,
        )

    def __enter__(self) -> "TransientProgress":
        _ACTIVE.add(self)
        _install_winch_handler()
        if self.transient:
            self._start_live("Starting…")
        return self

    def update(self, message: str) -> None:
        if not self.enabled:
            return
        raw = (message or "").rstrip()
        low = " ".join(raw.split()).lower()

        # A Rich Live line and a terminal input prompt cannot safely own the
        # cursor at the same time. Suspend Live before ask_human writes `>`.
        if "ask_human" in low and ("tools:" in low or "action:" in low):
            if not self._waiting_for_input:
                self._waiting_for_input = True
                self.close()
                self._last_status = "Waiting for your answer…"
                if not self.transient:
                    self.console.print("Waiting for your answer…")
            return
        if self._waiting_for_input:
            if "ask_human" in low and "←" in raw:
                self._waiting_for_input = False
                self._last_status = ""
                if self.transient:
                    self._start_live("Working…")
            else:
                return

        step_m = re.search(r"step\s+(\d+)\s*/\s*(\d+)", low)
        if step_m:
            self._step_label = f"Step {step_m.group(1)}/{step_m.group(2)}"

        # Multi-line checklist / reasoning / subagent board — sticky in detailed mode.
        if (
            _is_checklist_log(raw)
            or _is_reasoning_log(raw)
            or _is_subagent_board(raw)
        ):
            self._remember_todo_counts(raw)
            if _is_subagent_board(raw):
                rendered = _render_subagent_board(raw)
            elif _is_checklist_log(raw):
                rendered = _render_checklist(raw)
            else:
                rendered = _render_reasoning(raw)
            if not rendered:
                return
            if self.detailed or not self.transient:
                if rendered != self._last_status:
                    self._last_status = rendered
                    self.console.print(rendered)
                return
            # Compact: fold into a one-line status with todo fraction.
            if _is_subagent_board(raw):
                compact = _compact_with_todos(
                    _friendly_status(raw.splitlines()[0]),
                    self._step_label,
                    self._todo_done,
                    self._todo_total,
                )
            else:
                compact = _compact_with_todos(
                    "Updating checklist…" if _is_checklist_log(raw) else "Thinking…",
                    self._step_label,
                    self._todo_done,
                    self._todo_total,
                )
            self._set_status(compact)
            return

        compact = raw if self.detailed else _friendly_status(raw)
        if not compact:
            return
        if not self.detailed:
            compact = _compact_with_todos(
                compact, self._step_label, self._todo_done, self._todo_total
            )
        if compact == self._last_status:
            return
        self._set_status(compact)

    def _remember_todo_counts(self, raw: str) -> None:
        m = re.search(r"\[kageha\]\s+(todos|goals):\s*(\d+)\s*/\s*(\d+)", raw, re.I)
        if m:
            self._todo_done = int(m.group(2))
            self._todo_total = int(m.group(3))

    def _set_status(self, compact: str) -> None:
        self._last_status = compact
        width = self._status_width()
        render = compact
        if "\n" not in render and len(render) > width:
            render = render[: width - 1].rstrip() + "…"
        text = _progress_text(render, max_width=width)
        if self._live is not None:
            self._live.update(text, refresh=True)
        else:
            self.console.print(text)

    def _start_live(self, initial: str) -> None:
        if self._live is not None:
            return
        self._last_status = initial
        self._live = Live(
            _progress_text(initial, max_width=self._status_width()),
            console=self.console,
            refresh_per_second=14,
            transient=True,
            # Re-layout when the host terminal (IDE pane) changes size.
            vertical_overflow="ellipsis",
        )
        self._live.start()

    def close(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
        _ACTIVE.discard(self)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _is_checklist_log(message: str) -> bool:
    head = (message or "").lstrip()
    return bool(re.match(r"\[kageha\]\s+(todos|goals):\s*\d+\s*/\s*\d+", head, re.I))


def _is_reasoning_log(message: str) -> bool:
    return "reasoning:" in (message or "").lower()


def _render_checklist(message: str) -> str:
    lines = (message or "").splitlines()
    if not lines:
        return ""
    header = lines[0]
    m = re.search(r"(todos|goals):\s*(\d+)\s*/\s*(\d+)", header, re.I)
    kind = (m.group(1) if m else "todos").capitalize()
    frac = f"{m.group(2)}/{m.group(3)}" if m else ""
    out = [f"{kind} {frac}".rstrip()]
    for line in lines[1:]:
        cm = _CHECK_RE.match(line.strip())
        if not cm:
            continue
        mark = "✓" if cm.group(1).lower() == "x" else "○"
        body = cm.group(2).strip()
        body = re.sub(r"^(p\d+|g\d+):\s*", "", body, flags=re.I)
        if len(body) > 100:
            body = body[:97].rstrip() + "…"
        out.append(f"  {mark} {body}")
    return "\n".join(out) if len(out) > 1 else out[0]


def _render_reasoning(message: str) -> str:
    text = (message or "").strip()
    # Strip "[kageha]   reasoning: " prefix.
    text = re.sub(r"^\[kageha\]\s*reasoning:\s*", "", text, flags=re.I)
    text = " ".join(text.split())
    if not text:
        return ""
    if len(text) > 360:
        text = text[:357].rstrip() + "…"
    return f"Reasoning: {text}"


def _compact_with_todos(
    status: str, step_label: str, done: int, total: int
) -> str:
    parts: list[str] = []
    if step_label:
        parts.append(step_label)
    if total > 0:
        parts.append(f"Todos {done}/{total}")
    parts.append(status)
    # Dedupe if status already includes step wording.
    seen: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    return " · ".join(seen)


def _is_subagent_board(message: str) -> bool:
    head = (message or "").lstrip()
    return bool(
        re.match(
            r"\[kageha\]\s+spawn_(subagents|task_graph):\s*\d+\s+(tasks|nodes)",
            head,
            re.I,
        )
    )


def _render_subagent_board(message: str) -> str:
    """Keep assignment lines readable in verbose / non-transient progress."""
    lines = [ln.rstrip() for ln in (message or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    out = [lines[0].removeprefix("[kageha] ").strip()]
    for ln in lines[1:]:
        out.append(ln if ln.startswith("  ") else f"  {ln}")
    return "\n".join(out)


def _friendly_status(message: str) -> str:
    """Translate controller telemetry into conversation-level progress."""
    compact = " ".join((message or "").split())
    low = compact.lower()
    if "tools:" in low and "ask_human" in low:
        return "Waiting for your answer…"
    if "spawn_subagents:" in low or "spawn_task_graph:" in low:
        m = re.search(r"(\d+)\s+(tasks|nodes)", low)
        n = m.group(1) if m else "?"
        kind = "subagents" if "spawn_subagents" in low else "graph nodes"
        return f"Spawning {n} {kind}…"
    if "reasoning:" in low:
        return "Thinking…"
    if "action:" in low:
        # Prefer a short tool name when present.
        m = re.search(r"action:\s*([a-z0-9_]+)", low)
        if m:
            return f"Running {m.group(1)}…"
        return "Working…"
    if "planning" in low or "plan ready" in low or "] plan:" in low:
        return "Planning…"
    if "model:" in low and "→" in compact:
        # Failover-first: keep the concrete A → B line visible.
        m = re.search(r"model:\s*.+", compact, re.I)
        return (m.group(0) if m else compact)[:180]
    if "thinking" in low or "model=" in low:
        return "Thinking…"
    if "tools:" in low or "←" in compact:
        m = re.search(r"tools:\s*([^(\n]+)", low)
        if m:
            names = m.group(1).strip().rstrip(",")
            if names and "ask_human" not in names:
                first = names.split(",")[0].strip()
                return f"Running {first}…"
        return "Working…"
    if "verify=" in low or "progress=" in low:
        return "Checking the result…"
    if "reply:" in low:
        return "Checking the result…"
    if any(
        marker in low
        for marker in ("run_id=", "workspace=", "task=", "mcp:", "auto-loaded")
    ):
        return ""
    return compact
