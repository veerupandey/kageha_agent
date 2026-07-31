"""Transient terminal progress for interactive chat turns.

Cursor-CLI-style: one live status line (spinner + elapsed), plus sticky
activity lines when tools start/finish so the user can see what happened.
"""

from __future__ import annotations

import re
import signal
import threading
import time
import weakref
from types import FrameType, TracebackType

from rich.console import Console
from rich.text import Text


_CHECK_RE = re.compile(r"^[-*]\s+\[([ xX])\]\s+(.*)$")
_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

# Friendly running labels for common tools (keep in sync with WebUI pulse copy).
_TOOL_LABELS = {
    "read_file": "Reading file",
    "write_file": "Writing file",
    "edit_file": "Editing file",
    "list_dir": "Listing directory",
    "glob": "Finding files",
    "grep": "Searching code",
    "search": "Searching",
    "web_search": "Searching the web",
    "web_fetch": "Fetching page",
    "browser": "Using browser",
    "browser_open": "Opening browser",
    "browser_navigate": "Opening page",
    "browser_click": "Clicking in browser",
    "browser_type": "Typing in browser",
    "browser_snapshot": "Reading page",
    "shell": "Running shell",
    "bash": "Running shell",
    "run_terminal_cmd": "Running shell",
    "memory_search": "Searching memory",
    "memory_write": "Saving memory",
    "memory_read": "Reading memory",
    "todo_write": "Updating todos",
    "todo_read": "Reading todos",
    "ask_human": "Waiting for your answer",
    "request_approval": "Requesting approval",
    "research": "Researching",
    "deep_research": "Deep research",
    "skill_run": "Running skill",
    "skill_load": "Loading skill",
    "spawn_subagents": "Spawning subagents",
    "spawn_task_graph": "Running task graph",
    "nano_banana_generate": "Generating image",
    "nano_banana_edit": "Editing image",
    "fal_generate_image": "Generating image",
    "fal_image_to_video": "Generating video",
    "download_file": "Downloading file",
    "parallel_web_search": "Searching the web",
    "parallel_web_fetch": "Fetching pages",
    "research_run": "Researching",
    "mcp_list_servers": "Checking MCP servers",
    "skill_list": "Browsing skills",
    "skill_manage": "Updating skill",
}

# Active progress instances — refreshed on terminal resize (SIGWINCH).
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
        # Not on main thread / unsupported — status still truncates on updates.
        pass


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _tool_verb(name: str) -> str:
    tool = (name or "").strip()
    if not tool:
        return "Working"
    if tool in _TOOL_LABELS:
        return _TOOL_LABELS[tool]
    if tool.startswith("browser_"):
        return "Using browser"
    if tool.startswith("computer_"):
        return "Using desktop"
    if tool.startswith("memory_"):
        return "Using memory"
    pretty = tool.replace("_", " ").strip()
    return f"Running {pretty}"


def _progress_text(
    message: str,
    *,
    max_width: int | None = None,
    glyph: str | None = None,
) -> Text:
    """Colorful one-line live status (truncated to the current terminal width)."""
    msg = (message or "").strip() or "…"
    low = msg.lower()
    if glyph is None:
        if "waiting" in low:
            style = "bold #fbbf24"
            glyph = "⏸"
        elif "think" in low or "reason" in low:
            style = "bold #38bdf8"
            glyph = "✦"
        elif "step" in low or "tool" in low or "working" in low or "running" in low:
            style = "bold #2dd4bf"
            glyph = "▸"
        elif "error" in low or "fail" in low:
            style = "bold #fb7185"
            glyph = "!"
        else:
            style = "bold #22d3ee"
            glyph = "·"
    else:
        if "waiting" in low:
            style = "bold #fbbf24"
        elif "think" in low or "reason" in low:
            style = "bold #38bdf8"
        elif "error" in low or "fail" in low:
            style = "bold #fb7185"
        elif "step" in low or "tool" in low or "working" in low or "running" in low:
            style = "bold #2dd4bf"
        else:
            style = "bold #22d3ee"
    # Glyph + spaces take ~3 cells; keep the bar on one line after resize.
    if max_width is not None and max_width > 8 and len(msg) > max_width - 4:
        msg = msg[: max_width - 5].rstrip() + "…"
    return Text.assemble((f" {glyph} ", style), (msg, style))


def _activity_text(kind: str, message: str) -> Text:
    """Sticky scrollback line for a finished (or started) activity."""
    msg = (message or "").strip()
    if kind == "done":
        return Text.assemble(
            ("  ✓ ", "bold #34d399"),
            (msg, "#cbd5e1"),
        )
    if kind == "fail":
        return Text.assemble(
            ("  ! ", "bold #fb7185"),
            (msg, "#fda4af"),
        )
    if kind == "wait":
        return Text.assemble(
            ("  ⏸ ", "bold #fbbf24"),
            (msg, "#fde68a"),
        )
    return Text.assemble(
        ("  ▸ ", "bold #2dd4bf"),
        (msg, "#cbd5e1"),
    )


class TransientProgress:
    """Show agent status on one CR-updated line (no Rich Live).

    Rich Live redraws pin the Cursor/VS Code terminal viewport and block
    scrollback during long turns — use a plain carriage-return status instead.

    Compact mode mirrors Cursor CLI: a spinner + elapsed timer while work is
    in flight, and sticky tool lines left in scrollback as steps complete.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        detailed: bool = False,
        console: Console | None = None,
        heartbeat_interval: float = 0.12,
        show_elapsed: bool = True,
        sticky_activity: bool = True,
    ) -> None:
        self.console = console or Console()
        self.enabled = enabled
        self.detailed = detailed
        self.heartbeat_interval = float(heartbeat_interval or 0.0)
        self.show_elapsed = show_elapsed
        self.sticky_activity = sticky_activity
        # True while a CR status line is open (compat name: tests use _live).
        self._live: bool | None = None
        self._last_status = ""
        self._base_status = ""
        self._waiting_for_input = False
        # True while StreamReply owns the cursor — never sticky-print Step lines.
        self._paused_for_stream = False
        self._todo_done = 0
        self._todo_total = 0
        self._step_label = ""
        self._started_at = time.monotonic()
        self._phase_started_at = self._started_at
        self._spinner_idx = 0
        self._active_tool = ""
        self._lock = threading.RLock()
        self._stop_heartbeat = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

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
        """Re-measure and redraw the status line after the pane changes size."""
        with self._lock:
            if not self._live or not self._base_status:
                return
            self._write_status_line(self._compose_display(self._base_status))

    def __enter__(self) -> "TransientProgress":
        _ACTIVE.add(self)
        _install_winch_handler()
        self._started_at = time.monotonic()
        self._phase_started_at = self._started_at
        if self.transient:
            self._start_live("Starting…")
            if self._can_heartbeat():
                self._start_heartbeat()
        return self

    def update(self, message: str) -> None:
        if not self.enabled:
            return
        raw = (message or "").rstrip()
        low = " ".join(raw.split()).lower()

        with self._lock:
            # Streaming reply owns the terminal — remember status but do not print
            # sticky "Step N/40" lines under the live panel.
            if self._paused_for_stream:
                step_m = re.search(r"step\s+(\d+)\s*/\s*(\d+)", low)
                if step_m:
                    self._step_label = f"Step {step_m.group(1)}/{step_m.group(2)}"
                self._remember_todo_counts(raw)
                return

            # A Rich Live line and a terminal input prompt cannot safely own the
            # cursor at the same time. Suspend Live before ask_human writes `>`.
            if "ask_human" in low and ("tools:" in low or "action:" in low):
                if not self._waiting_for_input:
                    self._waiting_for_input = True
                    self._signal_stop_heartbeat()
                    self._clear_status_line()
                    self._last_status = "Waiting for your answer…"
                    self._base_status = self._last_status
                    if self.sticky_activity and self.transient and not self.detailed:
                        self._sticky_print(
                            _activity_text("wait", "Waiting for your answer…")
                        )
                    elif not self.transient:
                        self.console.print("Waiting for your answer…")
                return
            if self._waiting_for_input:
                if "ask_human" in low and "←" in raw:
                    self._waiting_for_input = False
                    self._last_status = ""
                    self._base_status = ""
                    self._phase_started_at = time.monotonic()
                    if self.transient:
                        _ACTIVE.add(self)
                        self._start_live("Working…")
                        if self._can_heartbeat():
                            self._start_heartbeat()
                else:
                    return

            step_m = re.search(r"step\s+(\d+)\s*/\s*(\d+)", low)
            if step_m:
                self._step_label = f"Step {step_m.group(1)}/{step_m.group(2)}"

            # Tool completion → leave a sticky receipt, then resume working.
            if (
                self.sticky_activity
                and self.transient
                and not self.detailed
                and _is_tool_result_log(raw)
            ):
                done_line = _tool_result_activity(raw)
                if done_line:
                    failed = _tool_result_failed(raw)
                    self._sticky_print(
                        _activity_text("fail" if failed else "done", done_line)
                    )
                    self._active_tool = ""
                    self._phase_started_at = time.monotonic()
                    compact = _compact_with_todos(
                        "Thinking…",
                        self._step_label,
                        self._todo_done,
                        self._todo_total,
                    )
                    self._set_status(compact, reset_phase=False)
                    return

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
                        self._base_status = rendered
                        self.console.print(rendered)
                    return
                # Compact: fold into a one-line status with todo fraction.
                if _is_subagent_board(raw):
                    if self.sticky_activity and self.transient:
                        head = _friendly_status(raw.splitlines()[0]) or "Spawning subagents…"
                        self._sticky_print(_activity_text("start", head.rstrip("…")))
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

            # Tool start → sticky activity + live "Running …" status.
            if (
                self.sticky_activity
                and self.transient
                and not self.detailed
                and ("action:" in low or re.search(r"\btools:\s*[a-z0-9_]", low))
            ):
                tool_name, hint = _extract_tool_activity(raw)
                if tool_name and tool_name != "ask_human":
                    label = _tool_verb(tool_name)
                    sticky = label if not hint else f"{label} · {hint}"
                    if tool_name != self._active_tool:
                        self._sticky_print(_activity_text("start", sticky))
                        self._active_tool = tool_name
                        self._phase_started_at = time.monotonic()
                    compact = _compact_with_todos(
                        f"{label}…" if not hint else f"{label} · {hint}",
                        self._step_label,
                        self._todo_done,
                        self._todo_total,
                    )
                    self._set_status(compact, reset_phase=False)
                    return

            compact = raw if self.detailed else _friendly_status(raw)
            if not compact:
                return
            if not self.detailed:
                compact = _compact_with_todos(
                    compact, self._step_label, self._todo_done, self._todo_total
                )
            if compact == self._base_status:
                return
            # New phase of work — reset per-phase timer for clearer progress.
            if not any(
                marker in low
                for marker in ("model=", "tokens=", "usd~", "cache_read=")
            ):
                self._phase_started_at = time.monotonic()
            self._set_status(compact)

    def _remember_todo_counts(self, raw: str) -> None:
        m = re.search(r"\[kageha\]\s+(todos|goals):\s*(\d+)\s*/\s*(\d+)", raw, re.I)
        if m:
            self._todo_done = int(m.group(2))
            self._todo_total = int(m.group(3))

    def _compose_display(self, compact: str) -> str:
        """Attach elapsed time for the live status line."""
        text = compact
        if self.show_elapsed and self.transient and "\n" not in text:
            elapsed = _format_elapsed(time.monotonic() - self._started_at)
            if elapsed not in text:
                text = f"{text} · {elapsed}"
        return text

    def _set_status(self, compact: str, *, reset_phase: bool = True) -> None:
        if reset_phase and compact != self._base_status:
            # Keep phase timer unless caller already adjusted it.
            pass
        self._base_status = compact
        display = self._compose_display(compact)
        self._last_status = display
        if self.transient:
            if not self._live:
                self._start_live(display)
            else:
                self._write_status_line(display)
        else:
            width = self._status_width()
            render = display
            if "\n" not in render and len(render) > width:
                render = render[: width - 1].rstrip() + "…"
            self.console.print(_progress_text(render, max_width=width))

    def _write_status_line(self, compact: str) -> None:
        """Carriage-return one status line (does not pin scrollback like Live)."""
        width = self._status_width()
        render = compact
        if "\n" not in render and len(render) > width:
            render = render[: width - 1].rstrip() + "…"
        glyph = None
        if self.transient and self._can_heartbeat():
            glyph = _SPINNER_FRAMES[self._spinner_idx % len(_SPINNER_FRAMES)]
        text = _progress_text(render, max_width=width, glyph=glyph)
        # Render to plain+ansi string, then CR-overwrite the current line.
        try:
            with self.console.capture() as cap:
                self.console.print(text, end="")
            line = cap.get().rstrip("\n")
            pad = max(0, width - len(Text.from_ansi(line).plain))
            file = getattr(self.console, "file", None)
            if file is not None:
                file.write("\r" + line + (" " * pad))
                file.flush()
            else:
                self.console.print(text)
        except Exception:  # noqa: BLE001
            self.console.print(text)

    def _clear_status_line(self) -> None:
        if not self._live:
            return
        width = self._status_width()
        file = getattr(self.console, "file", None)
        if file is not None:
            try:
                file.write("\r" + (" " * width) + "\r")
                file.flush()
            except Exception:  # noqa: BLE001
                pass
        self._live = None

    def _sticky_print(self, text: Text | str) -> None:
        """Leave a permanent activity line in scrollback under the status bar."""
        was_live = bool(self._live)
        base = self._base_status
        self._clear_status_line()
        self.console.print(text)
        if was_live and self.transient and not self._paused_for_stream and not self._waiting_for_input:
            resume = base or "Working…"
            self._start_live(self._compose_display(resume))

    def _start_live(self, initial: str) -> None:
        # `initial` may already include elapsed; store base separately when possible.
        if " · " in initial and self.show_elapsed:
            # Prefer the last known base status if this is a redraw of composed text.
            if self._base_status and initial.startswith(self._base_status):
                pass
            elif not self._base_status:
                self._base_status = initial
        else:
            self._base_status = initial
        display = (
            initial
            if initial != self._base_status and " · " in initial
            else self._compose_display(self._base_status or initial)
        )
        self._last_status = display
        if self._live:
            self._write_status_line(display)
            return
        self._live = True
        self._write_status_line(display)

    def _can_heartbeat(self) -> bool:
        """Heartbeat only on a real TTY — keeps unit tests deterministic."""
        if self.heartbeat_interval <= 0 or not self.transient:
            return False
        file = getattr(self.console, "file", None)
        try:
            return bool(file is not None and file.isatty())
        except Exception:  # noqa: BLE001
            return False

    def _start_heartbeat(self) -> None:
        if not self._can_heartbeat():
            return
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            self._stop_heartbeat.clear()
            return
        self._stop_heartbeat.clear()

        def _loop() -> None:
            while not self._stop_heartbeat.wait(self.heartbeat_interval):
                # Non-blocking lock: never deadlock against update()/close().
                if not self._lock.acquire(blocking=False):
                    continue
                try:
                    if (
                        not self._live
                        or self._paused_for_stream
                        or self._waiting_for_input
                        or not self._base_status
                    ):
                        continue
                    self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER_FRAMES)
                    self._write_status_line(self._compose_display(self._base_status))
                finally:
                    self._lock.release()

        self._heartbeat_thread = threading.Thread(
            target=_loop,
            name="kageha-progress-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _signal_stop_heartbeat(self) -> None:
        self._stop_heartbeat.set()

    def _join_heartbeat(self) -> None:
        thread = self._heartbeat_thread
        self._heartbeat_thread = None
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=0.4)

    def suspend(self) -> None:
        """Clear the status line so a stream writer can own the cursor."""
        with self._lock:
            self._paused_for_stream = True
            self._clear_status_line()

    def resume(self, message: str = "Working…") -> None:
        """Restart the status line after streaming pauses."""
        with self._lock:
            self._paused_for_stream = False
            if not self.transient or self._waiting_for_input:
                return
            self._phase_started_at = time.monotonic()
            self._active_tool = ""
            if not self._live:
                self._start_live(message)
                if self._can_heartbeat():
                    self._start_heartbeat()

    def close(self) -> None:
        self._signal_stop_heartbeat()
        with self._lock:
            self._paused_for_stream = False
            self._clear_status_line()
            _ACTIVE.discard(self)
        self._join_heartbeat()

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


def _is_tool_result_log(message: str) -> bool:
    return bool(re.search(r"\[kageha\]\s*←\s*[a-z0-9_]+", message or "", re.I))


def _tool_result_failed(message: str) -> bool:
    low = (message or "").lower()
    return any(
        marker in low
        for marker in (
            "error:",
            "denied:",
            "failed",
            "traceback",
            "exception",
        )
    )


def _tool_result_activity(message: str) -> str:
    m = re.search(
        r"←\s*([a-z0-9_]+)\s*:\s*(.*)$",
        " ".join((message or "").split()),
        re.I,
    )
    if not m:
        return ""
    tool = m.group(1)
    preview = (m.group(2) or "").strip()
    label = _tool_verb(tool)
    hint = _short_hint(preview, tool=tool)
    if hint:
        return f"{label} · {hint}"
    return label


def _extract_tool_activity(message: str) -> tuple[str, str]:
    compact = " ".join((message or "").split())
    low = compact.lower()
    action = re.search(r"action:\s*([a-z0-9_]+)\s*(.*)$", compact, re.I)
    if action:
        tool = action.group(1)
        hint = _short_hint(action.group(2) or "", tool=tool)
        return tool, hint
    tools = re.search(r"tools:\s*([^(\n]+)", low)
    if tools:
        first = tools.group(1).strip().rstrip(",").split(",")[0].strip()
        if first:
            return first, ""
    return "", ""


def _short_hint(raw: str, *, tool: str = "", limit: int = 56) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    # Prefer path-like or command-like snippets from JSON-ish args.
    # Handles both {"path":"foo"} and path=foo / path: foo.
    path_m = re.search(
        r'["\']?(?:path|file|filename|url|query|command|cmd)["\']?\s*[:=]\s*'
        r'["\']([^"\']+)["\']',
        text,
        re.I,
    )
    if not path_m:
        path_m = re.search(
            r'["\']?(?:path|file|filename|url|query|command|cmd)["\']?\s*[:=]\s*'
            r'([^"\',}\s]+)',
            text,
            re.I,
        )
    if path_m:
        text = path_m.group(1).strip()
    else:
        # Strip outer braces / quotes from action dumps.
        text = re.sub(r"^\{+\s*", "", text)
        text = re.sub(r"\s*\}+$", "", text)
        text = text.strip(" \"'")
    text = " ".join(text.split())
    if tool in {"bash", "shell", "run_terminal_cmd"} and len(text) > limit:
        # Keep the start of the command — usually the most informative.
        return text[: limit - 1].rstrip() + "…"
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


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
        tool, hint = _extract_tool_activity(compact)
        if tool:
            label = _tool_verb(tool)
            if hint:
                return f"{label} · {hint}"
            return f"{label}…"
        return "Working…"
    if "planning degraded" in low or "fallback plan" in low:
        return "Planning (fallback)…"
    if "planning" in low or "plan ready" in low or "] plan:" in low:
        return "Planning…"
    if "model retry" in low or "model: retrying" in low or "retry after" in low:
        return "Retrying model…"
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
                return f"{_tool_verb(first)}…"
        if "←" in compact:
            return "Thinking…"
        return "Working…"
    if "verify=" in low or "progress=" in low:
        return "Checking the result…"
    if "reply:" in low:
        return "Checking the result…"
    if "user inject:" in low:
        return "Applying your steering…"
    if any(
        marker in low
        for marker in ("run_id=", "workspace=", "task=", "mcp:", "auto-loaded")
    ):
        return ""
    return compact
