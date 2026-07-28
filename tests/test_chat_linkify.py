"""Terminal OSC-8 linkify for URLs and local files."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from kageha.chat.linkify import href_for_target, linkify_text
from kageha.chat import ui


def test_href_for_web_and_file(tmp_path: Path):
    assert href_for_target("https://example.com/a.png") == "https://example.com/a.png"
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"x")
    href = href_for_target(str(shot))
    assert href and href.startswith("file://")
    assert href.endswith("shot.png")


def test_linkify_web_url_emits_osc8():
    buf = StringIO()
    c = Console(file=buf, force_terminal=True, color_system="truecolor", width=80)
    c.print(linkify_text("See https://example.com/demo.mp4 please"))
    out = buf.getvalue()
    assert "\x1b]8;" in out
    assert "https://example.com/demo.mp4" in out


def test_linkify_markdown_image_and_local_path(tmp_path: Path):
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"x")
    buf = StringIO()
    c = Console(file=buf, force_terminal=True, color_system="truecolor", width=100)
    text = f"![poster](https://cdn.example/p.png) and {vid}"
    c.print(linkify_text(text))
    out = buf.getvalue()
    assert "https://cdn.example/p.png" in out
    assert "file://" in out
    assert "clip.mp4" in out


def test_print_assistant_makes_saved_path_clickable(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    shot = tmp_path / "artifacts" / "browser_open.png"
    shot.parent.mkdir()
    shot.write_bytes(b"x")
    buf = StringIO()
    c = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        width=100,
        theme=ui._THEME,
        no_color=False,
    )
    ui.print_assistant(f"Done.\n\nSaved:\n  {shot.resolve()}", console=c)
    out = buf.getvalue()
    assert "\x1b]8;" in out
    assert "file://" in out
