"""Chat presentation — conversation first, receipts second."""

from __future__ import annotations

import re
from pathlib import Path


def clean_reply_text(text: str, *, max_chars: int = 1200) -> str:
    """Normalize model output for chat display.

    Keeps markdown for Rich rendering. Strips interactive DOM dumps and
    collapses extreme whitespace.
    """
    t = (text or "").strip()
    if not t:
        return ""
    # Cut interactive a11y dumps / DOM noise that sometimes leaks into replies
    if "Interactive snapshot" in t or "[e0]" in t:
        t = t.split("Interactive snapshot")[0].strip()
    t = re.sub(r"\n{3,}", "\n\n", t)
    if len(t) > max_chars:
        # Prefer ending on a sentence boundary
        cut = t[:max_chars].rsplit(".", 1)[0]
        t = (cut + ".").strip() if cut else t[:max_chars].rstrip() + "…"
    return t


def format_chat_reply(
    *,
    text: str,
    files: list[str] | None = None,
    workspace_root: Path | str | None = None,
    max_files: int = 3,
) -> str:
    """User-facing turn: short answer + optional absolute file receipts."""
    body = clean_reply_text(text)
    root = Path(workspace_root) if workspace_root else None
    files = list(files or [])[:max_files]
    if not files:
        return body or "Done."

    # If the prose already lists the paths, don't duplicate a receipt block.
    abs_paths = []
    for rel in files:
        p = (root / rel).resolve() if root else Path(rel).resolve()
        abs_paths.append(str(p))

    if body and all(p in body or Path(p).name in body for p in abs_paths):
        return body

    lines = [body] if body else []
    lines.append("")
    lines.append("Saved:")
    for p in abs_paths:
        lines.append(f"  {p}")
    return "\n".join(lines).strip()


def print_chat_reply(text: str) -> None:
    """Print assistant reply via Rich UI (plain fallback when non-TTY)."""
    from kageha.chat.ui import print_assistant

    print_assistant(text)
