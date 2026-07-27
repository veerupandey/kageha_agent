"""Deliverables bind to the agent session, not the project root."""

from __future__ import annotations

from pathlib import Path

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace


def _ctx(tmp_path: Path, *, project: Path | None = None) -> HarnessContext:
    ws = SessionWorkspace.create("bind-sess")
    # Re-home workspace under tmp for isolation.
    root = tmp_path / "session"
    root.mkdir()
    (root / "artifacts").mkdir()
    ws.root = root
    return HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=None,  # type: ignore[arg-type]
        project_root=str(project) if project else "",
    )


def test_bare_pptx_binds_to_session_artifacts(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    ctx = _ctx(tmp_path, project=project)
    target = ctx.resolve_write_path("bare_fair_investor_pitch.pptx")
    assert target == ctx.session_root() / "artifacts" / "bare_fair_investor_pitch.pptx"
    assert str(target).startswith(str(ctx.session_root()))
    assert not str(target).startswith(str(project.resolve()))


def test_artifacts_prefix_binds_to_session(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    ctx = _ctx(tmp_path, project=project)
    target = ctx.resolve_write_path("artifacts/deck.pdf")
    assert target == ctx.session_root() / "artifacts" / "deck.pdf"


def test_source_file_stays_in_project(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    ctx = _ctx(tmp_path, project=project)
    target = ctx.resolve_write_path("build_native_deck.py")
    assert target == project.resolve() / "build_native_deck.py"


def test_is_session_deliverable_path_helpers():
    assert HarnessContext.is_session_deliverable_path("deck.pptx")
    assert HarnessContext.is_session_deliverable_path("artifacts/x.html")
    assert not HarnessContext.is_session_deliverable_path("src/main.py")
    assert not HarnessContext.is_session_deliverable_path("build_native_deck.py")
