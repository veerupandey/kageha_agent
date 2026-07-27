"""MediaProvider registry (trimmed harness)."""

from __future__ import annotations

from kageha.harness.media import get_provider, list_providers
from kageha.memory.skills import SkillRegistry


def test_builtin_providers_listed():
    names = {p["name"] for p in list_providers()}
    assert "gemini" in names
    assert "fal" in names
    gemini = get_provider("gemini")
    assert gemini is not None
    assert gemini.capabilities.image is True


def test_media_skills_bundled():
    reg = SkillRegistry()
    assert "generate_image_gemini" in reg.skills
    assert "generate_media" in reg.skills
    assert (reg.skills["generate_media"].path / "scripts" / "generate.py").is_file()
