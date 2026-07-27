"""Task-based effort (low / medium / high) for planning and model thinking."""

from __future__ import annotations

import re
from typing import Literal

Effort = Literal["low", "medium", "high"]

_LOW_RE = re.compile(
    r"^(thanks|thank\s+you|thx|ok|okay|yes|no|yep|yeah|hi|hello|"
    r"status|progress|where\s+are|what'?s\s+up|"
    r"pause|play|start|stop|mute|unmute|home|back|"
    r"volume\s*up|volume\s*down|vol\s*up|vol\s*down|"
    r"louder|quieter|power\s*off|turn\s*off)\b",
    re.I,
)
_HIGH_RE = re.compile(
    r"\b("
    r"research|architect|architecture|deploy|implement|build|create|"
    r"generate|multi[- ]?step|end[- ]?to[- ]?end|presentation|pptx|"
    r"video|carousel|full\s+stack|production|migrate|refactor|"
    r"comprehensive|deep\s+dive|compare|evaluate|benchmark"
    r")\b",
    re.I,
)
_MEDIUM_HINT_RE = re.compile(
    r"\b(fix|edit|update|add|change|tweak|summarize|summarise|"
    r"explain|teach|draft|write|search|find|open|browse)\b",
    re.I,
)


def classify_effort(task: str, *, default: Effort = "medium") -> Effort:
    """Heuristic effort from the user/task text."""
    text = (task or "").strip()
    if not text:
        return default
    if len(text) < 40 and _LOW_RE.search(text):
        return "low"
    if _HIGH_RE.search(text) or len(text) >= 280:
        return "high"
    if _MEDIUM_HINT_RE.search(text) or len(text) >= 80:
        return "medium"
    if len(text) < 48:
        return "low"
    return default


def gemini_thinking_level(effort: Effort | str | None, *, has_tools: bool) -> str:
    """Map effort → Gemini 3 thinkingLevel."""
    level = (effort or "medium").strip().lower()
    if level not in {"low", "medium", "high"}:
        level = "medium"
    if not has_tools and level == "high":
        # Pure text answers rarely need max thinking.
        return "medium"
    if not has_tools and level == "medium":
        return "low"
    return level
