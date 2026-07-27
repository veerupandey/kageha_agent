"""Foolproof computer-use path: skill routing, filter, readiness."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tool_policy import _merge_policy
from kageha.harness.tools.builtin import load_entry_point_tools
from kageha.harness.tools.computer_ready import task_wants_computer
from kageha.harness.tools.skills_tools import activate_skills
from kageha.loop.controller import _filter_tools_for_skills
from kageha.memory.skills import SkillRegistry


def _ctx(tmp_path: Path) -> HarnessContext:
    root = tmp_path / "session"
    root.mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(exist_ok=True)
    return HarnessContext(
        workspace=SessionWorkspace(run_id="cu", root=root),
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )


def test_task_wants_computer_desktop_not_web():
    assert task_wants_computer(
        "open Slack and click Messages"
    )
    assert task_wants_computer(
        "Use Calculator: 8+9 via computer_get_state / computer_click"
    )
    assert not task_wants_computer("open https://example.com and click Login")
    assert not task_wants_computer("search the website for docs")


def test_slack_autoload_prefers_computer_use():
    reg = SkillRegistry()
    loaded = reg.auto_load_for_task("open Slack and click Messages", limit=4)
    assert loaded.names == ["computer_use"]
    assert "web_browse" not in loaded.names


def test_packs_union_across_overlays():
    merged = _merge_policy({"packs": ["computer"]}, {"packs": ["browser"]})
    assert set(merged["packs"]) == {"computer", "browser"}


def test_skill_filter_keeps_computer_even_if_only_web_browse_active(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KAGEHA_TOOL_PACKS", "computer")
    ctx = _ctx(tmp_path)
    ctx.tools = load_entry_point_tools(ctx)
    # Pack ownership wins: web_browse allowlist must not delete computer_*.
    activate_skills(ctx, ["web_browse"])
    names = {s.name for s in _filter_tools_for_skills(ctx)}
    assert "computer_get_state" in names
    assert "computer_click" in names
    assert "computer_doctor" in names
