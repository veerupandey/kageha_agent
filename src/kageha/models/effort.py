"""Task-based effort (low / medium / high) for planning and model thinking.

The classifier returns an Effort level AND an optional TaskProfile that carries
richer metadata used by the planner, controller, and verifier to scale their
behavior to the actual task complexity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

Effort = Literal["low", "medium", "high"]


# ─── Low-effort: greetings, confirmations, device controls, status checks ───
_LOW_RE = re.compile(
    r"^(thanks|thank\s+you|thx|ok|okay|yes|no|yep|yeah|hi|hello|"
    r"status|progress|where\s+are|what'?s\s+up|"
    r"pause|play|start|stop|mute|unmute|home|back|"
    r"volume\s*up|volume\s*down|vol\s*up|vol\s*down|"
    r"louder|quieter|power\s*off|turn\s*off)\b",
    re.I,
)

# Simple Q&A / lookup patterns that never need multi-step execution.
_SIMPLE_QA_RE = re.compile(
    r"^(what\s+(?:is|are|does|was)|who\s+(?:is|are|was|owns|manages|maintains)|"
    r"how\s+(?:do|does|is|are|many|much|can|to)|"
    r"why\s+(?:is|are|does|do|did)|"
    r"when\s+(?:is|was|did|does)|"
    r"which\s+(?:is|are|file|module|function|class)|"
    r"where\s+(?:is|are|do|does|can)|"
    r"explain|tell\s+me\s+about|"
    r"what'?s\s+the\s+(?:difference|meaning|purpose|latest|current)|"
    r"can\s+you\s+explain|define|describe)\b",
    re.I,
)

# Single-action requests that need at most one tool call.
_SINGLE_ACTION_RE = re.compile(
    r"^(open|browse\s+to|show\s+me|read|cat|list|ls|"
    r"check\s+(?:the|my)|print|display|look\s+at|"
    r"run\s+(?:the|this)|execute|pip\s+install)\b",
    re.I,
)

# ─── High-effort: architectural, multi-file, research+implement ───
_HIGH_RE = re.compile(
    r"\b("
    r"research|architect|architecture|deploy|implement|build|create|"
    r"generate|multi[- ]?step|end[- ]?to[- ]?end|presentation|pptx|"
    r"video|carousel|full\s+stack|production|migrate|refactor|"
    r"comprehensive|deep\s+dive|compare|evaluate|benchmark|"
    r"scaffold|boilerplate|from\s+scratch|entire|complete\s+(?:app|system|api)|"
    r"redesign|overhaul|rewrite|integrate|orchestrat|pipeline|"
    r"ci/?cd|infrastructure|terraform|kubernetes|docker\s*compose"
    r")\b",
    re.I,
)

# Multi-deliverable signals: "X and Y and Z", numbered lists, etc.
_MULTI_DELIVERABLE_RE = re.compile(
    r"(?:"
    r"(?:create|build|write|add|implement|generate)\b[^.]{0,60}\b(?:and|,)\s*(?:also\s+)?(?:create|build|write|add|implement|generate)|"
    r"\b(?:with|including|plus)\s+(?:tests?|docs?|documentation|types?|migrations?|ci)|"
    r"\b(?:1\.|2\.|3\.|\d+\))\s*\w"
    r")",
    re.I,
)

# File-count signals: mentions of many specific files or directories.
_MULTI_FILE_RE = re.compile(
    r"(?:"
    r"(?:all|every|each)\s+(?:file|module|component|endpoint|route|test)|"
    r"(?:across|throughout)\s+(?:the\s+)?(?:codebase|project|repo)|"
    r"\b\w+\.(?:py|ts|js|go|rs|java)\b[^.]{0,80}\b\w+\.(?:py|ts|js|go|rs|java)\b"
    r")",
    re.I,
)

# Testing requirements make tasks heavier.
_REQUIRES_TESTS_RE = re.compile(
    r"\b(with\s+tests?|include\s+tests?|add\s+tests?|pytest|unit\s+tests?|"
    r"test\s+coverage|100%\s*coverage|e2e\s+tests?|integration\s+tests?)\b",
    re.I,
)

# ─── Medium-effort: focused single-concern work ───
_MEDIUM_HINT_RE = re.compile(
    r"\b(fix|edit|update|add|change|tweak|summarize|summarise|"
    r"explain|teach|draft|write|search|find|open|browse|"
    r"debug|trace|log|profile|optimize|"
    r"rename|move|extract|inline|clean\s*up)\b",
    re.I,
)


@dataclass
class TaskProfile:
    """Rich metadata about a task's complexity — used downstream by planner/verifier."""

    effort: Effort
    is_qa: bool = False
    is_single_action: bool = False
    is_multi_file: bool = False
    is_multi_deliverable: bool = False
    requires_tests: bool = False
    estimated_tool_calls: int = 1
    skip_planning: bool = False
    skip_verification: bool = False
    verification_depth: Literal["none", "light", "full"] = "light"
    signals: list[str] = field(default_factory=list)


def classify_effort(task: str, *, default: Effort = "medium") -> Effort:
    """Heuristic effort from the user/task text."""
    text = (task or "").strip()
    if not text:
        return default
    if len(text) < 40 and _LOW_RE.search(text):
        return "low"
    if _HIGH_RE.search(text) or len(text) >= 280:
        return "high"
    # Multi-deliverable or multi-file → high even for shorter prompts.
    if _MULTI_DELIVERABLE_RE.search(text) or _MULTI_FILE_RE.search(text):
        return "high"
    if _REQUIRES_TESTS_RE.search(text):
        # Tests requirement bumps at least to high if combined with build verbs.
        if _HIGH_RE.search(text):
            return "high"
        return "medium"
    if _MEDIUM_HINT_RE.search(text) or len(text) >= 80:
        return "medium"
    if len(text) < 48:
        return "low"
    return default


def profile_task(task: str) -> TaskProfile:
    """Build a full TaskProfile for downstream complexity-aware decisions.

    This is the richer sibling of classify_effort — call it when you need more
    than a single effort label (e.g., to decide planning depth, verification
    strategy, or step budget).
    """
    text = (task or "").strip()
    effort = classify_effort(text)
    signals: list[str] = []

    is_qa = bool(_SIMPLE_QA_RE.search(text))
    is_single_action = bool(_SINGLE_ACTION_RE.search(text)) and not _HIGH_RE.search(text)
    is_multi_file = bool(_MULTI_FILE_RE.search(text))
    is_multi_deliverable = bool(_MULTI_DELIVERABLE_RE.search(text))
    requires_tests = bool(_REQUIRES_TESTS_RE.search(text))

    if is_qa:
        signals.append("qa_pattern")
    if is_single_action:
        signals.append("single_action")
    if is_multi_file:
        signals.append("multi_file")
    if is_multi_deliverable:
        signals.append("multi_deliverable")
    if requires_tests:
        signals.append("requires_tests")

    # Estimate tool calls needed.
    if effort == "low":
        estimated_tools = 0 if is_qa else 1
    elif effort == "medium":
        estimated_tools = 3 if requires_tests else 2
    else:
        estimated_tools = 8 if is_multi_file else 5

    # Planning decisions.
    skip_planning = effort == "low" or (effort == "medium" and is_qa)
    skip_verification = effort == "low"

    # Verification depth.
    if effort == "low":
        verification_depth: Literal["none", "light", "full"] = "none"
    elif effort == "medium":
        verification_depth = "light"
    else:
        verification_depth = "full"

    return TaskProfile(
        effort=effort,
        is_qa=is_qa,
        is_single_action=is_single_action,
        is_multi_file=is_multi_file,
        is_multi_deliverable=is_multi_deliverable,
        requires_tests=requires_tests,
        estimated_tool_calls=estimated_tools,
        skip_planning=skip_planning,
        skip_verification=skip_verification,
        verification_depth=verification_depth,
        signals=signals,
    )


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
