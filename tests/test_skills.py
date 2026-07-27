from kageha.config import bundled_skills_dir, skills_dirs
from kageha.memory.skills import SkillRegistry


def test_bundled_skills_discoverable():
    reg = SkillRegistry()
    assert "getting_started" in reg.skills
    assert "web_research" in reg.skills
    assert "pdf_ingest" in reg.skills
    assert "memory" in reg.skills
    assert "make_diagram" in reg.skills
    assert "make_presentation" in reg.skills
    assert "make_infographic" in reg.skills
    assert "make_social_carousel" in reg.skills
    catalog = reg.catalog()
    assert "web_research" in catalog
    assert "getting_started" in catalog


def test_package_bundled_skills_in_skills_dirs():
    dirs = skills_dirs()
    bundled = bundled_skills_dir()
    assert bundled in dirs
    assert (bundled / "getting_started" / "SKILL.md").is_file()


def test_skill_stub(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    reg = SkillRegistry()
    path = reg.create_stub("demo-skill", "A demo")
    assert (path / "SKILL.md").is_file()
    reg.reload()
    assert "demo-skill" in reg.skills


def test_skill_observe_and_refine(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    reg = SkillRegistry()
    reg.create_stub("lifecycle-skill", "Lifecycle demo")
    out = reg.manage(
        "observe",
        "lifecycle-skill",
        "API rate-limited; retry with backoff",
        approved=True,
    )
    assert out.startswith("Recorded observation")
    text = (home / "skills" / "lifecycle-skill" / "SKILL.md").read_text()
    assert "## Observations" in text
    assert "rate-limited" in text

    out = reg.manage(
        "refine",
        "lifecycle-skill",
        "Always wait 2s after 429 before retry.",
        approved=True,
    )
    assert out.startswith("Refined")
    text = (home / "skills" / "lifecycle-skill" / "SKILL.md").read_text()
    assert "## Refinements" in text
    assert "wait 2s" in text

    # Surgical refine via patch syntax
    out = reg.manage(
        "refine",
        "lifecycle-skill",
        "1. ...<<<>>>1. Check auth first",
        approved=True,
    )
    assert out.startswith("Refined")
    text = (home / "skills" / "lifecycle-skill" / "SKILL.md").read_text()
    assert "Check auth first" in text

    denied = reg.manage("observe", "lifecycle-skill", "x", approved=False)
    assert denied.startswith("NEEDS_APPROVAL")
