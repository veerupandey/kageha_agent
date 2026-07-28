"""CLI markdown renderer (Rich + file:// / path hyperlinks)."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from kageha.chat import ui
from kageha.chat.markdown_render import prepare_markdown, render_chat_markdown


def _console(width: int = 100) -> tuple[Console, StringIO]:
    buf = StringIO()
    c = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        width=width,
        theme=ui._THEME,
        no_color=False,
    )
    return c, buf


def test_prepare_markdown_wraps_bare_url_and_path(tmp_path: Path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"x")
    out = prepare_markdown(f"See https://example.com and {shot.resolve()}")
    assert "[https://example.com](https://example.com)" in out
    assert "file://" in out
    assert "shot.png" in out


def test_prepare_markdown_skips_code_fences():
    src = "Before\n\n```\n/Users/no/link.png\n```\n\nAfter https://ex.com"
    out = prepare_markdown(src)
    assert "```\n/Users/no/link.png\n```" in out
    assert "[https://ex.com](https://ex.com)" in out


def test_render_markdown_bold_list_and_heading():
    c, buf = _console()
    md = render_chat_markdown("# Title\n\nHello **world**\n\n- one\n- two")
    c.print(md)
    out = buf.getvalue()
    assert "Title" in out
    assert "\x1b[1m" in out  # bold
    assert "world" in out
    assert "one" in out


def test_render_markdown_file_link_osc8(tmp_path: Path):
    shot = tmp_path / "clip.mp4"
    shot.write_bytes(b"x")
    c, buf = _console()
    c.print(render_chat_markdown(f"Saved:\n\n{shot.resolve()}"))
    out = buf.getvalue()
    assert "\x1b]8;" in out
    assert "file://" in out


def test_print_assistant_renders_markdown(tmp_path: Path):
    c, buf = _console()
    ui.print_assistant(
        "## Done\n\nClick [demo](https://example.com/a) and **go**.",
        console=c,
    )
    out = buf.getvalue()
    assert "Done" in out
    assert "\x1b]8;" in out
    assert "https://example.com/a" in out
    assert "\x1b[1m" in out
