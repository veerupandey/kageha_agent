"""Keep resume follow-ups short — prevent nested wrapper growth."""

from __future__ import annotations

import re

_WRAPPER_STARTS = (
    "continue in this existing session",
    "resume previous work in this session",
    "user follow-up in session",
)


def is_resume_wrapper(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return any(t.startswith(p) for p in _WRAPPER_STARTS) or t.count(
        "continue in this existing session"
    ) >= 2


def unwrap_objective(text: str, *, fallback: str = "") -> str:
    """Peel nested chat-resume wrappers to recover the real objective."""
    t = (text or "").strip()
    if not t:
        return (fallback or "").strip()[:2000]
    if not is_resume_wrapper(t) and "Original task:" not in t:
        return t[:2000]

    for m in reversed(list(re.finditer(r"(?im)^Original task:\s*(.+)$", t))):
        cand = m.group(1).strip()
        # Peel accidental nested "Original task:" prefixes from prior bugs.
        while cand.lower().startswith("original task:"):
            cand = cand.split(":", 1)[1].strip()
        low = cand.lower()
        if not cand or any(low.startswith(p) for p in _WRAPPER_STARTS):
            continue
        return cand[:2000]

    for line in t.splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith(
            (
                "continue in this",
                "resume previous",
                "original task:",
                "current goals:",
                "prior taskstate",
                "user follow-up:",
                "reuse existing",
                "# goal",
                "- [",
            )
        ):
            continue
        return s[:2000]

    return (fallback or t)[:500]


def build_followup_prompt(
    *,
    run_id: str,
    message: str,
    original: str,
    state_projection: str = "",
    goal_md: str = "",
    recent_chat: str = "",
) -> str:
    """Compact resume prompt — never embed a previous resume wrapper."""
    obj = unwrap_objective(original) or f"session {run_id}"
    msg = (message or "").strip()
    parts = [
        f"User follow-up in session {run_id}:",
        msg,
        "",
        f"Original objective: {obj}",
        "Reuse existing workspace files. Do not restart from scratch unless the user asks.",
        "Honor preferences and decisions already made in this session's conversation.",
    ]
    if recent_chat.strip():
        parts.extend(["", recent_chat.strip()[:3500]])
    if goal_md.strip():
        # Keep goals short — they are also in working notes / goal_card.json
        parts.extend(["", "Goals:", goal_md.strip()[:1200]])
    if state_projection.strip():
        parts.extend(["", "TaskState:", state_projection.strip()[:2500]])
    text = "\n".join(parts)
    if len(text) > 7000:
        return text[:6800] + "\n…[truncated]"
    return text
