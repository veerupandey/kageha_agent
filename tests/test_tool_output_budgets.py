"""Tool output budgets: read_file / list_dir / router envelope."""

from __future__ import annotations

import asyncio
from pathlib import Path

from kageha.harness.approvals import ApprovalGate
from kageha.harness.router import (
    _effective_output_limit,
    execute_tool_calls,
    truncate_tool_output,
)
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.base import Tool, ToolRegistry
from kageha.harness.tools.builtin import register
from kageha.models.base import ToolCall
from kageha.models.registry import ModelRegistry
from kageha.models.router import ModelRouter


def _ctx(tmp_path, monkeypatch) -> tuple[HarnessContext, Path]:
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    ws = SessionWorkspace.create("budget")
    project = tmp_path / "proj"
    project.mkdir()
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
        project_root=str(project),
    )
    return ctx, project


def test_read_file_default_line_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_READ_FILE_LINE_LIMIT", "5")
    ctx, project = _ctx(tmp_path, monkeypatch)
    (project / "big.py").write_text("\n".join(f"line{i}" for i in range(1, 21)) + "\n")
    tool = register(ctx).get("read_file")
    assert tool is not None
    out = asyncio.run(tool.call(path="big.py"))
    assert "line1" in out
    assert "line5" in out
    assert "line6" not in out.split("...[")[0]
    assert "lines 1-5 of 20" in out
    assert "offset=6" in out


def test_read_file_offset_limit(tmp_path, monkeypatch):
    ctx, project = _ctx(tmp_path, monkeypatch)
    (project / "n.py").write_text("\n".join(f"L{i}" for i in range(1, 11)) + "\n")
    tool = register(ctx).get("read_file")
    assert tool is not None
    out = asyncio.run(tool.call(path="n.py", offset=4, limit=3))
    head = out.split("...[")[0]
    assert "L4" in head and "L6" in head
    assert "L3" not in head
    assert "L7" not in head
    assert "lines 4-6 of 10" in out
    assert "offset=7" in out


def test_list_dir_shallow_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_LIST_DIR_MAX", "50")
    ctx, project = _ctx(tmp_path, monkeypatch)
    (project / "a.txt").write_text("a")
    sub = project / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("n")
    tool = register(ctx).get("list_dir")
    assert tool is not None
    out = asyncio.run(tool.call(path="."))
    assert "a.txt" in out
    assert "sub/" in out
    assert "nested.txt" not in out

    deep = asyncio.run(tool.call(path=".", recursive=True))
    assert "sub/nested.txt" in deep


def test_list_dir_glob_and_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_LIST_DIR_MAX", "3")
    ctx, project = _ctx(tmp_path, monkeypatch)
    for i in range(6):
        (project / f"f{i}.py").write_text("x")
        (project / f"f{i}.txt").write_text("x")
    tool = register(ctx).get("list_dir")
    assert tool is not None
    out = asyncio.run(tool.call(path=".", glob="*.py"))
    assert ".txt" not in out
    assert "truncated at 3" in out


def test_truncate_tool_output_head_tail():
    body = "A" * 200 + "MID" + "B" * 200
    out = truncate_tool_output(body, 120)
    assert out.startswith("A")
    assert out.endswith("B")
    assert "truncated" in out
    assert "head+tail" in out
    assert len(out) == 120
    assert "MID" not in out


def test_effective_output_limit_computer_override(monkeypatch):
    monkeypatch.setenv("KAGEHA_TOOL_OUTPUT_LIMIT", "1000")
    monkeypatch.setenv("KAGEHA_COMPUTER_TOOL_OUTPUT_LIMIT", "50000")
    assert _effective_output_limit("read_file", None) == 1000
    assert _effective_output_limit("computer_get_state", None) == 50000
    assert _effective_output_limit("computer_click", 2000) == 50000


def test_router_applies_output_limit(monkeypatch):
    monkeypatch.setenv("KAGEHA_TOOL_OUTPUT_LIMIT", "120")
    reg = ToolRegistry()

    async def bulky() -> str:
        return "X" * 500

    reg.register(
        Tool(
            name="bulky_tool",
            description="bulky",
            parameters={"type": "object", "properties": {}},
            handler=bulky,
        )
    )
    msgs = asyncio.run(
        execute_tool_calls(
            reg,
            [ToolCall(id="1", name="bulky_tool", arguments={})],
        )
    )
    content = msgs[0].content or ""
    assert "truncated" in content
    assert len(content) == 120
