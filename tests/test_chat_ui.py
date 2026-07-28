"""Rich chat console helpers."""

from __future__ import annotations

from io import StringIO
import re

import yaml
from rich.console import Console

from kageha.chat import ui
from kageha.chat.present import clean_reply_text, print_chat_reply


def _plain(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def _console(force_terminal: bool = True) -> tuple[Console, StringIO]:
    buf = StringIO()
    c = Console(
        file=buf,
        force_terminal=force_terminal,
        width=80,
        color_system="truecolor" if force_terminal else None,
        no_color=not force_terminal,
        theme=ui._THEME,
    )
    return c, buf


def test_print_banner_includes_model_and_approvals():
    c, buf = _console()
    ui.print_banner(
        approvals="Approvals: ask before risky tools",
        model_line="Models (from setup ladders): planner=gemini-pro",
        console=c,
    )
    out = _plain(buf.getvalue())
    assert "Kageha" in out
    # Chips render label/value separately
    assert "approvals" in out.lower()
    assert "ask" in out.lower()
    assert "planner" in out.lower()
    assert "gemini-pro" in out


def test_fit_panel_width_tracks_console():
    c, _ = _console()
    assert ui.fit_panel_width(c) == 79  # width 80 → leave 1 col for border wrap
    narrow = Console(file=StringIO(), force_terminal=True, width=50, theme=ui._THEME)
    assert ui.fit_panel_width(narrow) == 49


def test_print_assistant_markdown_panel_on_tty():
    c, buf = _console(force_terminal=True)
    ui.print_assistant("Hello **world**", console=c)
    out = buf.getvalue()
    assert "kageha" in out.lower()
    assert "Hello" in out
    assert "world" in out


def test_print_assistant_plain_when_not_tty():
    c, buf = _console(force_terminal=False)
    ui.print_assistant("Plain reply", console=c)
    out = buf.getvalue()
    assert "kageha> Plain reply" in out


def test_print_sessions_table():
    c, buf = _console()
    ui.print_sessions(
        [
            {
                "run_id": "abc123",
                "mtime": "1.0",
                "status": "ok",
                "task": "demo task",
            }
        ],
        console=c,
    )
    out = buf.getvalue()
    assert "abc123" in out
    assert "demo task" in out


def test_print_sessions_empty():
    c, buf = _console()
    ui.print_sessions([], console=c)
    assert "No sessions" in buf.getvalue()


def test_ladder_model_summary(tmp_path, monkeypatch):
    home = tmp_path / "khome"
    home.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    (home / "models.yaml").write_text(
        yaml.safe_dump(
            {
                "setup_pins": {
                    "planner": "gemini-pro",
                    "executor": "gpt-fast",
                    "session_default": "azure-mini",
                }
            }
        )
    )
    summary = ui.ladder_model_summary()
    assert "planner=gemini-pro" in summary
    assert "executor=gpt-fast" in summary
    assert "default=azure-mini" in summary


def test_clean_keeps_markdown_strips_dom():
    raw = (
        "Yes, I opened **Google**.\n\n"
        "## Interactive snapshot\n"
        "- [e0] a name='Skip to main content'\n"
    )
    out = clean_reply_text(raw)
    assert "Interactive snapshot" not in out
    assert "[e0]" not in out
    assert "**Google**" in out


def test_print_chat_reply_uses_ui(monkeypatch):
    c, buf = _console(force_terminal=False)
    ui.set_console(c)
    try:
        print_chat_reply("hi there")
        assert "kageha> hi there" in buf.getvalue()
    finally:
        ui.set_console(None)
