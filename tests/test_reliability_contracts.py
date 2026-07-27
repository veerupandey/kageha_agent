"""Contracts that keep long-running agent work honest and recoverable."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from kageha.eval.harness import GoldenTask, run_golden
from kageha.harness.sandbox import SessionWorkspace
from kageha.loop.controller import RunResult
from kageha.loop.goal_card import GoalCard
from kageha.loop.planner import make_plan
from kageha.loop.verifier import build_workspace_evidence
from kageha.models.base import ChatMessage, ChatResponse, ToolCall
from kageha.models.router import sanitize_messages_for_provider


def test_planner_filters_invented_tools():
    router = MagicMock()
    router.chat = AsyncMock(
        return_value=(
            MagicMock(),
            ChatResponse(
                message=ChatMessage(
                    role="assistant",
                    content=(
                        '{"summary":"make it","milestones":["deck exists"],'
                        '"steps":[{"id":"1","description":"build",'
                        '"tools":["write_file","imaginary_renderer"]}]}'
                    ),
                )
            ),
        )
    )
    plan = asyncio.run(
        make_plan(
            "make a deck",
            router,
            available_tools={"write_file", "bash"},
        )
    )
    assert plan.steps[0].tools == ["write_file"]
    prompt = router.chat.await_args.args[0][0].content
    assert "write_file" in prompt
    assert "ONLY these exact names" in prompt


def test_workspace_export_preserves_paths_and_hides_control_files(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    workspace = SessionWorkspace.create("export")
    workspace.write_text("outputs/report/brief.md", "done")
    workspace.write_text("research/notes.md", "evidence")
    workspace.write_text("events.jsonl", "{}")
    destination = tmp_path / "project"

    copied = workspace.export_to(destination)

    assert "outputs/report/brief.md" in copied
    assert (destination / "outputs/report/brief.md").read_text() == "done"
    assert (destination / "research/notes.md").read_text() == "evidence"
    assert not (destination / "events.jsonl").exists()


def test_workspace_evidence_includes_content_and_image_dimensions(tmp_path):
    from PIL import Image

    (tmp_path / "brief.md").write_text("# Finding\nThe intervention improved engagement.")
    Image.new("RGB", (1080, 1080), "white").save(tmp_path / "slide.png")

    evidence = build_workspace_evidence(tmp_path)

    assert "The intervention improved engagement" in evidence
    assert "image=1080x1080" in evidence


def test_same_provider_model_switch_can_force_tool_history_normalization():
    messages = [
        ChatMessage(
            role="assistant",
            tool_calls=[ToolCall(id="1", name="pdf_extract", arguments={"path": "paper.pdf"})],
        ),
        ChatMessage(role="tool", tool_call_id="1", name="pdf_extract", content="14 pages"),
    ]

    normalized = sanitize_messages_for_provider(
        messages,
        target_provider="gemini",
        source_provider="gemini",
        force=True,
    )

    assert not any(message.tool_calls for message in normalized)
    assert all(message.role != "tool" for message in normalized)
    assert any("14 pages" in message.content for message in normalized)


def test_golden_eval_rejects_incomplete_status_even_when_legacy_suite_allows_it(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    workspace = SessionWorkspace.create("incomplete")
    workspace.write_text("hello.txt", "hello from kageha")
    result = RunResult(
        run_id=workspace.run_id,
        status="max_steps",
        message="not finished",
        goal=GoalCard.from_task("write hello"),
        steps=12,
        spent_usd=0.1,
        artifacts=["hello.txt"],
    )
    controller = MagicMock()
    controller.run = AsyncMock(return_value=result)
    task = GoldenTask(
        id="honest",
        prompt="write hello",
        expect_status=["success", "max_steps"],
        expect_files=["hello.txt"],
        expect_file_contains={"hello.txt": "hello from kageha"},
    )

    evaluated = asyncio.run(run_golden(task, controller_factory=lambda: controller))

    assert evaluated.passed is False
    assert any("required success" in reason for reason in evaluated.reasons)


def test_golden_eval_validates_artifact_shape(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    workspace = SessionWorkspace.create("visual")
    for index in range(1, 4):
        path = workspace.path(f"carousel/slide_{index:02d}.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1080, 1080), "white").save(path)
    result = RunResult(
        run_id=workspace.run_id,
        status="success",
        message="finished",
        goal=GoalCard.from_task("carousel"),
        steps=4,
        spent_usd=0.1,
        artifacts=workspace.list_files(),
    )
    controller = MagicMock()
    controller.run = AsyncMock(return_value=result)
    task = GoldenTask(
        id="visual",
        prompt="make carousel",
        expect_files=["carousel/slide_01.png"],
        expect_glob_counts={"carousel/slide_*.png": 3},
        expect_image_dimensions={"carousel/slide_01.png": [1080, 1080]},
    )

    evaluated = asyncio.run(run_golden(task, controller_factory=lambda: controller))

    assert evaluated.passed is True
