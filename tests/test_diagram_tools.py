"""Diagram kind detection + render tool (mocked network)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.diagram import (
    _guess_kind,
    register_diagram_tools,
)
from kageha.models.registry import ModelRegistry
from kageha.models.router import ModelRouter
from kageha.memory.skills import SkillRegistry


def test_guess_kind_mermaid_and_excalidraw():
    assert _guess_kind("flowchart TD\n  A-->B") == "mermaid"
    assert _guess_kind("sequenceDiagram\n  Alice->>Bob: hi") == "mermaid"
    assert (
        _guess_kind('{"type":"excalidraw","elements":[]}')
        == "excalidraw"
    )
    assert _guess_kind("@startuml\nAlice -> Bob\n@enduml") == "plantuml"
    assert _guess_kind("digraph G { a -> b }") == "graphviz"


def test_choose_diagram_mode_structured_vs_image(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ws = SessionWorkspace.create("d1")
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )
    tool = register_diagram_tools(ctx).get("choose_diagram_mode")
    assert tool is not None
    out = asyncio.run(tool.call(task="draw a system architecture flowchart"))
    import json

    data = json.loads(out)
    assert data["mode"] == "structured"
    assert data["kind"] in {"mermaid", "excalidraw", "plantuml", "graphviz"}

    art = json.loads(
        asyncio.run(tool.call(task="watercolor artistic illustration poster mood"))
    )
    assert art["mode"] == "image_model"


def test_render_diagram_writes_png(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ws = SessionWorkspace.create("d2")
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )
    tool = register_diagram_tools(ctx).get("render_diagram")
    assert tool is not None

    fake_png = b"\x89PNG\r\n\x1a\n" + b"fake"

    with patch(
        "kageha.harness.tools.diagram._render_kroki",
        new=AsyncMock(return_value=fake_png),
    ):
        out = asyncio.run(
            tool.call(
                source="flowchart TD\n  A[Start] --> B[End]",
                kind="mermaid",
                format="png",
                filename="diagrams/flow.png",
            )
        )
    import json

    data = json.loads(out)
    assert data["ok"] is True
    assert (ws.root / "diagrams" / "flow.png").read_bytes().startswith(b"\x89PNG")
    assert (ws.root / "diagrams" / "flow.mmd").is_file()


def test_make_diagram_skill_registered():
    reg = SkillRegistry()
    skill = reg.get("make_diagram")
    assert skill is not None
    assert "mermaid" in skill.description.lower() or "diagram" in skill.description.lower()
