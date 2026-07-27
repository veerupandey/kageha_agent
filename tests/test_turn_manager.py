"""Turn manager — micro-paths or self-depth agent."""

from __future__ import annotations

import asyncio
import json

from kageha.chat.turn_manager import (
    TurnContext,
    TurnDecision,
    build_turn_context,
    classify_deterministic,
    classify_turn,
    expand_user_message,
    ground_artifact_followup,
    new_task_prompt,
    persist_turn_decision,
    prefer_loop_mode,
    route_for_decision,
    resolve_artifact_references,
    topics_related,
    wants_full_plan,
)
from kageha.harness.sandbox import SessionWorkspace


def _ctx(
    *,
    objective: str = "Create a 10-slide presentation about coaching",
    artifacts: list[str] | None = None,
    run_id: str | None = "sess1",
) -> TurnContext:
    return TurnContext(
        run_id=run_id,
        objective=objective,
        artifacts=artifacts
        or [
            "artifacts/coaching_deck.pptx",
            "artifacts/slide_01.png",
        ],
    )


def test_teach_bedrock_is_new_task_discard_plan():
    d = classify_deterministic("Teach me AWS Bedrock", _ctx())
    assert d.intent == "new_task"
    assert d.discard_old_plan is True
    assert d.related_to_current_task is False
    assert route_for_decision(d, has_session=True, message="Teach me AWS Bedrock") == "new_run"


def test_where_saved_is_quick_where():
    msg = "where did you save it?"
    d = classify_deterministic(msg, _ctx())
    assert d.intent == "status"
    assert d.requires_tools is False
    assert route_for_decision(d, has_session=True, message=msg) == "quick_where"


def test_status_is_quick_status():
    msg = "status"
    d = classify_deterministic(msg, _ctx())
    assert d.intent == "status"
    assert route_for_decision(d, has_session=True, message=msg) == "quick_status"


def test_make_dog_image_darker_modify():
    ctx = _ctx(
        objective="Generate a cute dog image for the homepage",
        artifacts=["artifacts/dog.png", "artifacts/dog_v2.png"],
    )
    msg = "make the dog image darker"
    d = classify_deterministic(msg, ctx)
    assert d.intent == "modify_artifact"
    assert d.related_to_current_task is True
    assert any("dog" in a for a in d.reuse_artifacts)
    assert route_for_decision(d, has_session=True, message=msg) == "resume"


def test_make_it_shorter_continues():
    msg = "make it shorter"
    d = classify_deterministic(msg, _ctx())
    assert d.intent in {"continue_task", "modify_artifact"}
    assert route_for_decision(d, has_session=True, message=msg) == "resume"


def test_explicit_new_task_and_start_over():
    for msg in ("new task please", "start over with something else", "different topic"):
        d = classify_deterministic(msg, _ctx())
        assert d.intent == "new_task", msg
        assert d.discard_old_plan is True


def test_cancel_route():
    d = classify_deterministic("cancel", _ctx())
    assert d.intent == "cancel"
    assert route_for_decision(d, has_session=True, message="cancel") == "cancel"


def test_first_message_no_session_is_agent():
    d = classify_deterministic("Build a CLI todo app", TurnContext())
    assert d.intent == "new_task"
    assert d.requires_tools is True
    assert route_for_decision(d, has_session=False) == "first_run"


def test_easy_qa_goes_to_agent_self_depth():
    """Self-depth: greetings/Q&A are agent turns (model may use 0 tools)."""
    for msg in ("hi", "what is DNS?", "how do I use Docker?", "thanks"):
        for ctx in (TurnContext(), _ctx()):
            d = classify_deterministic(msg, ctx)
            route = route_for_decision(
                d, has_session=ctx.has_session, message=msg, turn_ctx=ctx
            )
            assert route in {"first_run", "resume", "new_run"}, (msg, route)
            assert route not in {
                "quick_where",
                "quick_status",
                "quick_remote",
                "cancel",
            }


def test_scan_network_is_agent():
    ctx = _ctx(objective="Control Sony Bravia TV")
    msg = "scan the network for devices"
    d = classify_deterministic(msg, ctx)
    assert d.requires_tools is True
    route = route_for_decision(d, has_session=True, message=msg, turn_ctx=ctx)
    assert route in {"new_run", "resume", "first_run"}


def test_device_status_is_agent():
    ctx = _ctx(objective="Control Sony Bravia TV")
    d = classify_deterministic("is the TV on?", ctx)
    assert d.requires_tools is True
    assert route_for_decision(d, has_session=True, message="is the TV on?", turn_ctx=ctx) in {
        "resume",
        "new_run",
    }


def test_clarify_goes_to_agent():
    ctx = _ctx(objective="Control Sony Bravia TV")
    d = classify_deterministic("what's that mean?", ctx)
    assert d.requires_tools is True
    assert (
        route_for_decision(d, has_session=True, message="what's that mean?", turn_ctx=ctx)
        == "resume"
    )


def test_prefer_loop_mode_followup_by_default():
    d = TurnDecision(intent="new_task", requires_tools=True)
    assert prefer_loop_mode("open browser", d, route="first_run") == "followup"
    assert prefer_loop_mode("scan the network", d, route="resume") == "followup"


def test_prefer_loop_mode_full_for_plan_command():
    d = TurnDecision(intent="new_task", requires_tools=True)
    assert prefer_loop_mode("/plan fix the flaky CI", d, route="first_run") == "full"
    assert wants_full_plan("/plan refactor auth")


def test_prefer_loop_mode_full_for_escalate_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ws = SessionWorkspace.create("esc1")
    # escalate_plan writes agent_mode.flag (and a note flag); loop depth reads mode.
    (ws.root / "agent_mode.flag").write_text("plan\n", encoding="utf-8")
    (ws.root / "escalate_plan.flag").write_text("big job\n", encoding="utf-8")
    d = TurnDecision(intent="continue_task", requires_tools=True)
    assert prefer_loop_mode("continue", d, route="resume", workspace=ws) == "full"


def test_prefer_loop_mode_na_for_micro():
    d = TurnDecision(intent="micro_action", requires_tools=False)
    assert prefer_loop_mode("pause", d, route="quick_remote") == "n/a"
    assert prefer_loop_mode("status", d, route="quick_status") == "n/a"


def test_topics_related_positive_and_negative():
    assert topics_related(
        "add another coaching tip slide",
        "Create a presentation about coaching",
    )
    assert not topics_related(
        "Teach me AWS Bedrock",
        "Create a 10-slide presentation about coaching",
    )


def test_new_task_prompt_strips_plan_and_mentions_prior():
    text = new_task_prompt(
        "/plan Teach me AWS Bedrock",
        prior_run_id="abc123",
        reuse_artifacts=["artifacts/notes.md"],
    )
    assert "Teach me AWS Bedrock" in text
    assert not text.lower().startswith("/plan")
    assert "abc123" in text
    assert "do not continue the old plan" in text.lower() or "Start a new plan" in text


def test_build_turn_context_from_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ws = SessionWorkspace.create("tm1")
    ws.write_text(
        "goal_card.json",
        json.dumps({"task": "Make slides about cats", "items": []}),
    )
    ws.write_text(
        "task_state.json",
        json.dumps({"objective": "Make slides about cats", "stages": []}),
    )
    (ws.root / "artifacts").mkdir(exist_ok=True)
    (ws.root / "artifacts" / "cats.pptx").write_bytes(b"x")
    ctx = build_turn_context(ws)
    assert ctx.run_id == "tm1"
    assert "cats" in ctx.objective.lower()
    assert any("cats.pptx" in a for a in ctx.artifacts)


def test_pending_clarification_answer_resumes_task():
    ctx = _ctx(objective="Make five coaching slides")
    ctx.pending_question = "Use a professional visual style?"
    ctx.pending_yes_label = "Professional"
    ctx.pending_no_label = "Keep current"
    ctx.pending_request = "Improve the five slides"

    decision = classify_deterministic("yes", ctx)
    assert decision.intent == "continue_task"
    assert decision.requires_tools is True
    assert route_for_decision(decision, has_session=True, message="yes") == "resume"

    expanded = expand_user_message("yes", ctx)
    assert "Improve the five slides" in expanded
    assert "Professional" in expanded


def test_negative_feedback_resolves_latest_artifacts():
    ctx = _ctx()
    ctx.recent_artifacts = [
        "artifacts/carousel/slide_01.png",
        "artifacts/carousel/slide_02.png",
    ]
    decision = classify_deterministic("these are so boring", ctx)
    assert decision.intent == "modify_artifact"
    assert decision.requires_tools is True
    assert decision.reuse_artifacts == ctx.recent_artifacts

    refs = resolve_artifact_references(
        "these are so boring",
        ctx,
        preferred=decision.reuse_artifacts,
    )
    grounded = ground_artifact_followup("these are so boring", refs)
    assert "slide_01.png" in grounded


def test_classify_turn_is_sync_deterministic():
    """No LLM router — classify_turn never needs the network."""
    d = asyncio.run(classify_turn("what is a subnet mask?", TurnContext()))
    assert d.requires_tools is True
    assert d.reason.startswith("agent turn")


def test_comet_browse_expands_to_tool_instructions():
    msg = "can you please open comet and browse to kageha.ca"
    expanded = expand_user_message(msg, TurnContext())
    assert "browser_connect" in expanded
    assert "comet" in expanded.lower()
    assert "https://kageha.ca" in expanded
    assert "browser_open" in expanded
    assert "Do NOT list capabilities" in expanded or "do NOT list capabilities" in expanded
    assert route_for_decision(
        classify_deterministic(msg, TurnContext()),
        has_session=False,
        message=msg,
    ) == "first_run"


def test_persist_turn_decision(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ws = SessionWorkspace.create("persist1")
    d = TurnDecision(intent="continue_task", reason="test")
    persist_turn_decision(ws, d, message="hi", route="resume")
    chat = (ws.root / "chat.jsonl").read_text(encoding="utf-8")
    assert "turn_decision" in chat
