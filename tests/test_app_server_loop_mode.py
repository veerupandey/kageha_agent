"""App server loop_mode defaults to chat-speed followup."""

from kageha.app_server import _resolve_loop_mode


def test_default_loop_mode_is_followup():
    assert _resolve_loop_mode({}, message="what's on my network?") == "followup"


def test_explicit_full_and_act():
    assert _resolve_loop_mode({"loop_mode": "full"}, message="hi") == "full"
    assert _resolve_loop_mode({"loop_mode": "act"}, message="hi") == "followup"


def test_plan_prefix_selects_full():
    assert _resolve_loop_mode({}, message="/plan research competitors") == "full"


def test_deep_agent_mode_beats_stale_followup():
    """Older clients sent followup with agent_mode=goal/plan/spec — deep wins."""
    for mode in ("plan", "spec", "goal"):
        assert (
            _resolve_loop_mode(
                {"agent_mode": mode, "loop_mode": "followup"},
                message="finish the migration",
                agent_mode=mode,
            )
            == "full"
        )
