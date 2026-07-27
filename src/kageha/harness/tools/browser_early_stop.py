"""Deterministic early-stop for simple browser screenshot / browse goals.

After a successful ``browser_screenshot`` / ``browse_logged_in`` that wrote an
image, skip further LLM→bash/list_dir thrash (common on "open URL + screenshot"
tasks that otherwise burn the full max_steps budget).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCREENSHOT_TOOLS = frozenset(
    {
        "browser_screenshot",
        "browse_logged_in",
    }
)

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

_SCREENSHOT_GOAL_RE = re.compile(
    r"\b(screenshot|screen\s*shot|capture|snapshot|take\s+a\s+pic|"
    r"picture\s+of|image\s+of)\b|"
    r"\b(open|go\s+to|navigate|visit|browse)\b.{0,80}\bhttps?://",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BrowserEarlyStop:
    tool: str
    path: str
    answer: str
    evidence: str


def goal_wants_browser_screenshot(objective: str) -> bool:
    text = str(objective or "").strip()
    if not text:
        return False
    return bool(_SCREENSHOT_GOAL_RE.search(text))


def _extract_image_path(content: str) -> str | None:
    text = str(content or "")
    # Common tool result shapes: "screenshot: artifacts/x.png" or plain path.
    for m in re.finditer(
        r"(?:screenshot|saved|path|wrote|file)\s*[:=]?\s*[`\"]?"
        r"([^\s`\"']+\.(?:png|jpe?g|webp|gif))",
        text,
        re.IGNORECASE,
    ):
        return m.group(1).strip()
    for m in re.finditer(
        r"((?:artifacts|outputs)/[^\s`\"']+\.(?:png|jpe?g|webp|gif))",
        text,
        re.IGNORECASE,
    ):
        return m.group(1).strip()
    for m in re.finditer(r"([^\s`\"']+\.(?:png|jpe?g|webp|gif))", text):
        cand = m.group(1).strip()
        if Path(cand).suffix.lower() in _IMAGE_SUFFIXES:
            return cand
    return None


def select_browser_early_stop(
    tool_rows: list[tuple[str, str]],
    *,
    objective: str = "",
) -> BrowserEarlyStop | None:
    """Return early-stop if a screenshot tool succeeded with an image path.

    Only fires when the objective looks like a browse/screenshot request, so
    multi-step research that happens to take a mid-flow screenshot is not cut
    short.
    """
    if not goal_wants_browser_screenshot(objective):
        return None
    # Prefer the last successful screenshot tool in this turn.
    for name, content in reversed(tool_rows):
        tool = str(name or "").strip()
        if tool not in _SCREENSHOT_TOOLS:
            continue
        low = str(content or "").lower()
        if any(tok in low for tok in ("error", "failed", "traceback")) and (
            "screenshot" not in low and "saved" not in low and "artifacts/" not in low
        ):
            continue
        path = _extract_image_path(content)
        if not path:
            continue
        answer = (
            f"Captured the page screenshot and saved it to `{path}`.\n\n"
            f"**Saved deliverable:** `{path}`"
        )
        return BrowserEarlyStop(
            tool=tool,
            path=path,
            answer=answer,
            evidence=f"browser_early_stop:{tool}:{path}",
        )
    return None


def has_browser_screenshot_evidence(
    tool_rows: list[tuple[str, Any]],
) -> bool:
    for name, content in tool_rows:
        if str(name or "").strip() not in _SCREENSHOT_TOOLS:
            continue
        if _extract_image_path(str(content or "")):
            return True
    return False
