"""Blink-speed research backend.

Tiered pipeline (one tool call when possible):

  flash    — parallel search + parallel HTTP extract (no browser)
  standard — flash + warm headless CDP extract for thin/JS pages
  deep     — full interactive browser control (Playwright / Comet)

Headless backends: http | chromium (warm pool) | lightpanda/cdp (external CDP).
"""

from __future__ import annotations

from kageha.research.backend import ResearchBackend, research_run
from kageha.research.citations import (
    Citation,
    collect_citations_from_messages,
    ensure_cited_answer,
    merge_citations,
)

__all__ = [
    "Citation",
    "ResearchBackend",
    "collect_citations_from_messages",
    "ensure_cited_answer",
    "merge_citations",
    "research_run",
]
