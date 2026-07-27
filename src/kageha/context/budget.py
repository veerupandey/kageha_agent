"""Per-section token budgets (approx via chars/4)."""

from __future__ import annotations

from dataclasses import dataclass


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


@dataclass
class SectionBudget:
    system: int = 2000
    tools: int = 3000
    skills: int = 1500
    kb: int = 2000
    history: int = 12000
    working: int = 2000

    @property
    def total(self) -> int:
        return (
            self.system
            + self.tools
            + self.skills
            + self.kb
            + self.history
            + self.working
        )


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    if estimate_tokens(text) <= max_tokens:
        return text
    # Keep head + tail for restorable compression feel
    max_chars = max_tokens * 4
    head = int(max_chars * 0.7)
    tail = max_chars - head
    return text[:head] + "\n...\n[compacted]\n...\n" + text[-tail:]
