"""Core vs optional tool-pack gating (lean kernel)."""

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


def test_optional_packs_are_browser_and_computer_only():
    assert OPTIONAL_PACK_NAMES == frozenset({"browser", "computer"})
    assert "media" not in OPTIONAL_PACK_NAMES
    assert "kb" not in OPTIONAL_PACK_NAMES
    assert "pdf" not in OPTIONAL_PACK_NAMES


def test_resolve_default_core_only(monkeypatch, tmp_path):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("KAGEHA_TOOL_PACKS", raising=False)
    monkeypatch.setenv("KAGEHA_BROWSER_PACK", "0")
    monkeypatch.setenv("KAGEHA_COMPUTER", "0")
    enabled = resolve_enabled_packs(policy={})
    assert set(enabled) == CORE_PACK_NAMES
    assert "browser" not in enabled
    assert "computer" not in enabled


def test_resolve_env_all(monkeypatch):
    monkeypatch.setenv("KAGEHA_TOOL_PACKS", "all")
    monkeypatch.setenv("KAGEHA_COMPUTER", "0")
    enabled = resolve_enabled_packs(policy={"packs": ["browser"]})
    assert CORE_PACK_NAMES <= set(enabled)
    assert "browser" in enabled
    assert "computer" not in enabled  # force-disabled


def test_resolve_env_beats_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KAGEHA_TOOL_PACKS", "computer")
    monkeypatch.setenv("KAGEHA_BROWSER_PACK", "0")
    monkeypatch.delenv("KAGEHA_COMPUTER", raising=False)
    enabled = resolve_enabled_packs(policy={"packs": ["browser"]})
    assert "computer" in enabled
    assert "browser" not in enabled


def test_resolve_yaml_packs(monkeypatch):
    monkeypatch.delenv("KAGEHA_TOOL_PACKS", raising=False)
    monkeypatch.setenv("KAGEHA_COMPUTER", "0")
    enabled = resolve_enabled_packs(policy={"packs": ["browser", "kb", "media"]})
    assert "browser" in enabled
    # Unknown / removed packs are ignored.
    assert "kb" not in enabled
    assert "media" not in enabled


def test_driver_presence_does_not_auto_enable_computer(monkeypatch):
    monkeypatch.delenv("KAGEHA_TOOL_PACKS", raising=False)
    monkeypatch.delenv("KAGEHA_COMPUTER", raising=False)
    monkeypatch.setenv("KAGEHA_BROWSER_PACK", "0")
    monkeypatch.setattr(
        "kageha.harness.tool_packs.platform.system", lambda: "Darwin"
    )
    monkeypatch.setattr(
        "kageha.harness.tools.computer_driver.driver_available", lambda: True
    )
    enabled = resolve_enabled_packs(policy={})
    assert "computer" not in enabled


def test_opt_out_computer(monkeypatch):
    monkeypatch.setenv("KAGEHA_TOOL_PACKS", "browser,-computer")
    enabled = resolve_enabled_packs(policy={})
    assert "browser" in enabled
    assert "computer" not in enabled


def test_default_load_excludes_optional(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("KAGEHA_TOOL_PACKS", raising=False)
    monkeypatch.setenv("KAGEHA_BROWSER_PACK", "0")
    monkeypatch.setenv("KAGEHA_COMPUTER", "0")
    ctx = _ctx(tmp_path)
    reg = load_entry_point_tools(ctx)
    names = set(reg.names())
    assert "bash" in names
    assert "skill_list" in names
    assert "memory_recall" in names
    assert "spawn_subagent" in names
    assert "mcp_list_servers" in names
    assert "browser_open" not in names
    assert ctx.meta.get("tool_packs_enabled")
    assert "browser" not in (ctx.meta.get("tool_packs_enabled") or [])
    assert summarize_packs(ctx.meta["tool_packs_enabled"])


def test_opt_in_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KAGEHA_TOOL_PACKS", "browser")
    monkeypatch.setenv("KAGEHA_COMPUTER", "0")
    ctx = _ctx(tmp_path)
    reg = load_entry_point_tools(ctx)
    names = set(reg.names())
    assert "browser_open" in names
