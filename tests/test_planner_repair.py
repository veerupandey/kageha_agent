from __future__ import annotations

import pytest

from kageha.loop.planner import (
    _loads_json_lenient,
    _objective_fallback_plan,
    _parse_plan_json,
    make_plan,
)
from kageha.models.base import ChatMessage, ChatResponse, ChatUsage


def test_loads_json_lenient_repairs_trailing_comma() -> None:
    data = _loads_json_lenient(
        '{"summary":"ship it","steps":[{"id":"1","description":"A","tools":[]},]}'
    )
    assert isinstance(data, dict)
    assert data["summary"] == "ship it"
    assert len(data["steps"]) == 1


def test_parse_plan_json_repairs_trailing_comma() -> None:
    plan = _parse_plan_json(
        'Here is the plan:\n{"summary":"ship it","milestones":["done"],'
        '"steps":[{"id":"1","description":"Build it","tools":["bash"]},]}\n',
        task="ship it",
        allowed_tools={"bash", "read_file"},
    )
    assert plan.summary == "ship it"
    assert plan.source == "llm"
    assert len(plan.steps) == 1
    assert plan.steps[0].description == "Build it"


def test_objective_fallback_plan_keeps_task_specific() -> None:
    plan = _objective_fallback_plan(
        "Build a tiny KV HTTP API with REST endpoints, persist to disk, "
        "and include pytest coverage that proves concurrent writes.",
        {"write_file", "edit_file", "bash", "read_file", "todo_write"},
    )
    assert plan.source == "template"
    joined = " ".join(step.description.lower() for step in plan.steps)
    assert "kv http api" in joined or "implement the requested work" in joined
    assert "verification" in joined or "tests" in joined
    assert "inspect the workspace" not in joined


class _PlannerRouter:
    def __init__(self, texts: list[str]) -> None:
        self.texts = list(texts)
        self.calls = 0

    async def chat(self, messages, **kwargs):  # noqa: ANN001, ANN003
        _ = messages, kwargs
        idx = min(self.calls, len(self.texts) - 1)
        self.calls += 1
        return object(), ChatResponse(
            message=ChatMessage(role="assistant", content=self.texts[idx]),
            usage=ChatUsage(),
            model="m",
        )


@pytest.mark.asyncio
async def test_make_plan_repairs_malformed_json_via_second_pass() -> None:
    router = _PlannerRouter(
        [
            '{"summary":"demo","steps":[{"id":"1","description":"One","tools":[]},',
            '{"summary":"demo","milestones":["done"],'
            '"steps":[{"id":"1","description":"One","tools":[]}]}',
        ]
    )
    plan = await make_plan("demo objective", router, available_tools={"bash"})
    assert plan.source == "llm"
    assert plan.steps[0].description == "One"
    assert router.calls >= 1
