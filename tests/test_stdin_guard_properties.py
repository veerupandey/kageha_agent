"""Property-based tests for the stdin-read guard (REL-002 / Property 8).

**Validates: Requirements 3.2**

Verifies that the stdin guard (conftest.py autouse fixture) fails a test
immediately if it attempts to read from stdin without a prior interactive
prompt having occurred in that test.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# Import the guard classes directly from conftest so we can test them
# in isolation without needing the autouse fixture to install them.
from conftest import StdinGuardState, _GuardedStdin, _GuardedStdinBuffer


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# The different read methods available on sys.stdin
_STDIN_READ_METHODS = st.sampled_from(["read", "readline", "readlines", "__next__", "__iter__"])

# The different read methods available on sys.stdin.buffer
_BUFFER_READ_METHODS = st.sampled_from(["read", "readline", "readlines"])

# Whether to mark prompted before the read attempt
_PROMPTED_BEFORE = st.booleans()

# Number of reads to attempt in sequence
_NUM_READS = st.integers(min_value=1, max_value=5)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeStdin:
    """Minimal fake stdin that supports read operations without blocking."""

    buffer: _FakeBuffer = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.buffer is None:
            self.buffer = _FakeBuffer()  # type: ignore[assignment]

    def read(self, *args: object, **kwargs: object) -> str:
        return ""

    def readline(self, *args: object, **kwargs: object) -> str:
        return ""

    def readlines(self, *args: object, **kwargs: object) -> list[str]:
        return []

    def __next__(self) -> str:
        raise StopIteration

    def __iter__(self):
        return iter([])

    def isatty(self) -> bool:
        return False


@dataclass
class _FakeBuffer:
    """Minimal fake stdin.buffer that supports read operations."""

    def read(self, *args: object, **kwargs: object) -> bytes:
        return b""

    def readline(self, *args: object, **kwargs: object) -> bytes:
        return b""

    def readlines(self, *args: object, **kwargs: object) -> list[bytes]:
        return []


# ---------------------------------------------------------------------------
# Property 8: The stdin guard fails immediately absent a prior interactive
# prompt
# ---------------------------------------------------------------------------


@given(
    read_method=_STDIN_READ_METHODS,
)
@settings(max_examples=100)
def test_stdin_guard_fails_without_prior_prompt(
    read_method: str,
) -> None:
    """**Validates: Requirements 3.2**

    For any sequence of test operations in which a stdin read is attempted
    before any interactive prompt has occurred in that test, the guard raises
    a failure at the point of that read rather than blocking.

    Tests that sys.stdin.{read, readline, readlines, __next__, __iter__}
    all fail immediately when no mark_prompted() has been called.
    """
    state = StdinGuardState()
    fake_stdin = _FakeStdin()
    guarded = _GuardedStdin(fake_stdin, state)

    # State should not be prompted
    assert not state.prompted

    # Attempting to read should trigger pytest.fail immediately
    with pytest.raises(pytest.fail.Exception) as exc_info:
        method = getattr(guarded, read_method)
        method()

    # The error message should reference Requirement 3.2
    assert "Requirement 3.2" in str(exc_info.value)
    assert "without a prior" in str(exc_info.value)


@given(
    read_method=_BUFFER_READ_METHODS,
)
@settings(max_examples=100)
def test_stdin_buffer_guard_fails_without_prior_prompt(
    read_method: str,
) -> None:
    """**Validates: Requirements 3.2**

    For any stdin.buffer read method attempted before any interactive prompt
    has occurred, the guard raises a failure at the point of that read
    rather than blocking.
    """
    state = StdinGuardState()
    fake_stdin = _FakeStdin()
    guarded = _GuardedStdin(fake_stdin, state)

    # Access the guarded buffer and try a read method
    guarded_buf = guarded.buffer

    # State should not be prompted
    assert not state.prompted

    # Attempting to read on the buffer should trigger pytest.fail immediately
    with pytest.raises(pytest.fail.Exception) as exc_info:
        method = getattr(guarded_buf, read_method)
        method()

    # The error message should reference Requirement 3.2
    assert "Requirement 3.2" in str(exc_info.value)
    assert "without a prior" in str(exc_info.value)


@given(
    read_method=_STDIN_READ_METHODS,
)
@settings(max_examples=100)
def test_stdin_guard_allows_read_after_prompt(
    read_method: str,
) -> None:
    """**Validates: Requirements 3.2**

    For any stdin read method attempted AFTER mark_prompted() has been called,
    the guard allows the read through to the underlying stdin without failing.
    This confirms the guard only blocks when no prior prompt has occurred.
    """
    state = StdinGuardState()
    fake_stdin = _FakeStdin()
    guarded = _GuardedStdin(fake_stdin, state)

    # Mark that an interactive prompt occurred
    state.mark_prompted()
    assert state.prompted

    # After marking, reads should pass through without pytest.fail
    method = getattr(guarded, read_method)
    # Should NOT raise — the prompt was already recorded
    try:
        method()
    except StopIteration:
        # __next__ on an empty iterator raises StopIteration, which is fine
        pass


@given(
    read_method=_BUFFER_READ_METHODS,
)
@settings(max_examples=100)
def test_stdin_buffer_guard_allows_read_after_prompt(
    read_method: str,
) -> None:
    """**Validates: Requirements 3.2**

    For any stdin.buffer read method attempted AFTER mark_prompted() has been
    called, the guard allows the read through without failing.
    """
    state = StdinGuardState()
    fake_stdin = _FakeStdin()
    guarded = _GuardedStdin(fake_stdin, state)

    # Mark that an interactive prompt occurred
    state.mark_prompted()
    assert state.prompted

    # Access the guarded buffer and try a read method — should pass through
    guarded_buf = guarded.buffer
    method = getattr(guarded_buf, read_method)
    method()  # Should NOT raise


@given(
    num_reads=_NUM_READS,
    read_method=_STDIN_READ_METHODS,
)
@settings(max_examples=100)
def test_stdin_guard_fails_on_every_read_without_prompt(
    num_reads: int,
    read_method: str,
) -> None:
    """**Validates: Requirements 3.2**

    For any number of read attempts without a prior prompt, each one fails
    immediately — the guard doesn't allow subsequent reads even after the
    first failure is caught.
    """
    state = StdinGuardState()
    fake_stdin = _FakeStdin()
    guarded = _GuardedStdin(fake_stdin, state)

    for _ in range(num_reads):
        with pytest.raises(pytest.fail.Exception):
            method = getattr(guarded, read_method)
            method()
