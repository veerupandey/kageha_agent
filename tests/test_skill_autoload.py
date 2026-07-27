"""Skill match + auto-load helpers (intent thresholds + exclusive routing)."""

from pathlib import Path

from kageha.memory.skills import SkillRegistry, autoload_min_score


def _write_skill(
    root: Path,
    name: str,
    description: str,
    *,
    triggers: list[str] | None = None,
    body: str = "# Steps\n1. do it\n",
) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    trigger_block = ""
    if triggers:
        lines = "\n".join(f"  - {t}" for t in triggers)
        trigger_block = f"triggers:\n{lines}\n"
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{trigger_block}---\n\n{body}"
    )


def _registry(tmp_path: Path, monkeypatch) -> SkillRegistry:
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("KAGEHA_SKILL_EMBEDDINGS", "off")
    monkeypatch.delenv("KAGEHA_SKILL_AUTOLOAD_MIN", raising=False)
    from kageha import config

    monkeypatch.setattr(config, "skills_dirs", lambda: [tmp_path / "skills"])
    reg = SkillRegistry()
    reg.reload()
    return reg


def test_match_ranks_name_hits(tmp_path: Path, monkeypatch):
    root = tmp_path / "skills"
    _write_skill(
        root,
        "make_reel",
        "Create short video reels for Instagram",
        triggers=["reel", "short video"],
        body="# Steps\n1. storyboard\n",
    )
    _write_skill(root, "pdf_ingest", "Extract text from PDF documents")

    reg = _registry(tmp_path, monkeypatch)
    matched = reg.match("make a matcha reel for Instagram", limit=2)
    assert matched
    assert matched[0].name == "make_reel"

    loaded = reg.auto_load_for_task("make a matcha reel", limit=1)
    assert "skill:make_reel" in loaded.text
    assert "storyboard" in loaded.text
    assert loaded.names == ["make_reel"]
    assert loaded.scores.get("make_reel", 0) >= autoload_min_score()


def test_autoload_skips_weak_scores(tmp_path: Path, monkeypatch):
    root = tmp_path / "skills"
    # Description shares a weak token with many casual queries ("the") — still
    # should not clear the default floor without a real trigger/name hit.
    _write_skill(
        root,
        "pdf_ingest",
        "Extract text from PDF documents when asked",
    )
    _write_skill(
        root,
        "web_research",
        "Blink-speed research via research_run then a sourced answer",
        triggers=["research", "look up", "find sources"],
    )
    reg = _registry(tmp_path, monkeypatch)
    loaded = reg.auto_load_for_task("hello there, just chatting", limit=4)
    assert loaded.names == []
    assert loaded.text == ""
    # Casual Q&A must not pull research just because "answer" appears in the blurb.
    casual = reg.auto_load_for_task("what is 2+2? just answer", limit=4)
    assert casual.names == []


def test_research_beats_browse_for_lookup(tmp_path: Path, monkeypatch):
    root = tmp_path / "skills"
    _write_skill(
        root,
        "web_research",
        "Blink-speed research via research_run",
        triggers=["research", "look up", "find sources"],
    )
    _write_skill(
        root,
        "web_browse",
        "Interactive browsing with browser_connect",
        triggers=["open the site", "log in", "browse to"],
    )
    reg = _registry(tmp_path, monkeypatch)
    loaded = reg.auto_load_for_task(
        "Look up recent news about Lightpanda and find sources", limit=4
    )
    assert loaded.names == ["web_research"]
    assert "web_browse" not in loaded.names


def test_browse_beats_research_for_login(tmp_path: Path, monkeypatch):
    root = tmp_path / "skills"
    _write_skill(
        root,
        "web_research",
        "Blink-speed research via research_run",
        triggers=["research", "look up", "find sources"],
    )
    _write_skill(
        root,
        "web_browse",
        "Interactive browsing with browser_connect",
        triggers=["open the site", "log in", "browse to", "use comet"],
    )
    reg = _registry(tmp_path, monkeypatch)
    loaded = reg.auto_load_for_task(
        "Log in to the dashboard and click through the settings form", limit=4
    )
    assert loaded.names == ["web_browse"]
    assert "web_research" not in loaded.names


def test_creative_exclusive_picks_one_make_skill(tmp_path: Path, monkeypatch):
    root = tmp_path / "skills"
    _write_skill(
        root,
        "make_reel",
        "Create short video reels",
        triggers=["reel", "short video"],
    )
    _write_skill(
        root,
        "make_social_carousel",
        "Instagram social carousel slides",
        triggers=["carousel", "instagram carousel"],
    )
    _write_skill(
        root,
        "make_presentation",
        "Pitch deck presentations",
        triggers=["presentation", "pitch deck"],
    )
    reg = _registry(tmp_path, monkeypatch)
    loaded = reg.auto_load_for_task(
        "Make an Instagram carousel about matcha", limit=4
    )
    assert loaded.names == ["make_social_carousel"]
    assert "make_reel" not in loaded.names
    assert "make_presentation" not in loaded.names


def test_triggers_frontmatter_parsed(tmp_path: Path, monkeypatch):
    root = tmp_path / "skills"
    _write_skill(
        root,
        "demo_skill",
        "Demo skill for trigger parsing",
        triggers=["exact phrase", "demo"],
    )
    reg = _registry(tmp_path, monkeypatch)
    skill = reg.get("demo_skill")
    assert skill is not None
    assert skill.triggers == ["exact phrase", "demo"]
    scored = reg.match_scored("please run the exact phrase now", limit=3)
    assert scored
    assert scored[0][1].name == "demo_skill"
    assert scored[0][0] >= 4.0


def test_autoload_min_env_override(tmp_path: Path, monkeypatch):
    root = tmp_path / "skills"
    _write_skill(
        root,
        "make_reel",
        "Create short video reels for Instagram",
        triggers=["reel"],
    )
    reg = _registry(tmp_path, monkeypatch)
    monkeypatch.setenv("KAGEHA_SKILL_AUTOLOAD_MIN", "99")
    loaded = reg.auto_load_for_task("make a reel", limit=2)
    assert loaded.names == []


def test_explicit_slash_bypasses_floor_and_disable(tmp_path: Path, monkeypatch):
    from kageha.memory.skills import parse_skill_invocations, strip_skill_invocations

    root = tmp_path / "skills"
    skill_dir = root / "danger_ops"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: danger_ops\n"
        "description: Dangerous operations that must be explicit\n"
        "disable-model-invocation: true\n"
        "triggers:\n"
        "  - danger ops\n"
        "---\n\n# Danger\nBe careful.\n"
    )
    reg = _registry(tmp_path, monkeypatch)

    # Implicit: should not load even with trigger text
    implicit = reg.auto_load_for_task("please run danger ops now", limit=4)
    assert implicit.names == []

    msg = "/danger_ops ship the change carefully"
    forced = parse_skill_invocations(msg, reg)
    assert forced == ["danger_ops"]
    loaded = reg.auto_load_for_task(msg, force_names=forced, limit=4)
    assert loaded.names == ["danger_ops"]
    assert "Be careful" in loaded.text
    assert "Explicit invocation" in loaded.text
    assert strip_skill_invocations(msg, forced) == "ship the change carefully"

    # Codex-style $name and /skill name
    assert parse_skill_invocations("$danger_ops do it", reg) == ["danger_ops"]
    assert parse_skill_invocations("/skill danger_ops do it", reg) == ["danger_ops"]


def test_paths_scope_blocks_without_hints(tmp_path: Path, monkeypatch):
    root = tmp_path / "skills"
    skill_dir = root / "py_review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: py_review\n"
        "description: Review Python modules carefully\n"
        "paths:\n"
        "  - \"**/*.py\"\n"
        "  - \"*.py\"\n"
        "triggers:\n"
        "  - review python\n"
        "---\n\n# Review\nCheck typing.\n"
    )
    reg = _registry(tmp_path, monkeypatch)

    no_paths = reg.auto_load_for_task("review python module for bugs", limit=4)
    assert no_paths.names == []

    with_paths = reg.auto_load_for_task(
        "review python module for bugs in src/foo.py",
        path_hints=["src/foo.py"],
        limit=4,
    )
    assert with_paths.names == ["py_review"]

    # Explicit still works without path hints
    forced = reg.auto_load_for_task(
        "/py_review anything",
        force_names=["py_review"],
        limit=4,
    )
    assert forced.names == ["py_review"]
