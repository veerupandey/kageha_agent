"""Agent mode policy (normal/plan/goal)."""

from __future__ import annotations

from pathlib import Path

from kageha.chat.turn_manager import prefer_agent_mode, prefer_loop_mode, TurnDecision
from kageha.loop.mode_policy import (
    AGENT_MODE_FLAG,
    MODE_CHIP_DESCRIPTIONS,
    goal_qa_misfit,
    is_mode_only_message,
    loop_mode_for,
    mode_only_ack,
    normalize_agent_mode,
    parse_mode_slash,
    resolve_agent_mode,
    strip_mode_slash,
    write_agent_mode_flag,
)


def test_normalize_and_loop_map():
    assert normalize_agent_mode("SPEC") == "normal"
    assert loop_mode_for("normal") == "followup"
    assert loop_mode_for("plan") == "full"
    assert loop_mode_for("goal") == "full"
    assert normalize_agent_mode("multitask") == "multitask"
    assert loop_mode_for("multitask") == "full"


def test_slash_parse_and_strip():
    assert parse_mode_slash("/spec build a CLI") is None
    assert parse_mode_slash("/goal ship it") == "goal"
    assert parse_mode_slash("/multitask ship it") == "multitask"
    assert strip_mode_slash("/plan research X") == "research X"
    assert strip_mode_slash("/multitask research X") == "research X"
    assert strip_mode_slash("hello") == "hello"
    # Mode-only must NOT round-trip the token as the objective.
    assert strip_mode_slash("/plan") == ""
    assert strip_mode_slash("/plan   ") == ""
    assert is_mode_only_message("/plan")
    assert is_mode_only_message("plan")
    assert not is_mode_only_message("/spec")
    assert not is_mode_only_message("/plan research X")
    assert not is_mode_only_message("plan the launch")
    assert "objective" in mode_only_ack("plan").lower()
    assert "execute" in mode_only_ack("goal").lower()
    assert "execute" in MODE_CHIP_DESCRIPTIONS["goal"].lower()
    assert goal_qa_misfit("goal", "What is HTTP 429?")
    assert not goal_qa_misfit("goal", "Ship X and verify Y")


def test_flag_and_resolve(tmp_path: Path):
    write_agent_mode_flag(tmp_path, "goal")
    assert (tmp_path / AGENT_MODE_FLAG).is_file()
    assert (
        resolve_agent_mode("", workspace_root=tmp_path, consume_flag=True) == "goal"
    )
    assert not (tmp_path / AGENT_MODE_FLAG).is_file()


def test_prefer_agent_and_loop_mode(tmp_path: Path):
    from kageha.harness.sandbox import SessionWorkspace

    ws = SessionWorkspace(run_id="m", root=tmp_path)
    decision = TurnDecision(
        intent="new_task",
        related_to_current_task=False,
        requires_tools=True,
        discard_old_plan=True,
        reason="t",
    )
    assert prefer_agent_mode("/spec do thing", workspace=ws) == "normal"
    assert (
        prefer_loop_mode(
            "/spec do thing", decision, route="first_run", workspace=ws
        )
        == "followup"
    )
    assert (
        prefer_loop_mode("hi", decision, route="first_run", workspace=ws)
        == "followup"
    )


def test_explicit_agent_mode_beats_workspace_flag(tmp_path: Path):
    write_agent_mode_flag(tmp_path, "plan")
    assert (
        resolve_agent_mode("", explicit="goal", workspace_root=tmp_path) == "goal"
    )


def test_slash_beats_default_explicit_normal():
    """CLI/WebUI defaults often send agent_mode=normal with `/plan …` in text."""
    assert (
        resolve_agent_mode("/plan ship hello.txt", explicit="normal") == "plan"
    )
    assert resolve_agent_mode("/spec build X", explicit="normal") == "normal"
    assert resolve_agent_mode("/goal ship it", explicit=None) == "goal"
    # No slash → explicit still applies.
    assert resolve_agent_mode("ship hello.txt", explicit="plan") == "plan"
    assert resolve_agent_mode("ship hello.txt", explicit=None) == "normal"


def test_plan_is_design_machine(tmp_path: Path):
    from kageha.loop.mode_policy import (
        render_plan_markdown,
        render_skill_gaps_markdown,
        tool_blocked_in_plan_design,
        write_plan_artifact,
    )
    from kageha.loop.planner import PlanStep

    steps = [PlanStep(id="s1", description="Do the thing", tools=[])]
    plan_md = write_plan_artifact(
        tmp_path,
        "plan",
        summary="A plan",
        steps=steps,
        task="Ship X",
        tldr="Ship X quickly",
    )
    assert (tmp_path / "plan.md").is_file()
    assert "Plan (plan)" in plan_md
    assert "TL;DR" in plan_md
    assert tool_blocked_in_plan_design("write_file", approved=False)
    assert not tool_blocked_in_plan_design("write_file", approved=True)
    assert "Approve / Build" in render_plan_markdown(
        "plan", summary="x", steps=steps
    )

    class _Skill:
        name = "web_research"
        description = "Blink-speed research via research_run"

    assert "`web_research`" in render_skill_gaps_markdown(
        task="research", steps=steps, matched=[_Skill()]
    )
