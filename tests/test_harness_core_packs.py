"""Core pack surface acceptance."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kageha.chat.turn_manager import TurnDecision, prefer_agent_mode, prefer_loop_mode
from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tool_packs import CORE_PACK_NAMES, OPTIONAL_PACK_NAMES
from kageha.harness.tools.builtin import load_entry_point_tools
from kageha.loop.mode_policy import loop_mode_for, mode_system_extra
from kageha.memory.skills import SkillRegistry
from kageha.models.doctor import run_models_doctor


def _ctx(tmp_path: Path) -> HarnessContext:
    root = tmp_path / "session"
    root.mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(exist_ok=True)
    return HarnessContext(
        workspace=SessionWorkspace(run_id="accept", root=root),
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )


def test_default_core_tools_only(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("KAGEHA_TOOL_PACKS", raising=False)
    monkeypatch.setenv("KAGEHA_BROWSER_PACK", "0")
    monkeypatch.setenv("KAGEHA_COMPUTER", "0")
    ctx = _ctx(tmp_path)
    reg = load_entry_point_tools(ctx)
    names = set(reg.names())
    # Core capabilities present
    for need in (
        "bash",
        "read_file",
        "web_search",
        "web_fetch",
        "research_run",
        "parallel_web_fetch",
        "skill_list",
        "mcp_list_servers",
        "memory_recall",
        "spawn_subagent",
        "spawn_task_graph",
        "forge_tool",
        "escalate_plan",
        "request_approval",
        "ask_human",
    ):
        assert need in names, need
    # Optional packs absent
    for absent in (
        "browser_open",
        "pdf_extract",
        "gemini_generate_image",
        "kb_search",
        "computer_click",
        "bravia_status",
    ):
        assert absent not in names, absent
    enabled = set(ctx.meta.get("tool_packs_enabled") or [])
    assert enabled == CORE_PACK_NAMES
    assert not (enabled & OPTIONAL_PACK_NAMES)
    assert int(ctx.meta.get("tool_count") or 0) == len(names)


def test_modes_and_prompts():
    decision = TurnDecision(
        intent="new_task",
        related_to_current_task=False,
        requires_tools=True,
        discard_old_plan=True,
        reason="t",
    )
    assert prefer_agent_mode("hello") == "normal"
    assert prefer_loop_mode("hello", decision, route="first_run") == "followup"
    assert prefer_agent_mode("/spec build X") == "normal"
    assert loop_mode_for("goal") == "full"
    plan_prompt = mode_system_extra("plan").lower()
    assert "plan.md" in plan_prompt or "design" in plan_prompt
    assert "build" in plan_prompt


def test_core_skills_present():
    skills = SkillRegistry()
    for name in (
        "getting_started",
        "computer_use",
        "web_browse",
        "memory",
        "web_research",
    ):
        assert name in skills.skills, name


def test_doctor_reports_tool_packs():
    report = run_models_doctor(smoke=False)
    names = {c.name for c in report.checks}
    assert "tool_packs" in names
    assert "tools_policy" in names
    pack = next(c for c in report.checks if c.name == "tool_packs")
    assert "core=" in pack.detail
    assert "optional=" in pack.detail


def test_core_pack_set_frozen():
    """Soft CI budget: default core packs must not silently grow."""
    assert CORE_PACK_NAMES == frozenset(
        {"forge", "skills", "mcp", "memory", "subagent", "research"}
    )
    # Device control must never return as optional harness packs.
    assert "bravia" not in OPTIONAL_PACK_NAMES
    assert "android_tv" not in OPTIONAL_PACK_NAMES
    assert "network_scan" not in OPTIONAL_PACK_NAMES


def test_no_harness_device_or_carousel_modules():
    from pathlib import Path

    tools = Path(__file__).resolve().parents[1] / "src" / "kageha" / "harness" / "tools"
    for gone in (
        "bravia.py",
        "android_tv.py",
        "network_scan.py",
        "carousel_studio.py",
        "pdf.py",
        "media.py",
        "diagram.py",
        "product_import.py",
        "connections_tools.py",
    ):
        assert not (tools / gone).exists(), gone
