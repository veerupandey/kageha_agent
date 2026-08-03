"""Design panel save API + plan.md re-read on Build approve."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kageha.app_server import AppServer
from kageha.harness.sandbox import SessionWorkspace
from kageha.loop.controller import LoopController
from kageha.loop.mode_policy import (
    apply_saved_plan_markdown,
    mark_plan_approved,
    parse_plan_markdown_steps,
    render_plan_markdown,
)
from kageha.loop.planner import PlanStep, TaskPlan
from kageha.memory.service import reset_memory_service_for_tests
from kageha.models.base import ChatMessage, ChatResponse, ChatUsage
from kageha.webui.server import WebUIApp


@pytest.fixture()
def webui_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WebUIApp:
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    monkeypatch.setenv("KAGEHA_MEMORY_EMBEDDINGS", "off")
    reset_memory_service_for_tests()
    app = WebUIApp(AppServer())
    yield app
    app.close()
    reset_memory_service_for_tests()


def _call(
    app: WebUIApp,
    method: str,
    path: str,
    *,
    body: dict | None = None,
) -> tuple[int, dict]:
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    status, data, ctype = app.handle(method, path, {}, raw, None)
    assert "json" in ctype
    return status, json.loads(data.decode("utf-8"))


def test_parse_plan_markdown_steps_roundtrip():
    text = render_plan_markdown(
        "plan",
        summary="Ship hello",
        steps=[
            PlanStep(id="s1", description="Write hello.py", tools=["write_file"]),
            PlanStep(id="s2", description="Verify output", tools=["bash"]),
        ],
        task="create hello",
        tldr="Write and verify hello.py",
    )
    steps = parse_plan_markdown_steps(text)
    assert steps == [
        ("s1", "Write hello.py"),
        ("s2", "Verify output"),
    ]


def test_apply_saved_plan_markdown_keeps_tools_for_known_ids():
    plan = TaskPlan(
        summary="old",
        steps=[
            PlanStep(id="s1", description="old desc", tools=["write_file"]),
        ],
        source="template",
    )
    text = """# Plan (plan)

**TL;DR:** Edited brief

## Steps

- [ ] `s1`: New description from UI
- [ ] `extra`: Brand new step

"""
    updated = apply_saved_plan_markdown(plan, text)
    assert updated.summary == "Edited brief"
    assert [s.id for s in updated.steps] == ["s1", "extra"]
    assert updated.steps[0].description == "New description from UI"
    assert updated.steps[0].tools == ["write_file"]
    assert updated.steps[1].tools == []
    assert updated.source.endswith("+disk")


def test_design_put_saves_plan_md(webui_app: WebUIApp):
    sid = "design-save-1"
    ws = SessionWorkspace.create(sid)
    (ws.root / "plan.md").write_text("# Plan (plan)\n\nold\n", encoding="utf-8")

    status, payload = _call(
        webui_app,
        "PUT",
        f"/api/sessions/{sid}/design",
        body={"file": "plan.md", "content": "# Plan (plan)\n\nedited from UI\n"},
    )
    assert status == 200
    assert payload["saved"] == ["plan.md"]
    assert "edited from UI" in payload["files"]["plan.md"]
    assert (ws.root / "plan.md").read_text(encoding="utf-8") == (
        "# Plan (plan)\n\nedited from UI\n"
    )

    status2, again = _call(webui_app, "GET", f"/api/sessions/{sid}/design")
    assert status2 == 200
    assert "edited from UI" in again["files"]["plan.md"]
    assert again["awaiting_build"] is True


def test_design_put_allows_explore_notes(webui_app: WebUIApp):
    sid = "design-save-explore"
    ws = SessionWorkspace.create(sid)
    (ws.root / "plan.md").write_text("# Plan (plan)\n\nold\n", encoding="utf-8")
    (ws.root / "explore_notes.md").write_text("# Explore\n\nold\n", encoding="utf-8")

    status, payload = _call(
        webui_app,
        "PATCH",
        f"/api/sessions/{sid}/design",
        body={
            "files": {
                "plan.md": "# Plan (plan)\n\nnew plan\n",
                "explore_notes.md": "# Explore\n\nnew notes\n",
            }
        },
    )
    assert status == 200
    assert set(payload["saved"]) == {"plan.md", "explore_notes.md"}
    assert "new plan" in (ws.root / "plan.md").read_text(encoding="utf-8")
    assert "new notes" in (ws.root / "explore_notes.md").read_text(encoding="utf-8")


def test_design_put_rejects_non_allowlisted(webui_app: WebUIApp):
    sid = "design-save-deny-file"
    SessionWorkspace.create(sid)
    status, payload = _call(
        webui_app,
        "PUT",
        f"/api/sessions/{sid}/design",
        body={"file": "requirements.md", "content": "nope"},
    )
    assert status == 400
    assert "not editable" in payload["error"]


def test_design_put_locked_after_build(webui_app: WebUIApp):
    sid = "design-save-locked"
    ws = SessionWorkspace.create(sid)
    (ws.root / "plan.md").write_text("# Plan\n\nx\n", encoding="utf-8")
    mark_plan_approved(ws.root)
    status, payload = _call(
        webui_app,
        "PUT",
        f"/api/sessions/{sid}/design",
        body={"file": "plan.md", "content": "should fail"},
    )
    assert status == 400
    assert "locked" in payload["error"]
    assert (ws.root / "plan.md").read_text(encoding="utf-8") == "# Plan\n\nx\n"


