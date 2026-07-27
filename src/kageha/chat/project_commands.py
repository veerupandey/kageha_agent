"""Slash helpers for project brain commands and best-of-n."""

from __future__ import annotations

from pathlib import Path


def handle_project_command(
    line: str,
    *,
    project_root: str | Path | None = None,
) -> tuple[bool, str]:
    """Handle ``/project:<name>``, ``/cmd <name>``, ``/best-of-n …`` prep.

    Returns (handled, message_or_replacement). For best-of-n, returns a
    replacement objective the turn manager can run via CLI helper note —
    the interactive path prints instructions unless the user wants a full run.
    """
    text = (line or "").strip()
    low = text.lower()
    root = Path(project_root or Path.cwd()).expanduser()

    if low.startswith("/project:") or low.startswith("/cmd "):
        from kageha.project.brain import load_project_command

        if low.startswith("/project:"):
            name = text.split(":", 1)[1].strip().split()[0]
            rest = text.split(":", 1)[1].strip()[len(name) :].strip()
        else:
            parts = text.split(maxsplit=2)
            name = parts[1] if len(parts) > 1 else ""
            rest = parts[2] if len(parts) > 2 else ""
        body = load_project_command(root, name)
        if not body:
            return True, f"Unknown project command: {name or '(empty)'}"
        if rest:
            return True, f"{body}\n\n## User follow-up\n{rest}"
        return True, body

    if low == "/best-of-n" or low.startswith("/best-of-n "):
        objective = text.split(maxsplit=1)[1].strip() if " " in text else ""
        if not objective:
            return (
                True,
                "Usage: /best-of-n <objective>  (or: kageha best-of-n \"…\" --n 3)",
            )
        return (
            True,
            "__BEST_OF_N__:" + objective,
        )

    return False, ""
