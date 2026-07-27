"""Carousel Image Studio — skill scripts + library (not harness tools)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.builtin import load_entry_point_tools
from kageha.harness.tools.paths import rel_to_workspace
from kageha.creative.carousel_studio import (
    _normalize_product_image_path,
    _parse_json_blob,
    _repair_canva_copy_spacing,
    _system_prompt,
    generate_slide,
    plan_carousel_slide_generation,
    write_prompts,
)


def test_parse_json_blob_fenced():
    raw = 'Here you go:\n```json\n{"slides": [{"prompt": "x"}]}\n```\n'
    data = _parse_json_blob(raw)
    assert data is not None
    assert data["slides"][0]["prompt"] == "x"


def test_rel_to_workspace_resolves_symlink_roots(tmp_path: Path):
    root = (tmp_path / "ws").resolve()
    root.mkdir()
    nested = root / "carousel"
    nested.mkdir()
    f = nested / "prompts.json"
    f.write_text("{}", encoding="utf-8")
    assert rel_to_workspace(f, root) == "carousel/prompts.json"


def test_normalize_product_image_path(tmp_path: Path):
    root = tmp_path.resolve()
    (root / "artifacts" / "product").mkdir(parents=True)
    abs_p = root / "artifacts" / "product" / "p.jpg"
    abs_p.write_bytes(b"x")
    assert _normalize_product_image_path(str(abs_p), root) == "artifacts/product/p.jpg"
    assert _normalize_product_image_path("../etc/passwd", root) == ""


def test_repair_canva_copy_spacing():
    canva = _repair_canva_copy_spacing(
        {"heading": "MATCHAMADE SIMPLE"},
        {"headline": "Matcha Made Simple"},
    )
    assert " " in (canva.get("heading") or "")
    assert canva.get("heading") == "Matcha Made Simple"


def test_carousel_tools_not_in_harness_registry(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    root = tmp_path / "session"
    root.mkdir()
    (root / "artifacts").mkdir()
    ctx = HarnessContext(
        workspace=SessionWorkspace(run_id="t", root=root),
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )
    reg = load_entry_point_tools(ctx)
    assert "write_carousel_prompts" not in reg.names()
    assert "generate_carousel_slide" not in reg.names()
    # Media pack is opt-in; carousel workflows are skill-owned.
    assert "gemini_generate_image" not in reg.names()
    assert "skill_run" in reg.names()


@pytest.mark.asyncio
async def test_mcp_serve_registry_includes_skill_tools(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    from kageha.mcp.server import _build_registry

    ctx = await _build_registry(auto_approve=True)
    names = ctx.tools.names()
    for name in (
        "skill_list",
        "skill_load",
        "skill_run",
        "skill_list_resources",
    ):
        assert name in names, name
    assert "write_carousel_prompts" not in names
    assert "generate_carousel_slide" not in names


def test_make_social_carousel_skill_uses_scripts():
    from kageha.memory.skills import SkillRegistry

    reg = SkillRegistry()
    skill = reg.get("make_social_carousel")
    assert skill is not None
    body = skill.body or ""
    assert "scripts/write_prompts.py" in body
    assert "scripts/generate_slide.py" in body
    assert "skill_run" in body
    assert "import_product_images" in body
    assert skill.allowed_tools
    scripts = Path(skill.path) / "scripts"
    assert (scripts / "write_prompts.py").is_file()
    assert (scripts / "generate_slide.py").is_file()
    assert (scripts / "compose_slide.py").is_file()
    assert (scripts / "qa_carousel.py").is_file()


def test_make_brand_carousel_points_at_social_scripts():
    from kageha.memory.skills import SkillRegistry

    body = SkillRegistry().get("make_brand_carousel").body or ""
    assert "make_social_carousel" in body
    assert "write_prompts.py" in body
    assert "generate_slide.py" in body


def test_prompt_writer_system_mentions_product_and_research():
    text = _system_prompt(
        instruction="matcha tips carousel recreate",
        slide_count=6,
        aspect_ratio="4:5",
        brand_url="https://brand.example",
        product_url="https://shop.example/p",
        reference_url="https://ig.example/p",
        n_refs=2,
        n_products=1,
        product_names=["product_00.jpg"],
        research_notes="brand notes",
        use_web_search=True,
    )
    assert "Nano Banana" in text or "nano-banana" in text
    assert "productImagePath" in text
    assert "Image Studio" in text


def test_write_prompts_cli_help():
    import subprocess
    import sys

    script = (
        Path(__file__).resolve().parents[1]
        / "src/kageha/bundled_skills/make_social_carousel/scripts/write_prompts.py"
    )
    r = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "--instruction" in r.stdout


def test_generate_slide_cli_help():
    import subprocess
    import sys

    script = (
        Path(__file__).resolve().parents[1]
        / "src/kageha/bundled_skills/make_social_carousel/scripts/generate_slide.py"
    )
    r = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "--slide" in r.stdout


@pytest.mark.asyncio
async def test_write_prompts_persists_research_and_products(tmp_path: Path):
    root = tmp_path.resolve()
    (root / "artifacts" / "product").mkdir(parents=True)
    (root / "artifacts" / "reference").mkdir(parents=True)
    (root / "artifacts" / "product" / "product_00.jpg").write_bytes(b"fakejpg")
    (root / "artifacts" / "reference" / "ref_01.jpg").write_bytes(b"fakeref")

    fake_json = {
        "creativeDirection": "calm educational carousel",
        "researchSummary": "Brand is premium matcha.",
        "productTruthSummary": "Use real tin.",
        "designSystem": {"palette": ["#F5F0E6"]},
        "slides": [
            {
                "slideNumber": 1,
                "role": "hook",
                "title": "Hook",
                "prompt": "A" * 220 + " crisp legible typography high contrast",
                "productImagePath": "artifacts/product/product_00.jpg",
                "copy": {"headline": "Matcha Made Simple"},
                "canvaText": {"heading": "Matcha Made Simple"},
            },
            {
                "slideNumber": 2,
                "role": "value",
                "title": "Tip",
                "prompt": "B" * 220 + " crisp typography",
                "copy": {"headline": "Whisk Well"},
                "canvaText": {"heading": "Whisk Well"},
            },
            {
                "slideNumber": 3,
                "role": "cta",
                "title": "CTA",
                "prompt": "C" * 220 + " crisp typography",
                "copy": {"headline": "Shop Now"},
                "canvaText": {"heading": "Shop Now"},
            },
        ],
    }

    async def fake_gemini(**kwargs):
        return fake_json, json.dumps(fake_json), None

    with (
        patch(
            "kageha.creative.carousel_studio._fetch_url_research",
            new=AsyncMock(return_value="page notes"),
        ),
        patch(
            "kageha.creative.carousel_studio._gemini_generate_json",
            new=fake_gemini,
        ),
    ):
        out = await write_prompts(
            root,
            instruction="Kageha matcha product carousel recreate",
            slide_count=3,
            product_url="https://shop.example/matcha",
            brand_url="https://brand.example",
            use_web_search=False,
        )
    assert not out.startswith("ERROR:"), out
    data = json.loads(out)
    assert data["ok"] is True
    assert data["slideCount"] == 3
    assert (root / "carousel" / "prompts.json").is_file()
    assert (root / "carousel" / "research.md").is_file()
    assert "generate_slide.py" in data["next"]
    prompts = json.loads((root / "carousel" / "prompts.json").read_text())
    assert prompts["aspectRatio"] == "4:5"
    assert any(s.get("productImagePath") for s in prompts["slides"])


def test_plan_requires_product_path_when_packshots(tmp_path: Path):
    root = tmp_path.resolve()
    (root / "artifacts" / "product").mkdir(parents=True)
    (root / "artifacts" / "product" / "product_00.jpg").write_bytes(b"x")
    prompts = {
        "aspectRatio": "4:5",
        "productImages": ["artifacts/product/product_00.jpg"],
        "productLockRequired": True,
        "slides": [
            {
                "slideNumber": 1,
                "role": "hook",
                "prompt": "cover slide with product",
                "productImagePath": "",
            }
        ],
    }
    plan, err = plan_carousel_slide_generation(prompts, 1, root=root)
    assert plan is None
    assert err and "requires image_path" in err


def test_plan_uses_aspect_from_prompts(tmp_path: Path):
    root = tmp_path.resolve()
    prompts = {
        "aspectRatio": "9:16",
        "slides": [
            {"slideNumber": 1, "role": "hook", "prompt": "story slide " + ("x" * 40)}
        ],
    }
    plan, err = plan_carousel_slide_generation(prompts, 1, root=root)
    assert err is None
    assert plan is not None
    assert plan["aspect_ratio"] == "9:16"


@pytest.mark.asyncio
async def test_generate_slide_refuses_missing_product_file(tmp_path: Path):
    root = tmp_path.resolve()
    (root / "carousel").mkdir()
    (root / "carousel" / "prompts.json").write_text(
        json.dumps(
            {
                "aspectRatio": "4:5",
                "slides": [
                    {
                        "slideNumber": 1,
                        "role": "hook",
                        "prompt": "p" * 50,
                        "productImagePath": "artifacts/product/missing.jpg",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    out = await generate_slide(root, 1)
    assert out.startswith("ERROR:")
    assert "missing" in out.lower() or "productImagePath" in out


@pytest.mark.asyncio
async def test_generate_slide_happy_path_mocked(tmp_path: Path):
    root = tmp_path.resolve()
    (root / "carousel").mkdir()
    (root / "artifacts" / "product").mkdir(parents=True)
    prod = root / "artifacts" / "product" / "product_00.jpg"
    prod.write_bytes(b"x")
    (root / "carousel" / "prompts.json").write_text(
        json.dumps(
            {
                "aspectRatio": "4:5",
                "productImages": ["artifacts/product/product_00.jpg"],
                "slides": [
                    {
                        "slideNumber": 1,
                        "role": "hook",
                        "prompt": "cover " + ("y" * 40),
                        "productImagePath": "artifacts/product/product_00.jpg",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def fake_invoke(workspace_root, **kwargs):
        assert kwargs.get("aspect_ratio") == "4:5"
        assert kwargs.get("image_path")
        assert kwargs.get("model") == "nano-banana-pro"
        dest = root / "artifacts" / "carousel"
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / "slide_01.jpg"
        out.write_bytes(b"jpg")
        return json.dumps({"path": "artifacts/carousel/slide_01.jpg", "bytes": 3})

    with patch(
        "kageha.creative.carousel_studio._invoke_gemini_generate_image",
        new=fake_invoke,
    ):
        out = await generate_slide(root, 1)
    assert not out.startswith("ERROR:"), out
    data = json.loads(out)
    assert data["slideNumber"] == 1
    assert data["source"] == "generate_slide.py"
