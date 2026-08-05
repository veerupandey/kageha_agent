from pathlib import Path
from types import SimpleNamespace

from kageha.harness.approvals import ApprovalGate
from kageha.harness.dynamic_tools import (
    register_tool_search,
    search_specs,
    select_tool_specs,
)
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.base import Tool, ToolRegistry
from kageha.models.base import ToolSpec


def _ctx(tmp_path: Path) -> HarnessContext:
    root = tmp_path / "session"
    root.mkdir()
    return HarnessContext(
        workspace=SessionWorkspace(run_id="dynamic", root=root),
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )


def _spec(name: str, description: str = "") -> ToolSpec:
    return ToolSpec(name=name, description=description or name, parameters={})


def test_browser_task_selects_browser_not_unrelated_tools(tmp_path: Path):
    ctx = _ctx(tmp_path)
    specs = [_spec("tool_search"), _spec("read_file")]
    specs += [_spec(f"browser_{name}") for name in ("open", "click", "snapshot")]
    specs += [_spec(f"memory_{i}") for i in range(20)]
    selected = select_tool_specs(ctx, specs, task="Open the website and click login")
    names = {spec.name for spec in selected}
    assert {"tool_search", "browser_open", "browser_click", "browser_snapshot"} <= names
    assert not any(name.startswith("memory_") for name in names)
    assert len(selected) <= 18


def test_tool_search_activates_matches_for_next_step(tmp_path: Path):
    import asyncio

    ctx = _ctx(tmp_path)
    reg = ToolRegistry()
    reg.register(Tool("calendar_create", "Create a calendar event", {}, lambda: "ok"))
    reg.register(Tool("image_create", "Generate an image", {}, lambda: "ok"))
    register_tool_search(ctx, reg)

    result = asyncio.run(reg.get("tool_search").call(query="calendar event"))
    assert "calendar_create" in result
    assert "calendar_create" in ctx.meta["dynamic_tool_names"]


def test_search_specs_is_bounded_and_relevant():
    specs = [_spec("alpha_search", "Search alpha records"), _spec("beta_write")]
    assert [s.name for s in search_specs(specs, "alpha", limit=1)] == ["alpha_search"]


def test_automatic_selection_ignores_weak_generic_matches(tmp_path: Path):
    ctx = _ctx(tmp_path)
    specs = [
        _spec("tool_search"),
        _spec("read_file"),
        _spec("calendar_create", "Use this to create calendar events"),
    ]
    specs.extend(_spec(f"unrelated_{i}", "Specialized operation") for i in range(20))
    selected = select_tool_specs(ctx, specs, task="use the parser")
    assert "calendar_create" not in {spec.name for spec in selected}
