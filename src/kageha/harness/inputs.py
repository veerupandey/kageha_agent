"""Seed task-referenced external files into the session workspace."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from kageha.config import kageha_home, sessions_dir
from kageha.harness.sandbox import SessionWorkspace

# Paths users commonly paste into tasks.
_PATH_RE = re.compile(
    r"(?:~|/Users|/home|/tmp|/var|/opt)"
    r"[^\s'\"`\)\]\},]+"
)


def extract_paths(task: str) -> list[Path]:
    found: list[Path] = []
    for m in _PATH_RE.findall(task or ""):
        # trim trailing punctuation left by markdown/prose
        raw = m.rstrip(".,;:!?")
        try:
            p = Path(raw).expanduser().resolve()
        except Exception:  # noqa: BLE001
            continue
        if p.is_file():
            found.append(p)
    # de-dupe preserving order
    out: list[Path] = []
    seen: set[str] = set()
    for p in found:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _allowed_read(path: Path) -> bool:
    """Allow prior session artifacts, kageha home, and common text research files."""
    try:
        resolved = path.resolve()
    except Exception:  # noqa: BLE001
        return False
    roots = [
        sessions_dir().resolve(),
        kageha_home().resolve(),
    ]
    if any(str(resolved).startswith(str(r) + "/") or resolved == r for r in roots):
        return True
    # Explicit task path under home for research formats
    home = Path.home().resolve()
    if str(resolved).startswith(str(home) + "/") and resolved.suffix.lower() in {
        ".md",
        ".txt",
        ".pdf",
        ".json",
        ".csv",
        ".html",
        ".htm",
        ".yaml",
        ".yml",
    }:
        return True
    return False


def seed_task_inputs(task: str, workspace: SessionWorkspace) -> list[dict[str, str]]:
    """Copy referenced absolute files into ``inputs/`` for sandboxed tools.

    Returns list of {source, dest} maps. Safe no-op when nothing matches.
    """
    seeded: list[dict[str, str]] = []
    used_names: set[str] = set()
    for src in extract_paths(task):
        if not _allowed_read(src):
            continue
        name = src.name
        if name in used_names:
            name = f"{src.parent.name}_{src.name}"
        used_names.add(name)
        dest_rel = f"inputs/{name}"
        dest = workspace.root / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copy2(src, dest)
        seeded.append({"source": str(src), "dest": dest_rel})
    if seeded:
        lines = ["# Seeded inputs from task paths\n"]
        for item in seeded:
            lines.append(f"- `{item['source']}` → `{item['dest']}`")
        workspace.write_text("inputs/README.md", "\n".join(lines) + "\n")
    return seeded


def resolve_readable_path(workspace: SessionWorkspace, path: str) -> Path:
    """Resolve workspace-relative paths, or allowlisted absolute reads."""
    raw = (path or "").strip()
    if not raw:
        raise FileNotFoundError("empty path")
    # Absolute / home — allowlisted read
    if raw.startswith("~") or raw.startswith("/"):
        p = Path(raw).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"file not found: {path}")
        if not _allowed_read(p):
            raise ValueError(
                "Path escapes session workspace and is outside the allowlist "
                "(prior ~/.kageha sessions, kageha home, or home research files)."
            )
        return p
    return workspace.path(raw)
