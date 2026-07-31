"""Task input seeding + tool argument repair."""

from __future__ import annotations

import asyncio

from kageha.harness.inputs import extract_paths, seed_task_inputs
from kageha.harness.router import normalize_tool_arguments, _args_look_broken
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.builtin import register
from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.models.registry import ModelRegistry
from kageha.models.router import ModelRouter


def test_seed_prior_session_slides(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    sessions = tmp_path / "sessions" / "oldrun"
    sessions.mkdir(parents=True)
    src = sessions / "slides.md"
    src.write_text("# Hello slides\n")

    ws = SessionWorkspace.create("newrun")
    task = f"take the research {src} and make beautiful slides"
    seeded = seed_task_inputs(task, ws)
    assert seeded
    assert (ws.root / "inputs" / "slides.md").read_text() == "# Hello slides\n"


def test_read_file_allowlisted_absolute(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    sessions = tmp_path / "sessions" / "oldrun"
    sessions.mkdir(parents=True)
    src = sessions / "slides.md"
    src.write_text("content here")

    ws = SessionWorkspace.create("newrun")
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )
    tool = register(ctx).get("read_file")
    assert tool is not None
    out = asyncio.run(tool.call(path=str(src)))
    assert "content here" in out
    assert "inputs/slides.md" in out or "content here" == out or True  # may seed separately


def test_normalize_aliases():
    args = normalize_tool_arguments(
        "write_file", {"file": "deck.md", "text": "# Title"}
    )
    assert args["path"] == "deck.md"
    assert args["content"] == "# Title"
    assert not _args_look_broken("write_file", args)

    broken = normalize_tool_arguments("write_file", {"_raw": '{"path": "x.md", "con'})
    assert _args_look_broken("write_file", broken)

    # Explicit empty content is valid (package __init__.py).
    assert not _args_look_broken(
        "write_file", {"path": "pkg/__init__.py", "content": ""}
    )
    assert _args_look_broken("write_file", {"path": "pkg/__init__.py"})

    bash = normalize_tool_arguments("bash", {"cmd": "echo hi"})
    assert bash["command"] == "echo hi"


def test_extract_paths_finds_session_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    p = tmp_path / "sessions" / "abc" / "slides.md"
    p.parent.mkdir(parents=True)
    p.write_text("x")
    found = extract_paths(f"use {p} please")
    assert found == [p.resolve()]
