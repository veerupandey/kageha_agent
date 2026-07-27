"""Delayed channel progress indicators so quick replies remain instant."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable


class DelayedProgress:
    def __init__(
        self,
        send: Callable[[], Awaitable[object]],
        *,
        delay_s: float = 0.75,
    ) -> None:
        self.send = send
        self.delay_s = max(0.0, delay_s)
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "DelayedProgress":
        async def worker() -> None:
            await asyncio.sleep(self.delay_s)
            await self.send()

        self._task = asyncio.create_task(worker())
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

