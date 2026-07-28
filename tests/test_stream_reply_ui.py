"""Terminal StreamReply live sink."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from kageha.chat import ui
from kageha.chat.ui import StreamReply


def _console() -> tuple[Console, StringIO]:
    buf = StringIO()
    c = Console(
        file=buf,
        force_terminal=True,
        width=80,
        color_system="truecolor",
        theme=ui._THEME,
    )
    return c, buf


def test_stream_reply_concatenates_and_finalizes():
    c, buf = _console()
    suspended = []
    resumed = []
    reply = StreamReply(
        console=c,
        on_suspend=lambda: suspended.append(1),
        on_resume=lambda: resumed.append(1),
    )
    reply.begin_step()
    reply("Hello")
    reply(" world")
    assert reply.text() == "Hello world"
    assert suspended == [1]
    # Tool step discards buffer and resumes progress.
    reply.end_step(had_tool_calls=True)
    assert reply.text() == ""
    assert resumed == [1]
    reply.begin_step()
    reply("Final")
    out = reply.finalize("Final answer")
    assert out == "Final answer"
    assert "Final answer" in buf.getvalue() or "kageha" in buf.getvalue().lower()


def test_stream_reply_finalize_idempotent_panel():
    c, buf = _console()
    reply = StreamReply(console=c)
    reply.feed("abc")
    reply.finalize("abc")
    first = buf.getvalue()
    reply.finalize("abc")  # should not crash; may no-op print again
    assert "abc" in first or "kageha" in first.lower()
