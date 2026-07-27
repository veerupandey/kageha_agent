"""Regression tests for pdf_extract / pdf_meta tools and pdf_ingest skill."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.builtin import load_entry_point_tools
from kageha.harness.tools.pdf import register_pdf_tools

pytest.importorskip("pypdf")

from pypdf import PdfWriter  # noqa: E402
from pypdf.generic import (  # noqa: E402
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = REPO_ROOT / "skills" / "pdf_ingest" / "SKILL.md"


def _ctx(tmp_path: Path) -> HarnessContext:
    root = tmp_path / "session"
    root.mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(exist_ok=True)
    ws = SessionWorkspace(run_id="test", root=root)
    return HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )


def _make_pdf(
    path: Path,
    pages: list[str],
    *,
    title: str = "Fixture Doc",
    author: str = "Regression",
) -> Path:
    """Write a multi-page PDF; empty strings become blank pages."""
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        if text:
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            content = f"BT /F1 24 Tf 72 720 Td ({escaped}) Tj ET"
            stream = DecodedStreamObject()
            stream.set_data(content.encode("latin-1", errors="replace"))
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            resources = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {NameObject("/F1"): writer._add_object(font)}
                    )
                }
            )
            page[NameObject("/Resources")] = resources
            page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": title, "/Author": author})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        writer.write(f)
    return path


def test_load_entry_point_registers_pdf_tools(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_TOOL_PACKS", "pdf")
    ctx = _ctx(tmp_path)
    names = set(load_entry_point_tools(ctx).names())
    assert "pdf_extract" in names
    assert "pdf_meta" in names


def test_pdf_meta_multi_page_fixture(tmp_path: Path):
    pdf = _make_pdf(
        tmp_path / "multi.pdf",
        ["Alpha page", "Beta page", ""],
        title="Multi Page",
        author="Suite",
    )
    ctx = _ctx(tmp_path)
    reg = register_pdf_tools(ctx)

    async def _run():
        out = await reg.get("pdf_meta").call(path=str(pdf))
        assert not out.startswith("ERROR:"), out
        data = json.loads(out)
        assert data["pages"] == 3
        assert data["title"] == "Multi Page"
        assert data["author"] == "Suite"
        assert data["engine"] == "pypdf"
        assert Path(data["source"]) == pdf.resolve()

    asyncio.run(_run())


def test_pdf_extract_writes_nonempty_and_handles_blank_pages(tmp_path: Path):
    pdf = _make_pdf(
        tmp_path / "mixed.pdf",
        ["Hello extractable text", "", "Trailing content"],
    )
    ctx = _ctx(tmp_path)
    reg = register_pdf_tools(ctx)

    async def _run():
        out = await reg.get("pdf_extract").call(path=str(pdf))
        assert not out.startswith("ERROR:"), out
        data = json.loads(out)
        assert data["extract_path"] == "pdf/extract.txt"
        extract = ctx.workspace.root / "pdf" / "extract.txt"
        assert extract.is_file()
        text = extract.read_text(encoding="utf-8")
        assert text.strip()
        assert "Hello extractable text" in text
        assert "Trailing content" in text
        assert "--- page 1 ---" in text
        assert "--- page 2 ---" in text
        assert "--- page 3 ---" in text
        assert data["bytes"] == extract.stat().st_size
        assert data["pages"] == 3

    asyncio.run(_run())


def test_pdf_path_absolute_and_workspace_relative(tmp_path: Path):
    ctx = _ctx(tmp_path)
    pdf_rel = "docs/sample.pdf"
    pdf = _make_pdf(ctx.workspace.root / pdf_rel, ["Workspace relative body"])
    outside = _make_pdf(tmp_path / "outside.pdf", ["Absolute path body"])
    reg = register_pdf_tools(ctx)

    async def _run():
        abs_out = await reg.get("pdf_extract").call(path=str(outside.resolve()))
        assert not abs_out.startswith("ERROR:"), abs_out
        abs_data = json.loads(abs_out)
        assert "Absolute path body" in abs_data["preview"]
        extract = ctx.workspace.root / "pdf" / "extract.txt"
        assert "Absolute path body" in extract.read_text(encoding="utf-8")

        rel_out = await reg.get("pdf_meta").call(path=pdf_rel)
        assert not rel_out.startswith("ERROR:"), rel_out
        rel_data = json.loads(rel_out)
        assert rel_data["pages"] == 1
        assert Path(rel_data["source"]) == pdf.resolve()

        rel_extract = await reg.get("pdf_extract").call(path=pdf_rel)
        assert not rel_extract.startswith("ERROR:"), rel_extract
        assert "Workspace relative body" in extract.read_text(encoding="utf-8")

    asyncio.run(_run())


def test_pdf_missing_file_returns_error(tmp_path: Path):
    ctx = _ctx(tmp_path)
    reg = register_pdf_tools(ctx)

    async def _run():
        missing_abs = str(tmp_path / "nope.pdf")
        out = await reg.get("pdf_extract").call(path=missing_abs)
        assert out.startswith("ERROR:")
        assert "not found" in out.lower()

        meta = await reg.get("pdf_meta").call(path="missing/relative.pdf")
        assert meta.startswith("ERROR:")
        assert "not found" in meta.lower()

    asyncio.run(_run())


def test_pdf_extract_max_pages_limits(tmp_path: Path):
    pdf = _make_pdf(
        tmp_path / "long.pdf",
        ["PageOneMarker", "PageTwoMarker", "PageThreeMarker", "PageFourMarker"],
    )
    ctx = _ctx(tmp_path)
    reg = register_pdf_tools(ctx)

    async def _run():
        out = await reg.get("pdf_extract").call(path=str(pdf), max_pages=2)
        assert not out.startswith("ERROR:"), out
        data = json.loads(out)
        # metadata reports total pages; extract body is limited
        assert data["pages"] == 4
        text = (ctx.workspace.root / "pdf" / "extract.txt").read_text(encoding="utf-8")
        assert "PageOneMarker" in text
        assert "PageTwoMarker" in text
        assert "PageThreeMarker" not in text
        assert "PageFourMarker" not in text
        assert "--- page 1 ---" in text
        assert "--- page 2 ---" in text
        assert "--- page 3 ---" not in text

    asyncio.run(_run())


def test_pdf_ingest_skill_mentions_pdf_extract():
    assert SKILL_PATH.is_file(), f"missing skill: {SKILL_PATH}"
    body = SKILL_PATH.read_text(encoding="utf-8")
    assert "pdf_extract" in body
    assert "pdf_meta" in body
    # Skill must steer toward first-class tools, not forged ones
    assert "forge" in body.lower()
    assert "Do **not** forge" in body or "do not forge" in body.lower()
