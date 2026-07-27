"""Core vs optional tool-pack gating."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tool_packs import (
    CORE_PACK_NAMES,
    OPTIONAL_PACK_NAMES,
    resolve_enabled_packs,
    summarize_packs,
)
from kageha.harness.tools.builtin import load_entry_point_tools


def _ctx(tmp_path: Path) -> HarnessContext:
    root = tmp_path / "session"
    root.mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(exist_ok=True)
    return HarnessContext(
        workspace=SessionWorkspace(run_id="packs", root=root),
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )


def test_resolve_default_core_only(monkeypatch, tmp_path):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("KAGEHA_TOOL_PACKS", raising=False)
    monkeypatch.setenv("KAGEHA_BROWSER_PACK", "0")
    monkeypatch.setenv("KAGEHA_COMPUTER", "0")
    monkeypatch.setattr(
        "kageha.harness.tools.computer_driver.driver_available", lambda: False
    )
    enabled = resolve_enabled_packs(policy={})
    assert set(enabled) == CORE_PACK_NAMES
    assert "browser" not in enabled
    assert "computer" not in enabled


def test_resolve_env_all(monkeypatch):
    monkeypatch.setenv("KAGEHA_TOOL_PACKS", "all")
    enabled = resolve_enabled_packs(policy={"packs": ["browser"]})
    assert CORE_PACK_NAMES <= set(enabled)
    assert OPTIONAL_PACK_NAMES <= set(enabled)


def test_resolve_env_beats_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KAGEHA_TOOL_PACKS", "media")
    monkeypatch.setenv("KAGEHA_BROWSER_PACK", "0")
    monkeypatch.setenv("KAGEHA_COMPUTER", "0")
    monkeypatch.setattr(
        "kageha.harness.tools.computer_driver.driver_available", lambda: False
    )
    enabled = resolve_enabled_packs(policy={"packs": ["browser", "pdf"]})
    assert "media" in enabled
    assert "browser" not in enabled


def test_resolve_yaml_packs(monkeypatch):
    monkeypatch.delenv("KAGEHA_TOOL_PACKS", raising=False)
    monkeypatch.setenv("KAGEHA_COMPUTER", "0")
    monkeypatch.setattr(
        "kageha.harness.tools.computer_driver.driver_available", lambda: False
    )
    enabled = resolve_enabled_packs(policy={"packs": ["browser", "kb"]})
    assert "browser" in enabled
    assert "kb" in enabled
    assert "media" not in enabled


def test_auto_enable_computer_when_driver_present(monkeypatch):
    monkeypatch.delenv("KAGEHA_TOOL_PACKS", raising=False)
    monkeypatch.delenv("KAGEHA_COMPUTER", raising=False)
    monkeypatch.setattr(
        "kageha.harness.tool_packs.platform.system", lambda: "Darwin"
    )
    monkeypatch.setattr(
        "kageha.harness.tools.computer_driver.driver_available", lambda: True
    )
    enabled = resolve_enabled_packs(policy={})
    assert "computer" in enabled


def test_opt_out_computer_auto_enable(monkeypatch):
    monkeypatch.setenv("KAGEHA_TOOL_PACKS", "browser,-computer")
    monkeypatch.setattr(
        "kageha.harness.tool_packs.platform.system", lambda: "Darwin"
    )
    monkeypatch.setattr(
        "kageha.harness.tools.computer_driver.driver_available", lambda: True
    )
    enabled = resolve_enabled_packs(policy={})
    assert "browser" in enabled
    assert "computer" not in enabled


def test_default_load_excludes_optional(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("KAGEHA_TOOL_PACKS", raising=False)
    monkeypatch.setenv("KAGEHA_BROWSER_PACK", "0")
    monkeypatch.setenv("KAGEHA_COMPUTER", "0")
    monkeypatch.setattr(
        "kageha.harness.tools.computer_driver.driver_available", lambda: False
    )
    ctx = _ctx(tmp_path)
    reg = load_entry_point_tools(ctx)
    names = set(reg.names())
    assert "bash" in names
    assert "skill_list" in names
    assert "memory_recall" in names
    assert "spawn_subagent" in names
    assert "mcp_list_servers" in names
    assert "browser_open" not in names
    assert "pdf_extract" not in names
    assert "gemini_generate_image" not in names
    assert "kb_search" not in names
    assert ctx.meta.get("tool_packs_enabled")
    assert "browser" not in (ctx.meta.get("tool_packs_enabled") or [])
    assert summarize_packs(ctx.meta["tool_packs_enabled"])


def test_opt_in_media_and_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KAGEHA_TOOL_PACKS", "media,browser")
    ctx = _ctx(tmp_path)
    reg = load_entry_point_tools(ctx)
    names = set(reg.names())
    assert "gemini_generate_image" in names
    assert "browser_open" in names
    assert "pdf_extract" not in names
