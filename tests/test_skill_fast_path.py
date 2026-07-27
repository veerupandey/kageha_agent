"""Skill fast-path frontmatter → chat router."""

from __future__ import annotations

from pathlib import Path

from kageha.chat.quick_remote import (
    reload_skill_fast_paths,
    should_quick_remote,
)
from kageha.chat.turn_manager import TurnContext, classify_deterministic, route_for_decision
from kageha.memory.skills import (
    SkillRegistry,
    _parse_fast_path_action,
    collect_skill_fast_paths,
)


def test_parse_fast_path_action_forms():
    assert _parse_fast_path_action("key:Pause") == {"kind": "key", "key": "Pause"}
    assert _parse_fast_path_action("launch:youtube") == {
        "kind": "launch",
        "app": "youtube",
    }
    assert _parse_fast_path_action("status") == {"kind": "status"}
    assert _parse_fast_path_action({"kind": "key", "key": "Mute"}) == {
        "kind": "key",
        "key": "Mute",
    }


def test_sony_bravia_skill_declares_fast_paths():
    reload_skill_fast_paths()
    reg = SkillRegistry()
    skill = reg.get("sony_bravia")
    assert skill is not None
    assert skill.fast_paths["pause"] == {"kind": "key", "key": "Pause"}
    assert skill.fast_paths["start"] == {"kind": "key", "key": "Play"}
    assert skill.fast_paths["open youtube"] == {"kind": "launch", "app": "youtube"}
    assert "tv_control" in skill.fast_path_when
    phrases, when = collect_skill_fast_paths(reg)
    assert phrases["pause"]["key"] == "Pause"
    assert any("bravia" in w.lower() for w in when)


def test_router_uses_skill_fast_path():
    reload_skill_fast_paths()
    ctx = TurnContext(
        run_id="s1",
        objective="control the tv",
        artifacts=["tv_control.md"],
    )
    assert should_quick_remote("pause", ctx) == {"kind": "key", "key": "Pause"}
    d = classify_deterministic("start", ctx)
    assert d is not None
    assert d.intent == "micro_action"
    assert (
        route_for_decision(d, has_session=True, message="start", turn_ctx=ctx)
        == "quick_remote"
    )


def test_skill_md_files_exist():
    root = Path(__file__).resolve().parents[1] / "src/kageha/bundled_skills"
    for name in ("sony_bravia", "network_scan", "android_tv"):
        assert (root / name / "SKILL.md").is_file()
