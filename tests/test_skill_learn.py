"""Closest Hermes skill learning + Claude-style activation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.builtin import load_entry_point_tools
from kageha.harness.tools.skills_tools import activate_skills, register_skills_tools
from kageha.loop.controller import _filter_tools_for_skills
from kageha.memory.learning_loop import maybe_prompt_skill_distill, proposal_from_run
from kageha.memory.skill_learn import (
    collect_tool_pitfalls,
    learning_nudge,
    skill_learn_mode,
    skill_learn_nudges_enabled,
    skill_learn_soft_enabled,
    skill_learn_unattended_enabled,
    stamp_unattended_provenance,
)
from kageha.memory.skills import SkillRegistry
from kageha.models.base import ChatMessage


def test_skill_learn_mode_env(monkeypatch):
    monkeypatch.delenv("KAGEHA_SKILL_LEARN", raising=False)
    assert skill_learn_mode() == "hitl"
    assert skill_learn_soft_enabled(interactive=True) is False
    assert skill_learn_unattended_enabled(interactive=True) is False
    monkeypatch.setenv("KAGEHA_SKILL_LEARN", "soft")
    assert skill_learn_mode() == "soft"
    assert skill_learn_soft_enabled(interactive=True) is True
    assert skill_learn_unattended_enabled(interactive=True) is False
    assert skill_learn_nudges_enabled() is True
    monkeypatch.setenv("KAGEHA_SKILL_LEARN", "unattended")
    assert skill_learn_mode() == "unattended"
    assert skill_learn_soft_enabled(interactive=True) is True
    assert skill_learn_unattended_enabled(interactive=True) is True
    monkeypatch.setenv("KAGEHA_SKILL_LEARN", "auto")
    assert skill_learn_mode() == "unattended"
    monkeypatch.setenv("KAGEHA_SKILL_LEARN", "off")
    assert skill_learn_mode() == "off"
    assert skill_learn_nudges_enabled() is False
    assert skill_learn_soft_enabled(interactive=True) is False
    assert "learned: unattended" in stamp_unattended_provenance("# Skill\n")


def test_learning_nudge_and_pitfalls():
    pitfalls = collect_tool_pitfalls(
        [
            SimpleNamespace(name="bash", content="ERROR: boom\nmore"),
            SimpleNamespace(name="ok", content="fine"),
        ]
    )
    assert pitfalls and pitfalls[0].startswith("bash:")
    nudge = learning_nudge(["make_reel"], pitfalls=pitfalls)
    assert "skill_manage" in nudge
    assert "make_reel" in nudge
    assert "observe" in nudge


def test_activate_skills_sets_allowed_tools(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    root = tmp_path / "skills" / "gated"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: gated\ndescription: gated skill\n"
        "allowed-tools: web_search bash\n---\n\n# Gated\n",
        encoding="utf-8",
    )
    from kageha import config

    monkeypatch.setattr(config, "skills_dirs", lambda: [tmp_path / "skills"])
    reg = SkillRegistry()
    reg.reload()
    ws = SessionWorkspace(run_id="t", root=tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )
    activate_skills(ctx, ["gated"], registry=reg)
    assert ctx.meta["active_skills"] == ["gated"]
    assert "web_search" in (ctx.meta["skill_allowed_tools"] or [])


def test_autoload_returns_names(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    root = tmp_path / "skills" / "make_reel"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: make_reel\ndescription: Create short video reels\n---\n\n# Steps\n",
        encoding="utf-8",
    )
    from kageha import config

    monkeypatch.setattr(config, "skills_dirs", lambda: [tmp_path / "skills"])
    loaded = SkillRegistry().auto_load_for_task("make a reel", limit=1)
    assert loaded.names == ["make_reel"]
    assert "skill:make_reel" in loaded.text


def test_write_file_needs_approval(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    reg = SkillRegistry()
    reg.create_stub("wf-skill", "write file skill")
    denied = reg.manage(
        "write_file",
        "wf-skill",
        "scripts/x.py\n---\nprint(1)\n",
        approved=False,
    )
    assert denied.startswith("NEEDS_APPROVAL")
    ok = reg.manage(
        "write_file",
        "wf-skill",
        "scripts/x.py\n---\nprint(1)\n",
        approved=True,
    )
    assert ok.startswith("Wrote")
    assert (tmp_path / "skills" / "wf-skill" / "scripts" / "x.py").is_file()


def test_distill_prefers_refine_when_active():
    prop = proposal_from_run(
        task="make a reel",
        message="done",
        status="success",
        steps=6,
        active_skills=["make_reel"],
    )
    assert prop is not None
    assert prop.action == "refine"
    assert prop.name == "make_reel"


def test_skill_manage_in_skills_pack_not_forge_only(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    root = tmp_path / "session"
    root.mkdir()
    ctx = HarnessContext(
        workspace=SessionWorkspace(run_id="t", root=root),
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )
    reg = load_entry_point_tools(ctx)
    assert "skill_manage" in reg.names()
    assert "skill_load" in reg.names()
    assert "forge_tool" in reg.names()


@pytest.mark.asyncio
async def test_soft_observe_skips_approval(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("KAGEHA_SKILL_LEARN", "soft")
    home_skills = tmp_path / "skills"
    home_skills.mkdir()
    # SkillRegistry create_stub writes under kageha_home()/skills
    reg = SkillRegistry()
    reg.create_stub("soft-skill", "soft learn demo")
    root = tmp_path / "ws"
    root.mkdir()
    gate = ApprovalGate(auto_approve=False)
    gate.require = AsyncMock(return_value=False)  # type: ignore[method-assign]
    ctx = HarnessContext(
        workspace=SessionWorkspace(run_id="t", root=root),
        approvals=gate,
        router=SimpleNamespace(),
    )
    ctx.meta["skill_learn_interactive"] = True
    tools = register_skills_tools(ctx)
    manage = tools.get("skill_manage")
    assert manage is not None
    out = await manage.call(
        action="observe",
        name="soft-skill",
        content="rate limited once",
    )
    assert out.startswith("Recorded observation"), out
    gate.require.assert_not_called()
    text = (tmp_path / "skills" / "soft-skill" / "SKILL.md").read_text()
    assert "rate limited" in text


@pytest.mark.asyncio
async def test_unattended_create_skips_approval(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("KAGEHA_SKILL_LEARN", "unattended")
    root = tmp_path / "ws"
    root.mkdir()
    gate = ApprovalGate(auto_approve=False)
    gate.require = AsyncMock(return_value=False)  # type: ignore[method-assign]
    ctx = HarnessContext(
        workspace=SessionWorkspace(run_id="t", root=root),
        approvals=gate,
        router=SimpleNamespace(),
    )
    ctx.meta["skill_learn_interactive"] = True
    tools = register_skills_tools(ctx)
    manage = tools.get("skill_manage")
    assert manage is not None
    body = "---\nname: learned-x\ndescription: demo\n---\n\n# learned-x\n\nDo the thing.\n"
    out = await manage.call(action="create", name="learned-x", content=body)
    assert "Created skill" in out, out
    gate.require.assert_not_called()
    text = (tmp_path / "skills" / "learned-x" / "SKILL.md").read_text()
    assert "learned: unattended" in text
    # delete still HITL
    out_del = await manage.call(action="delete", name="learned-x", content="")
    assert out_del.startswith("DENIED"), out_del
    gate.require.assert_called()


def test_chat_defer_human_input_still_interactive_for_soft(monkeypatch):
    """Chat sets defer_human_input=True; soft learn must still see TTY as interactive."""
    monkeypatch.setenv("KAGEHA_SKILL_LEARN", "soft")
    monkeypatch.setattr("kageha.memory.skill_learn.sys.stdin.isatty", lambda: True)
    assert skill_learn_soft_enabled(interactive=True) is True
    # Channel asker blocks soft even when controller passed interactive=True
    from kageha.harness.approvals import set_channel_asker, reset_channel_asker

    tok = set_channel_asker(lambda _p: "no")
    try:
        assert skill_learn_soft_enabled(interactive=True) is False
        assert skill_learn_soft_enabled(interactive=None) is False
    finally:
        reset_channel_asker(tok)


@pytest.mark.asyncio
async def test_hitl_mode_requires_approval_for_observe(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("KAGEHA_SKILL_LEARN", "hitl")
    SkillRegistry().create_stub("hitl-skill", "hitl demo")
    root = tmp_path / "ws"
    root.mkdir()
    gate = ApprovalGate(auto_approve=False)
    gate.require = AsyncMock(return_value=False)  # type: ignore[method-assign]
    ctx = HarnessContext(
        workspace=SessionWorkspace(run_id="t", root=root),
        approvals=gate,
        router=SimpleNamespace(),
    )
    ctx.meta["skill_learn_interactive"] = True
    tools = register_skills_tools(ctx)
    manage = tools.get("skill_manage")
    out = await manage.call(
        action="observe",
        name="hitl-skill",
        content="note",
    )
    assert out.startswith("DENIED:")
    gate.require.assert_awaited()


@pytest.mark.asyncio
async def test_skill_load_activates_allowed_tools(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    root = tmp_path / "skills" / "gated"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: gated\ndescription: gated\nallowed-tools: web_search\n---\n\n# G\n",
        encoding="utf-8",
    )
    from kageha import config

    monkeypatch.setattr(config, "skills_dirs", lambda: [tmp_path / "skills"])
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = HarnessContext(
        workspace=SessionWorkspace(run_id="t", root=ws),
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )
    ctx.tools = load_entry_point_tools(ctx)
    tools = register_skills_tools(ctx)
    out = await tools.get("skill_load").call(name="gated")
    assert "skill:gated" in out or "# G" in out or "gated" in out
    assert "gated" in (ctx.meta.get("active_skills") or [])
    names = {s.name for s in _filter_tools_for_skills(ctx)}
    assert "web_search" in names
    assert "skill_run" in names


@pytest.mark.asyncio
async def test_write_file_tool_requires_approval(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("KAGEHA_SKILL_LEARN", "soft")
    SkillRegistry().create_stub("wf2", "wf")
    ws = tmp_path / "ws"
    ws.mkdir()
    gate = ApprovalGate(auto_approve=False)
    gate.require = AsyncMock(return_value=False)  # type: ignore[method-assign]
    ctx = HarnessContext(
        workspace=SessionWorkspace(run_id="t", root=ws),
        approvals=gate,
        router=SimpleNamespace(),
    )
    ctx.meta["skill_learn_interactive"] = True
    tools = register_skills_tools(ctx)
    out = await tools.get("skill_manage").call(
        action="write_file",
        name="wf2",
        content="scripts/x.py\n---\nprint(1)\n",
    )
    assert out.startswith("DENIED:")
    gate.require.assert_awaited()


def test_distill_refine_from_run_result_active_skills(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.delenv("KAGEHA_DISTILL", raising=False)
    monkeypatch.setattr("kageha.memory.learning_loop.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "y")
    reg = SkillRegistry()
    reg.create_stub("make-reel", "reel skill")
    # Rename stub dir name match — create_stub uses name as dir
    result = SimpleNamespace(
        run_id="r3",
        status="success",
        message="Built reel",
        steps=7,
        recovered_failures=[],
        verification_evidence="reel.mp4",
        validated=True,
        active_skills=["make-reel"],
    )
    out = maybe_prompt_skill_distill(
        result, task="Make reel", registry=reg, interactive=True
    )
    assert out is not None
    assert "Refined" in out
    text = (tmp_path / "skills" / "make-reel" / "SKILL.md").read_text()
    assert "## Refinements" in text or "Built reel" in text


def test_mid_run_nudge_helper_once_semantics():
    """Controller injects learning_nudge once; helper must be non-empty on pitfalls."""
    results = [ChatMessage(role="tool", name="bash", content="ERROR: fail", tool_call_id="1")]
    pitfalls = collect_tool_pitfalls(results)
    assert pitfalls
    first = learning_nudge(["make_social_carousel"], pitfalls=pitfalls)
    assert "observe" in first and "make_social_carousel" in first
    # Second call still returns text; once-ness is controller meta flag
    assert learning_nudge(["make_social_carousel"], pitfalls=pitfalls)
