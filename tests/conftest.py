"""Shared pytest fixtures — stdin-read guard (REL-002 / Requirement 3.2).

The guard below fails a test immediately, with a clear message, if the test
attempts to read from real stdin before a legitimate interactive prompt has
been recorded earlier in that same test. This turns an accidental blocking
read (which would otherwise hang the whole suite in CI) into an instant,
diagnosable test failure.

Tests that must intentionally read stdin after a real interactive prompt has
occurred can request the ``stdin_guard`` fixture explicitly and call
``stdin_guard.mark_prompted()`` first.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import pytest


@dataclass
class StdinGuardState:
    """Per-test state tracking whether an interactive prompt has occurred."""

    prompted: bool = False

    def mark_prompted(self) -> None:
        """Record that a legitimate interactive prompt already occurred.

        Call this before any stdin read that is expected as part of the
        test's own scenario; subsequent reads in the same test are allowed.
        """
        self.prompted = True


class _GuardedStdinBuffer:
    """Guards byte-level reads on ``sys.stdin.buffer``."""

    def __init__(self, real_buffer: object, state: StdinGuardState) -> None:
        self._real_buffer = real_buffer
        self._state = state

    def _fail(self, method: str) -> None:
        if self._state.prompted:
            return
        pytest.fail(
            f"Test attempted sys.stdin.buffer.{method}() without a prior "
            "interactive prompt occurring earlier in this test. Inject "
            "deterministic input/approvers instead of reading real stdin "
            "(Requirement 3.2). Call stdin_guard.mark_prompted() first if "
            "this read is intentional.",
            pytrace=False,
        )

    def read(self, *args: object, **kwargs: object) -> object:
        self._fail("read")
        return self._real_buffer.read(*args, **kwargs)  # type: ignore[attr-defined]

    def readline(self, *args: object, **kwargs: object) -> object:
        self._fail("readline")
        return self._real_buffer.readline(*args, **kwargs)  # type: ignore[attr-defined]

    def readlines(self, *args: object, **kwargs: object) -> object:
        self._fail("readlines")
        return self._real_buffer.readlines(*args, **kwargs)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._real_buffer, name)


class _GuardedStdin:
    """Proxy for ``sys.stdin`` that fails fast on unexpected blocking reads.

    Non-read attributes (``isatty``, monkeypatched overrides, etc.) pass
    through to the real stdin object unchanged, so existing tests that patch
    e.g. ``sys.stdin.isatty`` keep working.
    """

    def __init__(self, real_stdin: object, state: StdinGuardState) -> None:
        self._real_stdin = real_stdin
        self._state = state
        self._guarded_buffer: _GuardedStdinBuffer | None = None

    def _fail(self, method: str) -> None:
        if self._state.prompted:
            return
        pytest.fail(
            f"Test attempted sys.stdin.{method}() without a prior "
            "interactive prompt occurring earlier in this test. Inject "
            "deterministic input/approvers instead of reading real stdin "
            "(Requirement 3.2). Call stdin_guard.mark_prompted() first if "
            "this read is intentional.",
            pytrace=False,
        )

    def read(self, *args: object, **kwargs: object) -> object:
        self._fail("read")
        return self._real_stdin.read(*args, **kwargs)  # type: ignore[attr-defined]

    def readline(self, *args: object, **kwargs: object) -> object:
        self._fail("readline")
        return self._real_stdin.readline(*args, **kwargs)  # type: ignore[attr-defined]

    def readlines(self, *args: object, **kwargs: object) -> object:
        self._fail("readlines")
        return self._real_stdin.readlines(*args, **kwargs)  # type: ignore[attr-defined]

    def __next__(self) -> object:
        self._fail("__next__")
        return next(self._real_stdin)  # type: ignore[call-overload]

    def __iter__(self) -> object:
        self._fail("__iter__")
        return iter(self._real_stdin)  # type: ignore[call-overload]

    @property
    def buffer(self) -> _GuardedStdinBuffer:
        if self._guarded_buffer is None:
            self._guarded_buffer = _GuardedStdinBuffer(
                getattr(self._real_stdin, "buffer", None), self._state
            )
        return self._guarded_buffer

    def __getattr__(self, name: str) -> object:
        return getattr(self._real_stdin, name)


@pytest.fixture(autouse=True)
def stdin_guard(monkeypatch: pytest.MonkeyPatch) -> StdinGuardState:
    """Autouse guard: any real stdin read fails the test immediately unless
    a prior interactive prompt was marked via ``stdin_guard.mark_prompted()``.
    """
    state = StdinGuardState()
    monkeypatch.setattr(sys, "stdin", _GuardedStdin(sys.stdin, state))
    return state
