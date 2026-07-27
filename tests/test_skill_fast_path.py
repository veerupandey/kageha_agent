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


def test_bundled_skills_still_load_with_optional_fast_paths():
    reload_skill_fast_paths()
    reg = SkillRegistry()
    assert reg.get("getting_started") is not None
    assert reg.get("computer_use") is not None
    # Collect may be empty without device skills — must not crash.
    phrases, when = collect_skill_fast_paths(reg)
    assert isinstance(phrases, dict)
    assert isinstance(when, (list, tuple, set, frozenset)) or when is not None


def test_router_uses_skill_fast_path_when_declared(tmp_path: Path, monkeypatch):
    """Synthetic skill with fast_paths still routes micro_actions."""
    home = tmp_path / "home"
    skill_dir = home / "skills" / "demo_remote"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: demo_remote\n"
        "description: Demo remote\n"
        "fast-paths:\n"
        "  pause: key:Pause\n"
        "  start: key:Play\n"
        "fast-path-when:\n"
        "  - demo_remote\n"
        "---\n\n# Demo\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    reload_skill_fast_paths()
    ctx = TurnContext(
        run_id="s1",
        objective="control the demo remote",
        artifacts=["demo_remote.md"],
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
    for name in (
        "getting_started",
        "computer_use",
        "web_browse",
        "web_research",
        "memory",
    ):
        assert (root / name / "SKILL.md").is_file()
