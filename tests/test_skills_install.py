"""Agent Skills install parser + local install."""

from __future__ import annotations

from kageha.memory.skills_install import (
    find_skill_dirs,
    install_skills,
    parse_install_spec,
)


def test_parse_specs():
    assert parse_install_spec("anthropics/skills")["repo"] == "skills"
    assert parse_install_spec("anthropics/skills/pdf")["skill"] == "pdf"
    assert parse_install_spec("anthropics/skills@main/pptx")["skill"] == "pptx"
    assert parse_install_spec("anthropics/skills@main/pptx")["ref"] == "main"
    u = parse_install_spec(
        "https://github.com/anthropics/skills/tree/main/skills/xlsx"
    )
    assert u["skill"] == "xlsx"


def test_install_local_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    skill_dir = tmp_path / "src" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Demo for install test\n---\n\n# Demo\n"
    )
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "hi.sh").write_text("echo hi\n")

    result = install_skills(str(skill_dir), force=True)
    assert "demo-skill" in result.installed
    dest = tmp_path / "skills" / "demo-skill" / "SKILL.md"
    assert dest.is_file()
    assert (tmp_path / "skills" / "demo-skill" / "scripts" / "hi.sh").is_file()


def test_find_skill_dirs_nested(tmp_path):
    a = tmp_path / "skills" / "a"
    b = tmp_path / "skills" / "b"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "SKILL.md").write_text("---\nname: a\ndescription: A\n---\n")
    (b / "SKILL.md").write_text("---\nname: b\ndescription: B\n---\n")
    found = find_skill_dirs(tmp_path / "skills")
    assert {p.name for p in found} == {"a", "b"}
