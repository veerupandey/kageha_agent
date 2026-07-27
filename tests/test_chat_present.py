from pathlib import Path

from kageha.chat.present import clean_reply_text, format_chat_reply


def test_clean_strips_markdown_and_dom_dump():
    raw = (
        "Yes, I opened Google.\n\n"
        "## Interactive snapshot\n"
        "- [e0] a name='Skip to main content'\n"
        "- [e1] a name='LinkedIn'\n"
    )
    out = clean_reply_text(raw)
    assert "Interactive snapshot" not in out
    assert "[e0]" not in out
    assert "opened Google" in out


def test_format_chat_reply_receipt(tmp_path: Path):
    shot = tmp_path / "artifacts" / "browser_open.png"
    shot.parent.mkdir()
    shot.write_bytes(b"x")
    text = format_chat_reply(
        text="Opened the browser and took a screenshot.",
        files=["artifacts/browser_open.png"],
        workspace_root=tmp_path,
    )
    assert "Opened the browser" in text
    assert "Saved:" in text
    assert str(shot.resolve()) in text


def test_format_skips_duplicate_receipt_when_path_already_in_body(tmp_path: Path):
    shot = tmp_path / "artifacts" / "x.png"
    shot.parent.mkdir()
    shot.write_bytes(b"x")
    body = f"Done. Screenshot at {shot.resolve()}"
    text = format_chat_reply(
        text=body,
        files=["artifacts/x.png"],
        workspace_root=tmp_path,
    )
    assert text.count("Saved:") == 0
