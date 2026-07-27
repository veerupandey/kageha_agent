"""Short TV remotes must not open the full agent loop."""

from __future__ import annotations

from kageha.chat.quick_remote import (
    match_app_launch,
    match_remote_key,
    should_quick_remote,
)
from kageha.chat.turn_manager import (
    TurnContext,
    TurnDecision,
    classify_deterministic,
    route_for_decision,
)


def _tv_ctx() -> TurnContext:
    return TurnContext(
        run_id="tv1",
        objective="control the sony bravia tv",
        artifacts=["tv_control.md", "network_tvs.md"],
        recent_user_messages=["let's control the tv", "pause"],
    )


def test_match_remote_keys():
    assert match_remote_key("pause") == "Pause"
    assert match_remote_key("start") == "Play"
    assert match_remote_key("vol up") == "VolumeUp"
    assert match_remote_key("please mute") == "Mute"
    assert match_app_launch("open youtube") == "youtube"


def test_pause_and_start_route_quick_remote():
    ctx = _tv_ctx()
    for msg in ("pause", "start", "volume up", "mute", "stop"):
        d = classify_deterministic(msg, ctx)
        assert d is not None, msg
        assert d.intent == "micro_action", msg
        assert d.requires_tools is False, msg
        assert (
            route_for_decision(d, has_session=True, message=msg, turn_ctx=ctx)
            == "quick_remote"
        ), msg


def test_llm_mis_tag_still_forced_to_quick_remote():
    ctx = _tv_ctx()
    bad = TurnDecision(
        intent="continue_task",
        related_to_current_task=True,
        requires_tools=True,
        reason="llm mistake",
        source="llm",
    )
    assert (
        route_for_decision(bad, has_session=True, message="start", turn_ctx=ctx)
        == "quick_remote"
    )


def test_ambiguous_clarify_goes_to_agent():
    """Self-depth: clarifies are agent turns — model chooses tools/depth."""
    ctx = _tv_ctx()
    d = classify_deterministic("what's that mean?", ctx)
    assert d.requires_tools is True
    assert (
        route_for_decision(
            d, has_session=True, message="what's that mean?", turn_ctx=ctx
        )
        == "resume"
    )


def test_should_quick_remote_needs_tv_context_or_paired(monkeypatch):
    empty = TurnContext(run_id="x", objective="write a poem", artifacts=[])
    monkeypatch.setattr(
        "kageha.chat.quick_remote.device_remote_ready", lambda: False
    )
    assert should_quick_remote("pause", empty) is None
    assert should_quick_remote("pause", _tv_ctx()) == {"kind": "key", "key": "Pause"}
