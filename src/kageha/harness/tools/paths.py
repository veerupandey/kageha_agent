"""Shared workspace-relative path helpers for harness tools."""

from __future__ import annotations

from pathlib import Path


def rel_to_workspace(path: Path, root: Path) -> str:
    """Return path relative to workspace root, resolving both sides first.

    On macOS, session roots under /var often resolve to /private/var while
    Path objects from workspace.path() are already resolved — relative_to
    then raises ValueError. Resolve both; fall back to a safe relative string.
    """
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        resolved_root = root.resolve()
        resolved_path = path.resolve()
        root_s = str(resolved_root).rstrip("/")
        path_s = str(resolved_path)
        prefix = root_s + "/"
        if path_s.startswith(prefix):
            return path_s[len(prefix) :]
        # Last resort: keep a short relative-looking name (e.g. carousel/prompts.json)
        parts = resolved_path.parts
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"
        return resolved_path.name
