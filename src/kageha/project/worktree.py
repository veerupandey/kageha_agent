"""Git worktree isolation for parallel agents."""

from __future__ import annotations

import secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from kageha.project.brain import resolve_project_root


@dataclass
class WorktreeHandle:
    root: Path
    branch: str
    path: Path
    created: bool = True

    def remove(self, *, force: bool = True) -> None:
        if not self.created:
            return
        args = ["git", "worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(self.path))
        subprocess.run(
            args,
            cwd=str(self.root),
            capture_output=True,
            text=True,
            check=False,
        )
        # Best-effort branch cleanup for ephemeral attempt branches.
        if self.branch.startswith("kageha/"):
            subprocess.run(
                ["git", "branch", "-D", self.branch],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                check=False,
            )


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )


def is_git_repo(project_root: str | Path | None) -> bool:
    root = resolve_project_root(project_root)
    if root is None:
        return False
    return _git(root, "rev-parse", "--is-inside-work-tree").returncode == 0


def worktrees_dir(project_root: Path) -> Path:
    path = project_root / ".kageha" / "worktrees"
    path.mkdir(parents=True, exist_ok=True)
    ignore = project_root / ".kageha" / ".gitignore"
    if not ignore.is_file():
        try:
            ignore.write_text("worktrees/\n", encoding="utf-8")
        except OSError:
            pass
    return path


def create_worktree(
    project_root: str | Path | None,
    *,
    label: str = "",
    base_ref: str = "HEAD",
) -> WorktreeHandle:
    root = resolve_project_root(project_root)
    if root is None:
        raise ValueError("project_root required for worktree isolation")
    if not is_git_repo(root):
        raise ValueError(f"not a git repository: {root}")
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (label or "wt"))
    slug = (slug.strip("-") or "wt")[:40]
    token = secrets.token_hex(3)
    branch = f"kageha/{slug}-{token}"
    path = worktrees_dir(root) / f"{slug}-{token}"
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    # Create a new branch from base_ref checked out into the worktree.
    proc = _git(
        root,
        "worktree",
        "add",
        "-b",
        branch,
        str(path),
        base_ref,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git worktree add failed: {(proc.stderr or proc.stdout or '').strip()}"
        )
    return WorktreeHandle(root=root, branch=branch, path=path, created=True)


def list_worktrees(project_root: str | Path | None) -> list[dict[str, str]]:
    root = resolve_project_root(project_root)
    if root is None or not is_git_repo(root):
        return []
    proc = _git(root, "worktree", "list", "--porcelain")
    if proc.returncode != 0:
        return []
    rows: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        if not line.strip():
            if current:
                rows.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            current["branch"] = line.split(" ", 1)[1].removeprefix("refs/heads/")
        elif line.startswith("HEAD "):
            current["head"] = line.split(" ", 1)[1]
    if current:
        rows.append(current)
    return rows
