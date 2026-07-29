"""Adaptive context budget — dynamic allocation based on task and model.

Replaces static per-section budgets with intelligent allocation that:
- Scores messages by semantic importance (not just recency)
- Allocates budget dynamically based on task complexity
- Adjusts for model context window size
- Supports active context rotation from memory
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from kageha.context.budget import SectionBudget, estimate_tokens


# Heuristic importance signals in messages
_HIGH_IMPORTANCE_SIGNALS = re.compile(
    r"(?i)\b(error|failed|fix|bug|critical|must|require|blocke[dr]|urgent|"
    r"approved|rejected|decision|architecture|design|constraint)\b"
)
_LOW_IMPORTANCE_SIGNALS = re.compile(
    r"(?i)\b(okay|sure|thanks|got it|understood|no problem|let me know|"
    r"sounds good|perfect|great)\b"
)
_TOOL_RESULT_TRUNCATABLE = re.compile(
    r"(?i)(listing|directory|search results|matches found|output:)"
)


@dataclass
class ImportanceScore:
    """Importance score for a message in the conversation."""

    index: int
    score: float  # 0.0 to 1.0
    reason: str = ""


@dataclass
class AdaptiveBudget:
    """Dynamic context budget that adjusts to task and model.

    Instead of fixed per-section caps, allocates a total token budget
    across sections based on what the current task needs most.
    """

    # Total budget (model-dependent)
    total_tokens: int = 24000
    # Minimum reservations (never go below these)
    min_system: int = 1500
    min_history: int = 4000
    min_working: int = 1000
    # Task complexity multiplier (set by caller)
    complexity: float = 1.0  # 0.5 (simple) to 2.0 (complex)
    # Active files in context (for fileMatch steering)
    active_files: list[str] = field(default_factory=list)

    @classmethod
    def for_model(cls, model_name: str, *, complexity: float = 1.0) -> AdaptiveBudget:
        """Create a budget calibrated to a model's context window."""
        # Conservative: use ~15% of model window for assembled context
        # (rest reserved for tool schemas, response, etc.)
        window_estimates = {
            "claude": 180_000,
            "gpt": 128_000,
            "gemini": 1_000_000,
            "o1": 128_000,
            "o3": 200_000,
        }
        base_window = 128_000
        model_lower = (model_name or "").lower()
        for prefix, window in window_estimates.items():
            if prefix in model_lower:
                base_window = window
                break

        # Use 12-18% of window depending on complexity
        pct = 0.12 + (0.06 * min(complexity, 2.0) / 2.0)
        total = int(base_window * pct)
        # Cap at practical limits
        total = max(16000, min(total, 60000))

        return cls(total_tokens=total, complexity=complexity)

    def allocate(self, *, has_plan: bool = False, has_tools: int = 0) -> SectionBudget:
        """Dynamically allocate budget across sections.

        More tools → more tool budget. Has plan → more working notes.
        Higher complexity → more history for context.
        """
        total = self.total_tokens
        # Start with minimum reservations
        system = self.min_system
        working = self.min_working
        history = self.min_history

        remaining = total - system - working - history

        # Allocate remaining based on task needs
        # Tools: proportional to count (more tools = more schema space)
        tool_share = min(0.20, 0.10 + has_tools * 0.005) if has_tools else 0.10
        tools = int(remaining * tool_share)

        # Skills + KB: fixed moderate share
        skills = int(remaining * 0.08)
        kb = int(remaining * 0.08)

        # History gets the bulk of what's left
        history_extra = remaining - tools - skills - kb
        history += max(0, history_extra)

        # Complexity adjustment: complex tasks need more history
        if self.complexity > 1.2:
            # Steal from tools/skills for more history
            steal = int((self.complexity - 1.0) * 1000)
            tools = max(1500, tools - steal // 2)
            skills = max(800, skills - steal // 2)
            history += steal

        # Plan mode: boost working notes
        if has_plan:
            boost = min(1500, history // 8)
            history -= boost
            working += boost

        return SectionBudget(
            system=system,
            tools=tools,
            skills=skills,
            kb=kb,
            history=history,
            working=working,
        )


def score_message_importance(
    content: str,
    *,
    role: str = "",
    is_tool_result: bool = False,
    recency_weight: float = 1.0,
) -> float:
    """Score a message's importance for context retention.

    Returns 0.0 (expendable) to 1.0 (must keep).
    """
    # Role-based overrides first
    if role == "system":
        return 1.0  # Never drop system

    if not content:
        return 0.1

    base = 0.5 * recency_weight

    if role == "user":
        base = max(base, 0.7)  # User messages are high priority

    # Signal-based scoring
    high_matches = len(_HIGH_IMPORTANCE_SIGNALS.findall(content))
    low_matches = len(_LOW_IMPORTANCE_SIGNALS.findall(content))

    base += min(0.3, high_matches * 0.05)
    base -= min(0.2, low_matches * 0.05)

    # Tool results: large verbose output is lower priority
    if is_tool_result:
        tokens = estimate_tokens(content)
        if tokens > 2000:
            base -= 0.15  # Large tool outputs are candidates for truncation
        if _TOOL_RESULT_TRUNCATABLE.search(content[:200]):
            base -= 0.1  # Listing/search results compress well

    # Length penalty for very long messages (they consume budget)
    tokens = estimate_tokens(content)
    if tokens > 3000:
        base -= 0.1

    return max(0.05, min(1.0, base))


def select_messages_by_importance(
    messages: list[Any],
    *,
    max_tokens: int,
    preserve_last_n: int = 4,
) -> list[Any]:
    """Select messages to fit within budget, preferring important ones.

    Always preserves the last N messages (most recent context).
    For older messages, uses importance scoring to decide what to keep.
    """
    if not messages:
        return []

    total_tokens = sum(estimate_tokens(getattr(m, "content", "") or "") for m in messages)
    if total_tokens <= max_tokens:
        return list(messages)

    # Always keep the last N messages
    preserved = messages[-preserve_last_n:] if len(messages) > preserve_last_n else messages[:]
    candidates = messages[:-preserve_last_n] if len(messages) > preserve_last_n else []

    preserved_tokens = sum(
        estimate_tokens(getattr(m, "content", "") or "") for m in preserved
    )
    budget_for_older = max_tokens - preserved_tokens

    if budget_for_older <= 0 or not candidates:
        return preserved

    # Score and sort candidates by importance
    scored = []
    for i, msg in enumerate(candidates):
        content = getattr(msg, "content", "") or ""
        role = getattr(msg, "role", "")
        recency = 0.3 + (0.7 * i / max(len(candidates) - 1, 1))
        score = score_message_importance(
            content,
            role=role,
            is_tool_result=(role == "tool"),
            recency_weight=recency,
        )
        scored.append((score, i, msg))

    # Sort by importance (highest first)
    scored.sort(key=lambda x: -x[0])

    # Greedily add messages within budget (maintain original order)
    selected_indices: set[int] = set()
    used = 0
    for score, idx, msg in scored:
        tokens = estimate_tokens(getattr(msg, "content", "") or "")
        if used + tokens <= budget_for_older:
            selected_indices.add(idx)
            used += tokens

    # Reconstruct in original order
    older_selected = [
        candidates[i] for i in sorted(selected_indices)
    ]

    return older_selected + preserved
