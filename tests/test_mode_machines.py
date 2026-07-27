"""Plan/Spec/Goal machines — Build gate, artifacts, auto_approve independence."""

from __future__ import annotations

from pathlib import Path
import pytest

from kageha.harness.sandbox import SessionWorkspace
from kageha.loop.controller import LoopController
from kageha.loop.mode_policy import (
    PLAN_APPROVED_FLAG,
    clear_agent_mode_flag,
    clear_plan_approved,
    mark_plan_approved,
    plan_already_approved,
    requires_plan_approval,
    resolve_agent_mode,
    tool_blocked_in_plan_design,
    write_agent_mode_flag,
)
from kageha.loop.planner import PlanStep, TaskPlan
from kageha.models.base import ChatMessage, ChatResponse, ChatUsage


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
async def test_spec_writes_three_artifacts_without_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    result, ws, _p = await _run_mode(
        tmp_path, monkeypatch, agent_mode="spec", auto_approve=True, auto_build=False
    )
    assert result.status == "awaiting_plan_approval"
    assert (ws.root / "requirements.md").is_file()
    assert (ws.root / "plan.md").is_file()
    assert (ws.root / "skill_gaps.md").is_file()
    assert not (ws.root / "task_state.json").is_file()
    req = (ws.root / "requirements.md").read_text(encoding="utf-8")
    # Concrete objective → skip clarify with visible continue label (not stub).
    assert "No questions — Continue" in req
    assert "None recorded" not in req


@pytest.mark.asyncio
async def test_spec_clarify_asks_before_plan_and_records_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Ambiguous Spec task: clarify gate → answer in Open questions → plan.md."""
    from kageha.config import sessions_dir
    from kageha.harness.approvals import ApprovalRequest
    from kageha.loop.spec_clarify import SPEC_DESIGN_PHASES

    plan_calls: list[str] = []
    clarify_seen: list[ApprovalRequest] = []
    saw_plan_md_at_plan_time = []

    async def tracking_plan(task, *_a, **_k):
        plan_calls.append(str(task))
        saw_plan_md_at_plan_time.append(
            any(sessions_dir().rglob("plan.md"))
        )
        return _stub_plan()

    async def clarify_approver(req: ApprovalRequest) -> bool:
        clarify_seen.append(req)
        if req.action == "spec_clarify" or req.risk_class == "clarify":
            # Simulate WebUI: user answers Open questions then Continues.
            root = None
            for cand in sessions_dir().rglob("requirements.md"):
                root = cand.parent
                break
            assert root is not None, "requirements.md draft must exist at clarify"
            assert not (root / "plan.md").is_file(), (
                "plan.md must not exist before clarify Continue"
            )
            text = (root / "requirements.md").read_text(encoding="utf-8")
            lines = text.splitlines()
            out: list[str] = []
            injected = False
            for line in lines:
                out.append(line)
                if not injected and line.strip().startswith("- Q:"):
                    out.append("  - A: Stripe")
                    injected = True
            if not injected:
                out.append("- Q: Which payment provider?")
                out.append("  - A: Stripe")
            (root / "requirements.md").write_text("\n".join(out) + "\n", encoding="utf-8")
            return True
        # Build gate — deny so we stop at awaiting_plan_approval.
        return False

    # Force heuristic questions (no LLM propose).
    async def fake_propose(task, **_k):
        from kageha.loop.spec_clarify import ClarifyProposal, heuristic_questions

        return ClarifyProposal(
            questions=heuristic_questions(task),
            assumptions=[],
            skip=False,
            source="test",
        )

    monkeypatch.setattr(
        "kageha.loop.spec_clarify.propose_clarify", fake_propose
    )

    result, ws, project = await _run_mode(
        tmp_path,
        monkeypatch,
        agent_mode="spec",
        auto_approve=False,
        auto_build=False,
        objective="Build a checkout flow for our shop",
        approver=clarify_approver,
        plan_fn=tracking_plan,
    )
    assert result.status == "awaiting_plan_approval"
    assert plan_calls, "make_plan should run after clarify"
    assert any(
        r.action == "spec_clarify" or r.risk_class == "clarify" for r in clarify_seen
    )
    assert clarify_seen[0].risk_class == "clarify" or clarify_seen[
        0
    ].action == "spec_clarify"
    assert saw_plan_md_at_plan_time and saw_plan_md_at_plan_time[0] is False
    req = (ws.root / "requirements.md").read_text(encoding="utf-8")
    assert "## Open questions" in req
    assert "None recorded" not in req
    assert "Stripe" in req
    assert (ws.root / "plan.md").is_file()
    assert (ws.root / "skill_gaps.md").is_file()
    gaps = (ws.root / "skill_gaps.md").read_text(encoding="utf-8")
    assert "Skill gaps" in gaps
    # No project mutations before Build.
    assert (project / "README.md").read_text() == "proj\n"
    assert list(SPEC_DESIGN_PHASES) == [
        "requirements",
        "clarify",
        "plan",
        "skill_gaps",
        "build",
    ]


@pytest.mark.asyncio
async def test_spec_clarify_skip_when_unambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    clarify_asks = []

    async def tracking_approver(req) -> bool:
        clarify_asks.append(req)
        return False  # deny Build

    result, ws, _p = await _run_mode(
        tmp_path,
        monkeypatch,
        agent_mode="spec",
        auto_approve=False,
        auto_build=False,
        objective="Create hello_clarify.py that prints hello",
        approver=tracking_approver,
    )
    assert result.status == "awaiting_plan_approval"
    assert not any(
        getattr(r, "action", "") == "spec_clarify"
        or getattr(r, "risk_class", "") == "clarify"
        for r in clarify_asks
    )
    req = (ws.root / "requirements.md").read_text(encoding="utf-8")
    assert "No questions — Continue" in req


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

    for mode in ("plan", "spec", "goal"):
        assert loop_mode_for(normalize_agent_mode(mode)) == "full"
        assert (
            _resolve_loop_mode(
                {"agent_mode": mode, "loop_mode": "followup"},
                message="ship it",
                agent_mode=mode,
            )
            == "full"
        )
