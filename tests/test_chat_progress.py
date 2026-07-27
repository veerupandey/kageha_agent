"""Interactive chat progress is transient without changing normal controller logs."""

from io import StringIO

from rich.console import Console

from kageha.chat.progress import (
    TransientProgress,
    _friendly_status,
    _render_checklist,
    _render_reasoning,
)
from kageha.loop.controller import LoopController


def test_transient_progress_erases_status_when_closed():
    output = StringIO()
    console = Console(file=output, force_terminal=True, width=100)

    with TransientProgress(console=console) as progress:
        progress.update("[kageha] step 2/40 — thinking…")

    assert "step 2/40" not in output.getvalue()


def test_controller_routes_live_logs_to_handler(capsys):
    messages: list[str] = []
    controller = LoopController(live=True, log_handler=messages.append)

    controller._log("[kageha] planning…")

    assert messages == ["[kageha] planning…"]
    assert capsys.readouterr().out == ""


def test_hitl_telemetry_becomes_one_human_status():
    assert (
        _friendly_status("[kageha] tools: ask_human (parallel≤8)")
        == "Waiting for your answer…"
    )
    assert _friendly_status("[kageha] step 2/40 — thinking…") == "Thinking…"
    assert _friendly_status("[kageha] workspace=/tmp/session") == ""
    assert _friendly_status("[kageha]   tools: skill_run") == "Running skill_run…"


def test_non_terminal_progress_deduplicates_status():
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=100)
    progress = TransientProgress(console=console)
    progress.update("[kageha] tools: ask_human")
    progress.update("[kageha] tools: ask_human")
    assert output.getvalue().count("Waiting for your answer") == 1


def test_detailed_progress_keeps_action_trace():
    output = StringIO()
    console = Console(file=output, force_terminal=True, width=140)
    with TransientProgress(console=console, detailed=True) as progress:
        progress.update(
            '[kageha] action: browser_open {"url":"https://example.com"}'
        )
    text = output.getvalue()
    assert "browser_open" in text
    assert "example.com" in text


def test_detailed_progress_shows_reasoning_and_todo_checks():
    output = StringIO()
    console = Console(file=output, force_terminal=True, width=140)
    with TransientProgress(console=console, detailed=True) as progress:
        progress.update(
            "[kageha]   reasoning: I'll scan the LAN then write network_tvs.md"
        )
        progress.update(
            "[kageha] todos: 1/2\n"
            "- [x] p1: Scan for TVs\n"
            "- [ ] p2: Write report"
        )
    text = output.getvalue()
    assert "Reasoning:" in text
    assert "scan the LAN" in text
    assert "Todos 1/2" in text
    assert "✓" in text
    assert "○" in text


def test_render_helpers():
    board = _render_checklist(
        "[kageha] todos: 1/2\n- [x] p1: Scan\n- [ ] p2: Write"
    )
    assert "Todos 1/2" in board
    assert "✓ Scan" in board
    assert "○ Write" in board
    assert "Reasoning: look around" in _render_reasoning(
        "[kageha]   reasoning: look around"
    )


def test_controller_logs_todo_board(tmp_path):
    from kageha.harness.sandbox import SessionWorkspace

    messages: list[str] = []
    controller = LoopController(live=True, log_handler=messages.append)
    ws = SessionWorkspace(run_id="prog1", root=tmp_path / "prog1")
    ws.root.mkdir(parents=True)
    ws.write_text(
        "todo.md",
        "# Plan\n\n- [x] p1: Done already\n- [ ] p2: Still open\n",
    )
    controller._log_todo_board(ws)
    joined = "\n".join(messages)
    assert "todos: 1/2" in joined
    assert "[x] p1: Done already" in joined


def test_live_progress_releases_cursor_for_human_input():
    output = StringIO()
    console = Console(file=output, force_terminal=True, width=120)
    with TransientProgress(console=console) as progress:
        assert progress._live is not None
        progress.update("[kageha] tools: ask_human (parallel≤8)")
        assert progress._waiting_for_input is True
        assert progress._live is None

        # Tool-planning telemetry must not redraw over `Your answer>`.
        progress.update('[kageha] action: ask_human {"question":"Which style?"}')
        assert progress._live is None

        progress.update('[kageha] ← ask_human: {"answer":"professional"}')
        assert progress._waiting_for_input is False
        assert progress._live is not None
