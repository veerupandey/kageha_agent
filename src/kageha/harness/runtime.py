"""Harness runtime context shared by tools and loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kageha.harness.approvals import ApprovalGate
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.base import ToolRegistry
from kageha.models.router import ModelRouter

# User-facing outputs belong to the agent session (WebUI Artifacts /files),
# not the git project root — even when project_root is bound for code edits.
_SESSION_DELIVERABLE_EXTS = frozenset(
    {
        ".ppt",
        ".pptx",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".zip",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".mp4",
        ".webm",
        ".html",
        ".htm",
    }
)
_SESSION_OUTPUT_PREFIXES = (
    "artifacts/",
    "outputs/",
    "diagrams/",
    "research/",
    "slides/",
    "carousel/",
    "deck/",
)


@dataclass
class HarnessContext:
    workspace: SessionWorkspace
    approvals: ApprovalGate
    router: ModelRouter
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    attached_kbs: list[str] = field(default_factory=list)
    cancel_event: Any = None
    meta: dict[str, Any] = field(default_factory=dict)
    spent_usd: float = 0.0
    step: int = 0
    # When set (repo / git worktree), file+bash tools prefer this root so
    # parallel agents do not clobber the parent checkout.
    project_root: str = ""

    def coding_root(self) -> Path:
        """Directory tools should treat as the working tree for code edits."""
        raw = (self.project_root or self.meta.get("project_root") or "").strip()
        if raw:
            path = Path(raw).expanduser()
            try:
                path = path.resolve()
            except OSError:
                path = Path(raw).expanduser()
            if path.is_dir():
                return path
        return self.workspace.root.resolve()

    def session_root(self) -> Path:
        """Agent-bound session workspace (Artifacts / downloads live here)."""
        return self.workspace.root.resolve()

    @staticmethod
    def is_session_deliverable_path(rel: str) -> bool:
        """True when a relative path should bind to the agent session."""
        raw = (rel or "").strip().replace("\\", "/").lstrip("./")
        if not raw or raw.startswith("~") or raw.startswith("/"):
            return False
        low = raw.lower()
        if any(low.startswith(p) for p in _SESSION_OUTPUT_PREFIXES):
            return True
        # Bare deliverable filename → session artifacts/
        if "/" not in raw and Path(raw).suffix.lower() in _SESSION_DELIVERABLE_EXTS:
            return True
        return False

    def resolve_write_path(self, rel: str) -> Path:
        """Resolve a write path: session deliverables vs project code root.

        - ``artifacts/…``, ``outputs/…``, bare ``deck.pptx`` → session workspace
          (bound to this agent; WebUI can serve/download)
        - source / other relative paths → ``coding_root`` (project when bound)
        """
        raw = (rel or "").strip()
        if not raw:
            raise ValueError("empty path")
        # Absolute / home paths stay under coding_root (legacy escape hatch).
        if raw.startswith("~") or raw.startswith("/"):
            root = self.coding_root()
            target = Path(raw).expanduser().resolve()
            if not str(target).startswith(str(root)):
                raise ValueError(f"Path escapes project root: {rel}")
            return target

        norm = raw.replace("\\", "/").lstrip("./")
        if self.is_session_deliverable_path(norm):
            root = self.session_root()
            # Bare ``deck.pptx`` → artifacts/deck.pptx inside the session.
            if "/" not in norm:
                norm = f"artifacts/{norm}"
            target = (root / norm).resolve()
            if not str(target).startswith(str(root)):
                raise ValueError(f"Path escapes session workspace: {rel}")
            return target

        root = self.coding_root()
        target = (root / norm).resolve()
        if not str(target).startswith(str(root)):
            raise ValueError(f"Path escapes project root: {rel}")
        return target

    def note_touched(self, path: str | Path) -> None:
        touched = self.meta.setdefault("touched_paths", [])
        text = str(path)
        if text and text not in touched:
            touched.append(text)
