"""Focused HITL race tests — file path + pty tty (no live Terminal required)."""

from __future__ import annotations

import os
import pty
import sys
import threading
import time
from pathlib import Path

import pytest

from kageha.harness.approvals import race_tty_and_file


def test_prompt_has_unambiguous_answer_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from io import StringIO

    from kageha.harness.approvals import _emit_prompt

    output = StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    _emit_prompt(
        ["Which style?", "[Y] Professional    [N] Keep current"],
        tmp_path / "ANSWER.txt",
        tty_path=None,
    )
    assert "Which style?" in output.getvalue()
    assert output.getvalue().endswith("Your answer> ")


def test_race_file_answer(tmp_path: Path) -> None:
    answer = tmp_path / "ANSWER.txt"
    pending = tmp_path / "PENDING.md"
    prompts = ["[HITL] test file path", "Answer: y or n"]

    def writer() -> None:
        time.sleep(0.35)
        answer.write_text("y\n", encoding="utf-8")

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    ans = race_tty_and_file(
        prompts,
        timeout=5.0,
        tty_path=None,
        answer_path=answer,
        pending_path=pending,
        poll_interval=0.05,
    )
    t.join(timeout=2)
    assert ans == "y"
    assert pending.is_file()
    assert "answered" in pending.read_text(encoding="utf-8").lower() or "y" in pending.read_text(
        encoding="utf-8"
    )


def test_race_clears_stale_answer(tmp_path: Path) -> None:
    answer = tmp_path / "ANSWER.txt"
    pending = tmp_path / "PENDING.md"
    answer.write_text("stale-yes\n", encoding="utf-8")

    def writer() -> None:
        time.sleep(0.4)
        answer.write_text("fresh\n", encoding="utf-8")

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    ans = race_tty_and_file(
        ["prompt"],
        timeout=5.0,
        tty_path=None,
        answer_path=answer,
        pending_path=pending,
        poll_interval=0.05,
    )
    t.join(timeout=2)
    assert ans == "fresh"


def test_race_tty_via_pty(tmp_path: Path) -> None:
    answer = tmp_path / "ANSWER.txt"
    pending = tmp_path / "PENDING.md"
    master, slave = pty.openpty()
    slave_name = os.ttyname(slave)

    def type_answer() -> None:
        time.sleep(0.4)
        os.write(master, b"B\n")

    t = threading.Thread(target=type_answer, daemon=True)
    t.start()
    try:
        ans = race_tty_and_file(
            ["[HITL] cover style?", "Type A or B"],
            timeout=5.0,
            tty_path=slave_name,
            answer_path=answer,
            pending_path=pending,
            poll_interval=0.05,
        )
    finally:
        t.join(timeout=2)
        os.close(master)
        os.close(slave)
    assert ans == "B"


def test_race_timeout_empty(tmp_path: Path) -> None:
    ans = race_tty_and_file(
        ["will timeout"],
        timeout=0.35,
        tty_path=None,
        answer_path=tmp_path / "ANSWER.txt",
        pending_path=tmp_path / "PENDING.md",
        poll_interval=0.05,
    )
    assert ans == ""


@pytest.mark.asyncio
async def test_cli_approver_accepts_y(monkeypatch: pytest.MonkeyPatch) -> None:
    from kageha.harness import approvals as ap
    from kageha.harness.approvals import ApprovalDecision, ApprovalRequest, cli_approver

    monkeypatch.setattr(ap, "race_tty_and_file", lambda _lines, **_kw: "y")
    ok = await cli_approver(
        ApprovalRequest(
            action="bash",
            detail="pip install cowsay",
            risk_class="shell_network_or_destructive",
            default=ApprovalDecision.ASK,
        )
    )
    assert ok.approved is True
    assert ok.feedback == ""


@pytest.mark.asyncio
async def test_cli_approver_denies_n(monkeypatch: pytest.MonkeyPatch) -> None:
    from kageha.harness import approvals as ap
    from kageha.harness.approvals import ApprovalDecision, ApprovalRequest, cli_approver

    monkeypatch.setattr(ap, "race_tty_and_file", lambda _lines, **_kw: "n")
    ok = await cli_approver(
        ApprovalRequest(
            action="bash",
            detail="sudo rm -rf /",
            risk_class="shell_network_or_destructive",
            default=ApprovalDecision.ASK,
        )
    )
    assert ok.approved is False
    assert ok.feedback == ""


@pytest.mark.asyncio
async def test_cli_ask_human_returns_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    from kageha.harness import approvals as ap
    from kageha.harness.approvals import cli_ask_human

    monkeypatch.setattr(ap, "race_tty_and_file", lambda _lines, **_kw: "A")
    assert await cli_ask_human("cover A or B?") == "A"


@pytest.mark.asyncio
async def test_cli_binary_question_accepts_yes_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kageha.harness import approvals as ap
    from kageha.harness.approvals import cli_ask_human

    captured: list[str] = []

    def answer(lines, **_kwargs):  # noqa: ANN001
        captured.extend(lines)
        return "y"

    monkeypatch.setattr(ap, "race_tty_and_file", answer)
    result = await cli_ask_human(
        "Use the overall architecture?",
        yes_label="Overall architecture",
        no_label="Choose a specific angle",
    )
    assert result == "yes"
    assert any("[Y] Overall architecture" in line for line in captured)
    assert any("[N] Choose a specific angle" in line for line in captured)


@pytest.mark.asyncio
async def test_ask_human_prompts_only_once_per_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from kageha.harness.approvals import ApprovalGate
    from kageha.harness.runtime import HarnessContext
    from kageha.harness.sandbox import SessionWorkspace
    from kageha.harness.tools import builtin

    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    asker = AsyncMock(return_value="yes")
    monkeypatch.setattr(builtin, "cli_ask_human", asker)
    ctx = HarnessContext(
        workspace=SessionWorkspace.create("hitl-once"),
        approvals=ApprovalGate(auto_approve=True),
        router=MagicMock(),
    )
    tool = builtin.register(ctx).get("ask_human")
    assert tool is not None

    first = await tool.call(
        question="Use the overall architecture?",
        yes_label="Overall",
        no_label="Specific",
    )
    duplicate = await tool.call(question="Use the overall architecture?")
    second_question = await tool.call(question="Which visual style?")

    assert '"answer": "yes"' in first
    assert '"reused": true' in duplicate
    assert "clarification_limit" in second_question
    asker.assert_awaited_once()


@pytest.mark.asyncio
async def test_deferred_chat_question_does_not_open_nested_tty_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from kageha.harness.approvals import ApprovalGate
    from kageha.harness.runtime import HarnessContext
    from kageha.harness.sandbox import SessionWorkspace
    from kageha.harness.tools import builtin

    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    tty_asker = AsyncMock(side_effect=AssertionError("must not block on TTY"))
    monkeypatch.setattr(builtin, "cli_ask_human", tty_asker)
    ctx = HarnessContext(
        workspace=SessionWorkspace.create("deferred-hitl"),
        approvals=ApprovalGate(auto_approve=True),
        router=MagicMock(),
    )
    ctx.meta["defer_human_input"] = True
    tool = builtin.register(ctx).get("ask_human")
    assert tool is not None

    result = await tool.call(
        question="Use the overall architecture?",
        yes_label="Overall",
        no_label="Specific",
    )

    assert "needs_user_input" in result
    assert "Use the overall architecture?" in result
    tty_asker.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_mode_environment_is_a_deferred_input_failsafe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from kageha.harness.approvals import ApprovalGate
    from kageha.harness.runtime import HarnessContext
    from kageha.harness.sandbox import SessionWorkspace
    from kageha.harness.tools import builtin

    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("KAGEHA_CHAT_MODE", "1")
    tty_asker = AsyncMock(side_effect=AssertionError("must not block on TTY"))
    monkeypatch.setattr(builtin, "cli_ask_human", tty_asker)
    ctx = HarnessContext(
        workspace=SessionWorkspace.create("deferred-env"),
        approvals=ApprovalGate(auto_approve=True),
        router=MagicMock(),
    )
    tool = builtin.register(ctx).get("ask_human")
    assert tool is not None
    result = await tool.call(question="Which slides?")
    assert "needs_user_input" in result
    tty_asker.assert_not_awaited()
