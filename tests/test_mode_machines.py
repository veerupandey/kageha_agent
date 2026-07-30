"""Plan/Spec/Goal machines — Build gate, artifacts, auto_approve independence."""

from __future__ import annotations

from pathlib import Path
import pytest

from kageha.harness.approvals import ApprovalOutcome
from kageha.harness.sandbox import SessionWorkspace
from kageha.loop.controller import LoopController
from kageha.loop.mode_policy import (
    PLAN_APPROVED_FLAG,
    clear_agent_mode_flag,
    clear_plan_approved,
    mark_plan_approved,
    plan_already_approved,
    plan_skill_match_text,
    requires_plan_approval,
    resolve_agent_mode,
    tool_blocked_in_plan_design,
    write_agent_mode_flag,
)
from kageha.loop.planner import PlanStep, TaskPlan
from kageha.models.base import ChatMessage, ChatResponse, ChatUsage


async def _deny_approver(_req) -> ApprovalOutcome:
    """Deterministic stand-in for the interactive ``cli_approver``.

    Mode-machine tests exercise the Build/plan-approval gate and expect it to
    land on ``awaiting_plan_approval`` without ever prompting a human. This
    fixture injects that deterministic "not yet approved" decision explicitly
    (Requirement 3.1) instead of leaving ``approver=None`` and relying on the
    production fail-closed default — the mode machines under test should get
    a real, named approval decision, not an implicit non-decision.
    """
    return ApprovalOutcome(False)


def _stub_plan() -> TaskPlan:
    return TaskPlan(
        summary="Design then build",
        steps=[PlanStep(id="s1", description="Write hello.py", tools=["write_file"])],
        milestones=["hello.py exists"],
        source="template",
    )


def _fake_chat_response(text: str = "Done.") -> tuple[object, ChatResponse]:
    class _Model:
        model_id = "fake-model"
        provider = "fake"

    return _Model(), ChatResponse(
        message=ChatMessage(role="assistant", content=text),
        stop_reason="stop",
        usage=ChatUsage(prompt_tokens=1, completion_tokens=1),
    )


async def _run_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    agent_mode: str,
    auto_approve: bool = True,
    auto_build: bool = False,
    objective: str | None = None,
    approver=None,
    skip_explore: bool = True,
    plan_fn=None,
):
    # Inject a deterministic approval decision by default (Requirement 3.1)
    # instead of leaving ``approver=None`` for the mode-machine tests to fall
    # through to the production fail-closed default — the Build/plan gate
    # under test should see an explicit, named decision.
    if approver is None:
        approver = _deny_approver
    home = tmp_path / "home"
    home.mkdir()
    # Session isolation under tmp; keep real models.yaml discoverable.
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    monkeypatch.setenv("KAGEHA_MAX_STEPS", "3")
    monkeypatch.setenv("KAGEHA_MEMORY_ENABLED", "0")
    real_models = Path.home() / ".kageha" / "models.yaml"
    if real_models.is_file():
        (home / "models.yaml").write_text(
            real_models.read_text(encoding="utf-8"), encoding="utf-8"
        )

    async def fake_plan(*_a, **_k):
        return _stub_plan()

    monkeypatch.setattr(
        "kageha.loop.controller.make_plan", plan_fn or fake_plan
    )

    if skip_explore:
        async def fake_explore(**_k):
            return "explore: README present"

        monkeypatch.setattr(
            "kageha.loop.design_explore.explore_before_plan", fake_explore
        )

    # Keep tool loading light; design-gate tests never need real packs.
    from kageha.harness.tools.base import ToolRegistry

    def _empty_tools(ctx):
        return ToolRegistry()

    monkeypatch.setattr(
        "kageha.loop.controller.load_entry_point_tools",
        _empty_tools,
    )

    project = tmp_path / "proj"
    project.mkdir()
    (project / "README.md").write_text("proj\n", encoding="utf-8")

    ws = SessionWorkspace.create(f"mode-{agent_mode}")

    # Patch router.chat so act loops (goal / auto_build) finish without providers.
    async def fake_router_chat(self, *_a, **_k):
        return _fake_chat_response("Goals met with evidence.")

    monkeypatch.setattr(
        "kageha.models.router.ModelRouter.chat",
        fake_router_chat,
    )

    # Verifier: avoid extra LLM; mark goals passed so act can stop.
    async def fake_verify(goal, **_k):
        from kageha.loop.task_state import ValidationSnapshot
        from kageha.loop.verifier import VerifyResult

        for item in goal.items:
            goal.mark(item.id, passes=True, evidence="fake")
        return VerifyResult(
            goal=goal,
            snapshot=ValidationSnapshot(status="pass", notes="fake"),
        )

    monkeypatch.setattr(
        "kageha.loop.controller.verify_with_defects", fake_verify
    )

    ctrl = LoopController(
        auto_approve=auto_approve,
        auto_build=auto_build,
        live=False,
        max_steps_limit=3,
        project_root=str(project),
        platform="cli",
        approver=approver,
    )
    # Avoid "CLI"/desktop phrasing — task_wants_computer can false-positive.
    objective = objective or "Create hello_mode.py that prints hello"
    result = await ctrl.run(
        objective,
        workspace=ws,
        fresh_turn=True,
        turn_task=objective,
        # Stale followup from older clients — deep agent_mode must force full.
        loop_mode="followup",
        agent_mode=agent_mode,
    )
    return result, ws, project


@pytest.mark.asyncio
async def test_plan_without_build_awaits_and_no_task_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    result, ws, project = await _run_mode(
        tmp_path, monkeypatch, agent_mode="plan", auto_approve=True, auto_build=False
    )
    assert result.status == "awaiting_plan_approval"
    assert (ws.root / "plan.md").is_file()
    assert "Plan (plan)" in (ws.root / "plan.md").read_text(encoding="utf-8")
    assert not (ws.root / "requirements.md").is_file()
    assert not (ws.root / "skill_gaps.md").is_file()
    assert not (ws.root / "task_state.json").is_file()
    assert not plan_already_approved(ws.root)
    assert (project / "README.md").read_text() == "proj\n"
    assert not (project / "hello.py").exists()


@pytest.mark.asyncio
async def test_auto_approve_does_not_skip_plan_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    result, _ws, _p = await _run_mode(
        tmp_path,
        monkeypatch,
        agent_mode="plan",
        auto_approve=True,
        auto_build=False,
    )
    assert result.status == "awaiting_plan_approval"


@pytest.mark.asyncio
async def test_plan_auto_build_enters_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    result, ws, _p = await _run_mode(
        tmp_path, monkeypatch, agent_mode="plan", auto_approve=True, auto_build=True
    )
    assert result.status != "awaiting_plan_approval"
    assert (ws.root / "task_state.json").is_file()
    assert plan_already_approved(ws.root)


@pytest.mark.asyncio
async def test_slash_in_objective_selects_plan_despite_normal_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Controller must honor `/plan …` even when agent_mode defaults to normal."""
    result, ws, _p = await _run_mode(
        tmp_path,
        monkeypatch,
        agent_mode="normal",
        auto_approve=True,
        auto_build=False,
        objective="/plan Create slash_plan.py that prints slash",
    )
    assert result.status == "awaiting_plan_approval"
    assert (ws.root / "plan.md").is_file()
    assert "Plan (plan)" in (ws.root / "plan.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_goal_skips_build_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    assert not requires_plan_approval("goal")
    result, ws, _p = await _run_mode(
        tmp_path, monkeypatch, agent_mode="goal", auto_approve=True, auto_build=False
    )
    assert result.status != "awaiting_plan_approval"
    assert (ws.root / "task_state.json").is_file()
    assert not (ws.root / PLAN_APPROVED_FLAG).is_file()


@pytest.mark.asyncio
async def test_fresh_plan_clears_stale_approved_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Second Plan turn in the same session must re-await Build."""
    result, ws, _p = await _run_mode(
        tmp_path, monkeypatch, agent_mode="plan", auto_approve=True, auto_build=True
    )
    assert result.status != "awaiting_plan_approval"
    assert plan_already_approved(ws.root)

    objective = "Create second_plan.py that prints second"
    result_stale = await LoopController(
        auto_approve=True,
        auto_build=False,
        live=False,
        max_steps_limit=3,
        project_root=str(tmp_path / "proj"),
        platform="cli",
        approver=_deny_approver,
    ).run(
        objective,
        workspace=ws,
        fresh_turn=True,
        turn_task=objective,
        loop_mode="followup",
        agent_mode="plan",
    )
    assert result_stale.status == "awaiting_plan_approval"
    assert not plan_already_approved(ws.root)


def test_explicit_mode_beats_stale_flag(tmp_path: Path):
    write_agent_mode_flag(tmp_path, "plan")
    assert (
        resolve_agent_mode("", explicit="goal", workspace_root=tmp_path) == "goal"
    )
    clear_agent_mode_flag(tmp_path)


def test_design_tool_block_api():
    assert tool_blocked_in_plan_design("write_file", approved=False)
    assert tool_blocked_in_plan_design("bash", approved=False)
    assert tool_blocked_in_plan_design("spawn_task_graph", approved=False)
    assert not tool_blocked_in_plan_design("read_file", approved=False)
    assert not tool_blocked_in_plan_design("write_file", approved=True)


def test_mark_clear_plan_approved(tmp_path: Path):
    assert not plan_already_approved(tmp_path)
    mark_plan_approved(tmp_path)
    assert plan_already_approved(tmp_path)
    clear_plan_approved(tmp_path)
    assert not plan_already_approved(tmp_path)


@pytest.mark.asyncio
async def test_awaiting_build_never_leaves_plan_approved_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Deny/timeout at Build must not leave plan_approved.flag on disk."""
    result, ws, _p = await _run_mode(
        tmp_path, monkeypatch, agent_mode="plan", auto_approve=True, auto_build=False
    )
    assert result.status == "awaiting_plan_approval"
    assert not plan_already_approved(ws.root)
    assert not (ws.root / PLAN_APPROVED_FLAG).is_file()
    plan_md = (ws.root / "plan.md").read_text(encoding="utf-8")
    assert "Follow-up:" not in plan_md
    assert "source" not in plan_md or "followup" not in plan_md.lower()


@pytest.mark.asyncio
async def test_explicit_normal_ignores_stale_escalate_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Normal chip must not enter Plan explore→Build from escalate_plan.flag."""
    from kageha.chat.turn_manager import ESCALATE_PLAN_FLAG

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    monkeypatch.setenv("KAGEHA_MAX_STEPS", "3")
    monkeypatch.setenv("KAGEHA_MEMORY_ENABLED", "0")

    async def fake_plan(*_a, **_k):
        return _stub_plan()

    monkeypatch.setattr("kageha.loop.controller.make_plan", fake_plan)

    async def fake_explore(**_k):
        return "should not explore in normal"

    monkeypatch.setattr(
        "kageha.loop.design_explore.explore_before_plan", fake_explore
    )

    from kageha.harness.tools.base import ToolRegistry

    monkeypatch.setattr(
        "kageha.loop.controller.load_entry_point_tools",
        lambda _ctx: ToolRegistry(),
    )

    async def fake_router_chat(self, *_a, **_k):
        return _fake_chat_response("readme says hello")

    monkeypatch.setattr(
        "kageha.models.router.ModelRouter.chat", fake_router_chat
    )

    project = tmp_path / "proj"
    project.mkdir()
    (project / "README.md").write_text("# Hello\n", encoding="utf-8")
    ws = SessionWorkspace.create("escalate-ignore")
    (ws.root / ESCALATE_PLAN_FLAG).write_text("stale escalate\n", encoding="utf-8")
    write_agent_mode_flag(ws.root, "plan")

    result = await LoopController(
        auto_approve=True,
        auto_build=False,
        live=False,
        max_steps_limit=3,
        project_root=str(project),
        platform="cli",
        approver=_deny_approver,
    ).run(
        "Summarize the README in one sentence",
        workspace=ws,
        fresh_turn=True,
        turn_task="Summarize the README in one sentence",
        loop_mode="followup",
        agent_mode="normal",
    )
    assert result.status != "awaiting_plan_approval"
    assert not (ws.root / ESCALATE_PLAN_FLAG).is_file()
    assert not (ws.root / "plan.md").is_file()


@pytest.mark.asyncio
async def test_plan_different_ask_does_not_write_followup_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Second Plan objective must rewrite a real plan.md, not Follow-up: stub."""
    result, ws, _p = await _run_mode(
        tmp_path, monkeypatch, agent_mode="plan", auto_approve=True, auto_build=False
    )
    assert result.status == "awaiting_plan_approval"
    first_plan = (ws.root / "plan.md").read_text(encoding="utf-8")
    assert "Follow-up:" not in first_plan

    # Seed a prior goal so resume path would have used make_followup_plan before.
    from kageha.loop.goal_card import GoalCard

    GoalCard.from_task(
        "Create hello_mode.py that prints hello",
        milestones=["done"],
    ).save(ws.path("goal_card.json"))

    calls: list[str] = []

    async def tracking_plan(task, *_a, **_k):
        calls.append(str(task))
        return TaskPlan(
            summary="Second design",
            steps=[
                PlanStep(
                    id="s1",
                    description="Write second_plan.py",
                    tools=["write_file"],
                )
            ],
            milestones=["second_plan.py exists"],
            source="template",
        )

    monkeypatch.setattr("kageha.loop.controller.make_plan", tracking_plan)

    async def fake_explore(**_k):
        return "explore: second"

    monkeypatch.setattr(
        "kageha.loop.design_explore.explore_before_plan", fake_explore
    )

    result2 = await LoopController(
        auto_approve=True,
        auto_build=False,
        live=False,
        max_steps_limit=3,
        project_root=str(tmp_path / "proj"),
        platform="cli",
        approver=_deny_approver,
    ).run(
        "Create second_plan.py that prints second",
        workspace=ws,
        fresh_turn=False,
        turn_task="Create second_plan.py that prints second",
        loop_mode="full",
        agent_mode="plan",
    )
    assert result2.status == "awaiting_plan_approval"
    assert calls, "expected make_plan for Plan different-ask"
    plan_md = (ws.root / "plan.md").read_text(encoding="utf-8")
    assert "Follow-up:" not in plan_md
    assert "second_plan" in plan_md.lower() or "Second design" in plan_md


def test_deep_modes_force_full_over_stale_followup():
    from kageha.app_server import _resolve_loop_mode
    from kageha.loop.mode_policy import loop_mode_for, normalize_agent_mode

    for mode in ("plan", "goal"):
        assert loop_mode_for(normalize_agent_mode(mode)) == "full"
        assert (
            _resolve_loop_mode(
                {"agent_mode": mode, "loop_mode": "followup"},
                message="ship it",
                agent_mode=mode,
            )
            == "full"
        )


def test_plan_skill_match_text_prefers_objective(tmp_path: Path):
    root = tmp_path / "session"
    root.mkdir()
    (root / "plan.md").write_text(
        "# Plan (plan)\n\n"
        "**Objective:** Research kageha.ca and Bare & Fair connections\n\n"
        "**TL;DR:** Sourced findings report with relationship details.\n\n"
        "summary\n",
        encoding="utf-8",
    )
    text = plan_skill_match_text(root, "Execute the approved plan.")
    assert "Research kageha.ca" in text
    assert "Sourced findings" in text
    assert "Execute the approved plan." in text


def test_plan_needs_clarify_heuristic():
    from kageha.loop.mode_policy import is_plan_build_prompt, plan_needs_clarify

    assert plan_needs_clarify("add auth") is True
    assert plan_needs_clarify("make the app better somehow") is True
    assert (
        plan_needs_clarify("Create hello_mode.py that prints hello") is False
    )
    assert is_plan_build_prompt("Execute the approved plan.") is True
    assert is_plan_build_prompt("use Redis instead") is False


@pytest.mark.asyncio
async def test_plan_clarify_then_continue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ambiguous Plan ask pauses with awaiting_clarify; answer continues design."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    monkeypatch.setenv("KAGEHA_MAX_STEPS", "3")
    monkeypatch.setenv("KAGEHA_MEMORY_ENABLED", "0")

    async def fake_plan(*_a, **_k):
        return _stub_plan()

    monkeypatch.setattr("kageha.loop.controller.make_plan", fake_plan)

    async def fake_explore(**_k):
        return "explore ok"

    monkeypatch.setattr(
        "kageha.loop.design_explore.explore_before_plan", fake_explore
    )
    from kageha.harness.tools.base import ToolRegistry

    monkeypatch.setattr(
        "kageha.loop.controller.load_entry_point_tools",
        lambda _ctx: ToolRegistry(),
    )
    project = tmp_path / "proj"
    project.mkdir()
    ws = SessionWorkspace.create("clarify-plan")
    monkeypatch.setenv("KAGEHA_SESSION", str(ws.root))

    ctrl = LoopController(
        auto_approve=True,
        auto_build=False,
        live=False,
        max_steps_limit=3,
        project_root=str(project),
        platform="cli",
        defer_human_input=True,
        approver=_deny_approver,
    )
    r1 = await ctrl.run(
        "authenticate users somehow with either JWT or sessions",
        workspace=ws,
        fresh_turn=True,
        turn_task="authenticate users somehow with either JWT or sessions",
        loop_mode="full",
        agent_mode="plan",
    )
    assert r1.status == "awaiting_clarify"
    assert (ws.root / "clarify_pending.json").is_file()

    r2 = await ctrl.run(
        "Use JWT with refresh tokens",
        workspace=ws,
        fresh_turn=False,
        turn_task="Use JWT with refresh tokens",
        loop_mode="full",
        agent_mode="plan",
    )
    assert r2.status == "awaiting_plan_approval"
    assert not (ws.root / "clarify_pending.json").is_file()
    assert (ws.root / "plan.md").is_file()


@pytest.mark.asyncio
async def test_plan_suggest_revises_without_approving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from kageha.harness.approvals import ApprovalOutcome

    calls = {"n": 0}

    async def fake_plan(task, *_a, **_k):
        calls["n"] += 1
        desc = "prefer Redis step" if "Redis" in str(task) else "Write hello.py"
        return TaskPlan(
            summary=f"plan-{calls['n']}",
            steps=[PlanStep(id="s1", description=desc, tools=["write_file"])],
            milestones=["done"],
            source="template",
        )

    suggest_once = {"done": False}

    async def approver(_req):
        if not suggest_once["done"]:
            suggest_once["done"] = True
            return ApprovalOutcome(False, feedback="prefer Redis")
        return ApprovalOutcome(False)  # bare deny after revise → awaiting

    result, ws, _p = await _run_mode(
        tmp_path,
        monkeypatch,
        agent_mode="plan",
        auto_approve=False,
        auto_build=False,
        objective="Create hello_suggest.py that prints hello",
        approver=approver,
        plan_fn=fake_plan,
    )
    assert result.status == "awaiting_plan_approval"
    assert calls["n"] >= 2
    plan_md = (ws.root / "plan.md").read_text(encoding="utf-8")
    assert "Redis" in plan_md or "prefer Redis" in plan_md
