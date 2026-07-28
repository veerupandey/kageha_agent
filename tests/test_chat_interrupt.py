"""CLI mid-turn Ctrl+C cancel — stop steps without exiting chat."""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import pytest

from kageha.chat.interrupt import (
    await_run_with_interrupt,
    clear_hitl_interrupt,
    hitl_stop_event,
    request_hitl_interrupt,
)
from kageha.harness.approvals import race_tty_and_file
from kageha.loop.controller import RunResult
from kageha.loop.goal_card import GoalCard, GoalItem
from kageha.runtime.engine import AgentRuntime
from kageha.runtime.store import RuntimeStore
from kageha.runtime.types import TurnRequest


class _CancellableController:
    """Waits until cancel() is called, then returns cancelled."""

    def __init__(self, **kwargs):  # noqa: ANN003
        self.cancelled = False
        self._gate = asyncio.Event()
        self.event_sink = kwargs.get("event_sink")

    def cancel(self) -> None:
        self.cancelled = True
        self._gate.set()

    def inject(self, message: str) -> None:
        del message

    async def run(
        self,
        task: str,
        *,
        run_id: str,
        workspace,  # noqa: ANN001
        fresh_turn: bool,
        turn_task: str | None,
        loop_mode: str = "full",
        agent_mode: str = "normal",
    ) -> RunResult:
        del fresh_turn, turn_task, loop_mode, agent_mode, workspace
        try:
            await asyncio.wait_for(self._gate.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        status = "cancelled" if self.cancelled else "success"
        return RunResult(
            run_id=run_id,
            status=status,
            message="Cancelled" if status == "cancelled" else "done",
            goal=GoalCard(
                task=task,
                items=[GoalItem("g1", task, passes=status == "success")],
            ),
            steps=1,
            spent_usd=0.0,
            validated=status == "success",
        )


@pytest.fixture(autouse=True)
def _clear_hitl():
    clear_hitl_interrupt()
    yield
    clear_hitl_interrupt()


def test_hitl_interrupt_event_roundtrip():
    assert not hitl_stop_event().is_set()
    request_hitl_interrupt()
    assert hitl_stop_event().is_set()
    clear_hitl_interrupt()
    assert not hitl_stop_event().is_set()


def test_race_tty_wakes_on_hitl_interrupt(tmp_path: Path):
    """Cancel must unblock HITL so the turn can finish stopping."""
    stop = hitl_stop_event()

    def _trigger() -> None:
        import time

        time.sleep(0.15)
        request_hitl_interrupt()

    import threading

    threading.Thread(target=_trigger, daemon=True).start()
    ans = race_tty_and_file(
        ["Allow test?"],
        timeout=2.0,
        tty_path=None,  # skip controlling tty
        answer_path=tmp_path / "ANSWER.txt",
        pending_path=tmp_path / "PENDING.md",
        poll_interval=0.05,
        external_stop=stop,
    )
    assert ans == ""
    assert stop.is_set()


@pytest.mark.asyncio
async def test_await_run_with_interrupt_cooperative_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    store = RuntimeStore(tmp_path / "runtime.db")
    runtime = AgentRuntime(store=store, controller_factory=_CancellableController)
    try:
        handle = runtime.submit(TurnRequest(objective="long task"))
        loop = asyncio.get_running_loop()
        loop.call_later(0.05, handle.cancel)
        result = await await_run_with_interrupt(handle)
        assert result.status == "cancelled"
        assert result.run_id
    finally:
        runtime.close()
        store.close()


@pytest.mark.asyncio
async def test_await_run_with_interrupt_sigint_handler_cancels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    store = RuntimeStore(tmp_path / "runtime.db")
    runtime = AgentRuntime(store=store, controller_factory=_CancellableController)
    handlers: dict[int, object] = {}
    try:
        handle = runtime.submit(TurnRequest(objective="sigint task"))
        loop = asyncio.get_running_loop()
        original = loop.add_signal_handler

        def _capture(sig: int, callback, *args, **kwargs):  # noqa: ANN001
            handlers[sig] = callback
            return original(sig, callback, *args, **kwargs)

        monkeypatch.setattr(loop, "add_signal_handler", _capture)

        async def _fire() -> None:
            for _ in range(40):
                if signal.SIGINT in handlers:
                    cb = handlers[signal.SIGINT]
                    assert callable(cb)
                    cb()
                    return
                await asyncio.sleep(0.025)
            handle.cancel()

        asyncio.create_task(_fire())
        result = await await_run_with_interrupt(handle)
        assert result.status == "cancelled"
    finally:
        runtime.close()
        store.close()
