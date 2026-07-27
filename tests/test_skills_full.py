"""Full Agent Skills frontmatter + resources."""

from __future__ import annotations

from kageha.memory.skills import SkillRegistry, validate_skill


def test_frontmatter_allowed_tools_and_resources(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    root = tmp_path / "skills" / "demo-skill"
    (root / "scripts").mkdir(parents=True)
    (root / "references").mkdir()
    (root / "SKILL.md").write_text(
        "---\n"
        "name: demo-skill\n"
        "description: Demo skill with resources\n"
        "license: MIT\n"
        "allowed-tools: bash read_file write_file\n"
        "compatibility: Requires python\n"
        "metadata:\n  author: test\n"
        "---\n\n# Demo\n\nDo the thing.\n"
    )
    (root / "references" / "notes.md").write_text("# notes\n")
    (root / "scripts" / "hi.py").write_text("print('hi')\n")

    # Point skills_dirs at tmp via KAGEHA_HOME/skills
    reg = SkillRegistry()
    sk = reg.get("demo-skill")
    assert sk is not None
    assert sk.allowed_tools == ["bash", "read_file", "write_file"]
    assert sk.license == "MIT"
    assert "references/notes.md" in sk.list_resources()
    assert "scripts/hi.py" in sk.list_resources()
    assert not validate_skill(sk)

    text = reg.read_resource("demo-skill", "references/notes.md")
    assert "notes" in text
    body = reg.load_body("demo-skill")
    assert "allowed-tools" in body
    assert "Do the thing" in body
