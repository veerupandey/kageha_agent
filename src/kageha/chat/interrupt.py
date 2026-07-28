"""CLI mid-turn interrupt — Ctrl+C stops the running turn without exiting chat."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kageha.loop.controller import RunResult
    from kageha.runtime.engine import RunHandle

# Shared with HITL prompts so cancel can unblock an approval wait.
_HITL_STOP = threading.Event()


def hitl_stop_event() -> threading.Event:
    """Threading event set when the user requests a mid-turn stop."""
    return _HITL_STOP


def request_hitl_interrupt() -> None:
    _HITL_STOP.set()


def clear_hitl_interrupt() -> None:
    _HITL_STOP.clear()


def _cancelled_result(handle: "RunHandle", *, message: str = "Cancelled") -> "RunResult":
    from kageha.loop.controller import RunResult
    from kageha.loop.goal_card import GoalCard

    return RunResult(
        run_id=handle.session_id,
        status="cancelled",
        message=message,
        goal=GoalCard(task="", items=[]),
        steps=0,
        spent_usd=0.0,
        turn_id=handle.turn_id,
    )


async def await_run_with_interrupt(
    handle: "RunHandle",
    *,
    console: Any = None,
) -> "RunResult":
    """Await ``handle.result()``; first Ctrl+C cancels the turn, second quits.

    Idle-prompt Ctrl+C is unchanged (exits chat). This only applies while a
    turn is running.
    """
    from kageha.chat.ui import print_status

    clear_hitl_interrupt()
    loop = asyncio.get_running_loop()
    hits = {"n": 0}
    force_quit = asyncio.Event()
    registered = False

    def _on_sigint() -> None:
        hits["n"] += 1
        if hits["n"] == 1:
            print_status(
                "\nStopping… (Ctrl+C again to quit)",
                console=console,
            )
            handle.cancel()
            request_hitl_interrupt()
            return
        if handle._task is not None and not handle._task.done():
            handle._task.cancel()
        force_quit.set()

    try:
        try:
            loop.add_signal_handler(signal.SIGINT, _on_sigint)
            registered = True
        except (NotImplementedError, RuntimeError, AttributeError, OSError):
            registered = False

        if not registered:
            # Platforms without asyncio signal handlers (e.g. some Windows setups).
            try:
                return await handle.result()
            except KeyboardInterrupt:
                print_status(
                    "\nStopping… (Ctrl+C again to quit)",
                    console=console,
                )
                handle.cancel()
                request_hitl_interrupt()
                try:
                    return await asyncio.wait_for(handle.result(), timeout=30.0)
                except (asyncio.TimeoutError, asyncio.CancelledError, KeyboardInterrupt):
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        if handle._task is not None:
                            handle._task.cancel()
                            await asyncio.wait_for(handle.result(), timeout=2.0)
                    raise KeyboardInterrupt from None

        result_task = asyncio.create_task(handle.result())
        force_task = asyncio.create_task(force_quit.wait())
        done, pending = await asyncio.wait(
            {result_task, force_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if force_quit.is_set() and result_task not in done:
            print_status("\nForce quitting…", console=console)
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(handle.result(), timeout=2.0)
            raise KeyboardInterrupt

        try:
            return result_task.result()
        except asyncio.CancelledError:
            return _cancelled_result(handle)
        except Exception:
            # Surface runtime failures to the REPL error path.
            raise
    finally:
        if registered:
            with contextlib.suppress(Exception):
                loop.remove_signal_handler(signal.SIGINT)
        clear_hitl_interrupt()
