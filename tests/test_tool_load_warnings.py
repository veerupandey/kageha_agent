"""Tool pack load failures are surfaced, not silent."""

from __future__ import annotations

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.builtin import load_entry_point_tools
from kageha.models.router import ModelRouter
from kageha.models.registry import ModelRegistry


def test_load_entry_point_tools_records_meta(tmp_path, monkeypatch):
    monkeypatch.delenv("KAGEHA_STRICT_TOOLS", raising=False)
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ws = SessionWorkspace.create("toolwarn")
    # Point workspace under tmp
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )
    reg = load_entry_point_tools(ctx)
    assert "bash" in reg.names() or "ask_human" in reg.names()
    assert "tool_load_warnings" in ctx.meta
    assert isinstance(ctx.meta["tool_load_warnings"], list)
