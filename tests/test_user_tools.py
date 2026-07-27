"""Custom user tool directory discovery."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.builtin import load_entry_point_tools
from kageha.harness.tools.user_tools import load_user_tool_dirs


def _ctx(tmp_path: Path) -> HarnessContext:
    root = tmp_path / "session"
    root.mkdir(parents=True, exist_ok=True)
    ws = SessionWorkspace(run_id="test", root=root)
    return HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )


def test_load_user_tool_module(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    tools = home / "tools"
    tools.mkdir(parents=True)
    monkeypatch.setenv("KAGEHA_HOME", str(home))

    (tools / "demo_pack.py").write_text(
        """
from kageha.harness.tools.base import ToolRegistry, tool

def register(ctx):
    reg = ToolRegistry()
    @tool(description="Demo echo tool")
    async def demo_echo(text: str) -> str:
        return f"echo:{text}"
    reg.register(demo_echo)
    return reg
"""
    )

    ctx = _ctx(tmp_path)
    loaded = load_user_tool_dirs(ctx)
    assert any("demo_pack" in label for label, _ in loaded)
    names = {n for _, reg in loaded for n in reg.names()}
    assert "demo_echo" in names

    # Also via full entry-point loader
    full = load_entry_point_tools(ctx)
    assert "demo_echo" in full.names()


def test_bundled_skills_dir_is_package_local():
    from kageha.config import bundled_skills_dir

    d = bundled_skills_dir()
    assert d.name == "bundled_skills"
    assert (d / "getting_started" / "SKILL.md").is_file()
    assert (d / "web_research" / "SKILL.md").is_file()
    assert (d / "computer_use" / "SKILL.md").is_file()
    assert (d / "web_browse" / "SKILL.md").is_file()
    assert (d / "memory" / "SKILL.md").is_file()
