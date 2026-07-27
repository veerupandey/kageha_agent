"""Tool-loop guardrails (Hermes) + post-checkpoint guard (OpenClaw)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from kageha.loop.controller import _compose_turn_answer
from kageha.loop.goal_card import GoalCard, GoalItem
from kageha.loop.tool_guardrails import (
    PostCheckpointGuard,
    ToolCallGuardrailConfig,
    ToolCallGuardrailController,
    append_guidance,
    canonical_tool_args,
    classify_tool_failure,
    result_hash,
    synthetic_block_result,
)
from kageha.models.base import ChatMessage, ChatResponse, ChatUsage, ToolCall


def test_canonical_args_strips_volatile_keys():
    a = canonical_tool_args({"query": "x", "timestamp": 1, "pid": 99})
    b = canonical_tool_args({"query": "x", "timestamp": 2, "pid": 100})
    assert a == b
    assert "timestamp" not in a


def test_result_hash_ignores_guidance_suffix():
    from kageha.loop.tool_guardrails import ToolGuardrailDecision

    base = '{"ok": true, "data": 1}'
    guided = append_guidance(
        base,
        ToolGuardrailDecision(action="warn", code="x", message="loop", count=2),
    )
    assert "[Tool loop warning" in guided
    assert result_hash(base) == result_hash(guided)


def test_classify_tool_failure():
    assert classify_tool_failure("bash", "ERROR: boom")
    assert classify_tool_failure("bash", '{"exit_code": 1, "stdout": ""}')
    assert not classify_tool_failure("bash", '{"exit_code": 0, "stdout": "ok"}')
    assert not classify_tool_failure("read_file", "hello world")


def test_exact_failure_warn_then_block():
    cfg = ToolCallGuardrailConfig(
        enabled=True,
        hard_stop_enabled=True,
        exact_failure_warn_after=2,
        exact_failure_block_after=4,
        same_tool_failure_halt_after=99,
    )
    g = ToolCallGuardrailController(cfg)
    args = {"cmd": "missing"}
    for i in range(3):
        d = g.after_call("bash", args, "ERROR: not found", failed=True)
        if i == 0:
            assert d.action == "allow"
        else:
            assert d.action == "warn"
            assert d.code == "repeated_exact_failure_warning"

    # 4th failure still after_call warn (count=4); 5th before_call blocks
    d4 = g.after_call("bash", args, "ERROR: not found", failed=True)
    assert d4.action == "warn"
    assert d4.count == 4

    blocked = g.before_call("bash", args)
    assert blocked.should_halt
    assert blocked.code == "repeated_exact_failure_block"
    assert "Blocked bash" in synthetic_block_result(blocked)


def test_same_tool_failure_halt():
    cfg = ToolCallGuardrailConfig(
        enabled=True,
        hard_stop_enabled=True,
        exact_failure_warn_after=99,
        exact_failure_block_after=99,
        same_tool_failure_warn_after=3,
        same_tool_failure_halt_after=6,
    )
    g = ToolCallGuardrailController(cfg)
    for i in range(5):
        d = g.after_call("bash", {"cmd": f"try-{i}"}, "ERROR: x", failed=True)
        if i < 2:
            assert d.action == "allow"
        else:
            assert d.action == "warn"
            assert d.code == "same_tool_failure_warning"

    halt = g.after_call("bash", {"cmd": "try-5"}, "ERROR: x", failed=True)
    assert halt.action == "halt"
    assert halt.code == "same_tool_failure_halt"
    assert g.halt_decision is halt


def test_idempotent_no_progress_warn_and_halt():
    cfg = ToolCallGuardrailConfig(
        enabled=True,
        hard_stop_enabled=True,
        no_progress_warn_after=2,
        no_progress_block_after=4,
    )
    g = ToolCallGuardrailController(cfg)
    args = {"query": "bedrock"}
    body = "No results (DuckDuckGo blocked)"

    d1 = g.after_call("web_search", args, body, failed=False)
    assert d1.action == "allow"

    d2 = g.after_call("web_search", args, body, failed=False)
    assert d2.action == "warn"
    assert d2.code == "idempotent_no_progress_warning"

    g.after_call("web_search", args, body, failed=False)
    halt = g.after_call("web_search", args, body, failed=False)
    assert halt.should_halt
    assert "same result" in halt.message


def test_mutating_success_clears_failure_counters():
    cfg = ToolCallGuardrailConfig(hard_stop_enabled=True)
    g = ToolCallGuardrailController(cfg)
    args = {"path": "a.md", "content": "x"}
    g.after_call("write_file", args, "ERROR: disk", failed=True)
    g.after_call("write_file", args, "ERROR: disk", failed=True)
    ok = g.after_call("write_file", args, "Wrote 3 bytes", failed=False)
    assert ok.action == "allow"
    # Fresh failure streak after success
    d = g.after_call("write_file", args, "ERROR: disk", failed=True)
    assert d.action == "allow"
    assert d.count == 1


def test_hard_stop_disabled_never_blocks():
    cfg = ToolCallGuardrailConfig(
        hard_stop_enabled=False,
        exact_failure_warn_after=1,
        exact_failure_block_after=2,
        same_tool_failure_halt_after=2,
        no_progress_block_after=2,
    )
    g = ToolCallGuardrailController(cfg)
    for _ in range(5):
        d = g.after_call("bash", {"cmd": "x"}, "ERROR: e", failed=True)
        assert d.action in {"allow", "warn"}
        assert not d.should_halt
    assert not g.before_call("bash", {"cmd": "x"}).should_halt


def test_post_checkpoint_guard_aborts_identical_triples():
    guard = PostCheckpointGuard(enabled=True, window_size=4, abort_after=3)
    guard.arm()
    assert guard.armed
    args = {"q": "same"}
    body = "identical"
    v1 = guard.observe("web_search", args, body)
    assert not v1.should_abort
    again = guard.observe("web_search", args, body)
    assert not again.should_abort
    v3 = guard.observe("web_search", args, body)
    assert v3.should_abort
    assert v3.detector == "checkpoint_loop_persisted"


def test_post_checkpoint_guard_ignores_changing_results():
    guard = PostCheckpointGuard(enabled=True, window_size=4, abort_after=3)
    guard.arm()
    for i in range(4):
        v = guard.observe("web_search", {"q": "x"}, f"result-{i}")
        assert not v.should_abort


def test_post_checkpoint_guard_disabled():
    guard = PostCheckpointGuard(enabled=False)
    guard.arm()
    assert not guard.armed
    v = guard.observe("web_search", {"q": "x"}, "y")
    assert not v.should_abort


def test_compose_incomplete_status_fallback():
    async def _run():
        router = MagicMock()
        router.chat = AsyncMock(
            side_effect=RuntimeError("no model"),
        )
        text = await _compose_turn_answer(
            router=router,
            objective="search bedrock",
            status="max_steps",
            goal=GoalCard(task="t", items=[GoalItem("g1", "x", passes=False)]),
            history=[
                ChatMessage(
                    role="tool",
                    name="web_search",
                    content="partial notes about bedrock",
                )
            ],
            turn_artifacts=["artifacts/notes.md"],
        )
        assert "couldn't fully finish" in text.lower() or "partial" in text.lower()
        assert "notes.md" in text

    asyncio.run(_run())


def test_humanize_budget_status(tmp_path):
    from kageha.loop.artifacts import humanize_turn_reply

    reply = humanize_turn_reply(
        message="Hit budget",
        status="budget",
        user_line="do work",
        new_artifacts=[],
        workspace_root=tmp_path,
    )
    assert "cost limit" in reply.lower()


def test_ping_pong_warn_and_halt():
    cfg = ToolCallGuardrailConfig(
        hard_stop_enabled=True,
        ping_pong_warn_after=4,
        ping_pong_halt_after=6,
        global_breaker_warn_after=99,
        global_breaker_halt_after=99,
        exact_failure_warn_after=99,
        same_tool_failure_warn_after=99,
        same_tool_failure_halt_after=99,
        no_progress_warn_after=99,
        no_progress_block_after=99,
        stagnant_tools_warn_after=99,
        stagnant_tools_halt_after=99,
        unknown_tool_warn_after=99,
        unknown_tool_block_after=99,
    )
    g = ToolCallGuardrailController(cfg)
    # A,B,A,B,A,B → halt at 6
    pattern = [
        ("web_search", {"q": "a"}, "ra"),
        ("read_file", {"path": "x"}, "rb"),
    ]
    decisions = []
    for i in range(6):
        name, args, body = pattern[i % 2]
        decisions.append(g.after_call(name, args, body, failed=False))
    assert decisions[3].action == "warn"
    assert decisions[3].code == "ping_pong_warning"
    assert decisions[5].should_halt
    assert decisions[5].code == "ping_pong_halt"


def test_global_circuit_breaker():
    cfg = ToolCallGuardrailConfig(
        hard_stop_enabled=True,
        global_breaker_warn_after=3,
        global_breaker_halt_after=5,
        ping_pong_warn_after=99,
        ping_pong_halt_after=99,
        exact_failure_warn_after=99,
        same_tool_failure_warn_after=99,
        same_tool_failure_halt_after=99,
        no_progress_warn_after=99,
        no_progress_block_after=99,
        stagnant_tools_warn_after=99,
        stagnant_tools_halt_after=99,
        unknown_tool_warn_after=99,
        unknown_tool_block_after=99,
        history_size=20,
    )
    g = ToolCallGuardrailController(cfg)
    args = {"cmd": "same"}
    body = "same-out"
    for i in range(4):
        d = g.after_call("bash", args, body, failed=False)
        if i < 2:
            assert d.action == "allow"
        else:
            assert d.action == "warn"
            assert d.code == "global_circuit_breaker_warning"
    halt = g.after_call("bash", args, body, failed=False)
    assert halt.should_halt
    assert halt.code == "global_circuit_breaker"


def test_platform_hard_stop_resolution(monkeypatch):
    from kageha.config import tool_guardrails_hard_stop

    monkeypatch.delenv("KAGEHA_TOOL_GUARDRAILS_HARD_STOP", raising=False)
    monkeypatch.delenv("KAGEHA_INTERACTIVE_SOFT_GUARD", raising=False)
    assert tool_guardrails_hard_stop("whatsapp") is True
    assert tool_guardrails_hard_stop("telegram") is True
    assert tool_guardrails_hard_stop("cli") is True

    monkeypatch.setenv("KAGEHA_INTERACTIVE_SOFT_GUARD", "1")
    assert tool_guardrails_hard_stop("cli") is False
    assert tool_guardrails_hard_stop("whatsapp") is True

    monkeypatch.setenv("KAGEHA_TOOL_GUARDRAILS_HARD_STOP", "false")
    assert tool_guardrails_hard_stop("whatsapp") is False
    assert tool_guardrails_hard_stop("cli") is False


def test_from_env_respects_platform(monkeypatch):
    monkeypatch.delenv("KAGEHA_TOOL_GUARDRAILS_HARD_STOP", raising=False)
    monkeypatch.setenv("KAGEHA_INTERACTIVE_SOFT_GUARD", "1")
    cli_cfg = ToolCallGuardrailConfig.from_env("cli")
    wa_cfg = ToolCallGuardrailConfig.from_env("whatsapp")
    assert cli_cfg.hard_stop_enabled is False
    assert wa_cfg.hard_stop_enabled is True


def test_compose_scrubs_leaked_tool_calls():
    async def _run():
        router = MagicMock()
        router.chat = AsyncMock(
            return_value=(
                MagicMock(),
                ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(id="1", name="bash", arguments={"cmd": "x"})
                        ],
                    ),
                    usage=ChatUsage(),
                ),
            )
        )
        text = await _compose_turn_answer(
            router=router,
            objective="do work",
            status="max_steps",
            goal=GoalCard(task="t", items=[GoalItem("g1", "x", passes=False)]),
            history=[],
            turn_artifacts=[],
        )
        assert "stop condition" in text.lower()

    asyncio.run(_run())


def test_unknown_tool_warn_then_halt():
    cfg = ToolCallGuardrailConfig(
        hard_stop_enabled=True,
        unknown_tool_warn_after=2,
        unknown_tool_block_after=4,
        stagnant_tools_warn_after=99,
        stagnant_tools_halt_after=99,
        global_breaker_warn_after=99,
        global_breaker_halt_after=99,
        exact_failure_warn_after=99,
        same_tool_failure_warn_after=99,
        same_tool_failure_halt_after=99,
    )
    g = ToolCallGuardrailController(cfg)
    body = "ERROR: unknown tool 'foo'. Available: bash"
    d1 = g.after_call("foo", {}, body, failed=True)
    assert d1.action == "allow"
    assert d1.steer == "switch_tool"
    d2 = g.after_call("bar", {}, "ERROR: unknown tool 'bar'", failed=True)
    assert d2.action == "warn"
    assert d2.code == "unknown_tool_warning"
    g.after_call("baz", {}, "ERROR: unknown tool 'baz'", failed=True)
    halt = g.after_call("qux", {}, "ERROR: unknown tool 'qux'", failed=True)
    assert halt.should_halt
    assert halt.code == "unknown_tool_halt"
    assert g.consume_steer() == "switch_tool"


def test_stagnant_with_tools_warn_and_halt():
    cfg = ToolCallGuardrailConfig(
        hard_stop_enabled=True,
        stagnant_tools_warn_after=3,
        stagnant_tools_halt_after=5,
        ping_pong_warn_after=99,
        ping_pong_halt_after=99,
        global_breaker_warn_after=99,
        global_breaker_halt_after=99,
        exact_failure_warn_after=99,
        same_tool_failure_warn_after=99,
        same_tool_failure_halt_after=99,
        no_progress_warn_after=99,
        no_progress_block_after=99,
        unknown_tool_warn_after=99,
        unknown_tool_block_after=99,
    )
    g = ToolCallGuardrailController(cfg)
    args = {"cmd": "same"}
    body = "ERROR: boom"
    decisions = []
    for _ in range(5):
        decisions.append(g.after_call("bash", args, body, failed=True))
    assert any(d.code == "stagnant_with_tools_warning" for d in decisions)
    assert decisions[-1].should_halt
    assert decisions[-1].code == "stagnant_with_tools_halt"
    assert decisions[-1].steer == "switch_tool"


def test_exact_failure_sets_retry_steer():
    cfg = ToolCallGuardrailConfig(
        hard_stop_enabled=True,
        exact_failure_warn_after=2,
        exact_failure_block_after=99,
        same_tool_failure_halt_after=99,
        stagnant_tools_warn_after=99,
        stagnant_tools_halt_after=99,
        global_breaker_warn_after=99,
        global_breaker_halt_after=99,
    )
    g = ToolCallGuardrailController(cfg)
    g.after_call("bash", {"cmd": "x"}, "ERROR: e", failed=True)
    d = g.after_call("bash", {"cmd": "x"}, "ERROR: e", failed=True)
    assert d.action == "warn"
    assert d.steer == "retry"
    assert g.consume_steer() == "retry"


def test_switch_tool_steering_message():
    from kageha.loop.adaptive import switch_tool_steering_message
    from kageha.loop.task_state import TaskState

    state = TaskState(objective="x")
    state.record_tool(step=1, tool="bash", content="ERROR: timeout")
    msg = switch_tool_steering_message(state, detail="loop")
    assert "SWITCH_TOOL" in msg
    assert "Do NOT retry" in msg
