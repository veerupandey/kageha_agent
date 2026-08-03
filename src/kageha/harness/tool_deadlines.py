"""Per-tool execution deadlines.

A single flat 120s timeout was previously shared by three independent,
unsynchronized mechanisms (in-process ``asyncio.wait_for`` in
``harness.router._call_tool``, ``ToolJournal``'s durable ``deadline_at``, and
``run_shell``'s own subprocess timeout). Slow-but-legitimate tools (video
generation, deep research) would either get killed early or, if bumped
individually, drift out of sync with the durable deadline the watchdog relies
on to reap orphaned attempts. This module is the single source of truth both
paths read from.
"""

from __future__ import annotations

DEFAULT_TOOL_DEADLINE_S = 120.0

# Tools that legitimately run long. Keep generous but bounded — the watchdog
# (runtime/engine.py) reaps anything still IN_PROGRESS past its deadline, so
# an overly long value just delays cleanup after a crash, it doesn't cause
# false positives against a healthy run.
_TOOL_DEADLINE_OVERRIDES: dict[str, float] = {
    # Video generation (Fal) — polling upstream job, can take minutes.
    "fal_image_to_video": 420.0,
    "fal_text_to_video": 420.0,
    # Image generation — usually fast, but occasionally slow under load.
    "nano_banana_generate": 180.0,
    "nano_banana_edit": 180.0,
    # Deep/multi-step research tools.
    "research_run": 300.0,
    "parallel_web_search": 90.0,
    # Browser automation — page loads / navigation chains.
    "browser_open": 60.0,
    "browser_connect": 45.0,
}


def tool_deadline_s(tool_name: str, default: float = DEFAULT_TOOL_DEADLINE_S) -> float:
    """Return the execution deadline (seconds) for ``tool_name``."""
    return _TOOL_DEADLINE_OVERRIDES.get(tool_name or "", default)
