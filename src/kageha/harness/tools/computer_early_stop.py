"""Deterministic early-stop for grouped computer actions.

Research basis (only ship what can beat screenshot-loop CUAs on latency):

- OSWorld-Human (Abhyankar et al., 2025 / MLSys): LLM plan+reflect is 75–94% of
  CUA latency; later steps grow ~3× slower as history accumulates; recommended
  levers include **action grouping** and **history compression**. Best agents
  still take 1.4–4.3× more steps than human trajectories.
- Grouped-action WES: consecutive UI ops that share one observation should be
  one agent step — not replan after every micro-action.
- Claude computer-use guidance: without an explicit stop after success, agents
  keep cycling (extra screenshot/LLM turns).

What this module does NOT implement (insufficient competitive evidence):
- Bind / list_apps TTL caches (micro-optimizations; not shown to beat SOTA CUAs)
- Rust rewrites of the harness
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# Compound / grouped desktop actions — the OSWorld-Human "grouped step" unit.
_COMPOUND_TOOLS = frozenset({"computer_click_sequence"})
_COMPOUND_MODES = frozenset(
    {
        "type_text",
        "adaptive_text_from_labels",
        "ax_label_sequence",
        "ax_ref_sequence",
    }
)


@dataclass(frozen=True)
class ComputerEarlyStop:
    """Harness can finish the turn without another LLM call."""

    tool: str
    mode: str
    readings: list[dict[str, Any]]
    answer: str
    evidence: str


def _parse_tool_json(content: str) -> dict[str, Any] | None:
    text = (content or "").strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _reading_values(readings: list[Any]) -> list[str]:
    out: list[str] = []
    for row in readings:
        if isinstance(row, dict) and row.get("value") not in (None, ""):
            out.append(str(row["value"]))
    return out


def format_readings_answer(
    readings: list[dict[str, Any]], *, highlight: str | None = None
) -> str:
    vals = _reading_values(readings)
    if not vals:
        return "Computer action completed."
    if highlight and highlight in vals:
        return f"Readings: {highlight}"
    # Prefer the last reading (Calculator result display is usually last).
    if len(vals) == 1:
        return f"Readings: {vals[0]}"
    return f"Readings: {vals[-1]} (also: {', '.join(vals[:-1])})"


_SAFE_EXPR = re.compile(r"^[\d\.\+\-\*/\(\) ]+$")


def expected_value_from_type_text(text: str) -> str | None:
    """If text looks like ``8+9=``, return the evaluated result string."""
    raw = (text or "").strip().replace("×", "*").replace("÷", "/").replace(" ", "")
    if not raw.endswith("=") or len(raw) < 3:
        return None
    expr = raw[:-1]
    if not _SAFE_EXPR.fullmatch(expr):
        return None
    try:
        val = eval(expr, {"__builtins__": {}}, {})  # noqa: S307 — charset-gated
    except Exception:  # noqa: BLE001
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    if isinstance(val, (int, float)):
        return str(val)
    return None


def computer_compound_success(content: str, *, tool: str) -> ComputerEarlyStop | None:
    """Return early-stop payload if ``tool`` result is a successful grouped action."""
    if tool not in _COMPOUND_TOOLS:
        return None
    data = _parse_tool_json(content)
    if not data or data.get("ok") is not True:
        return None
    mode = str(data.get("mode") or "")
    if mode not in _COMPOUND_MODES:
        return None
    readings = data.get("readings")
    if not isinstance(readings, list) or not readings:
        return None
    values = _reading_values(readings)
    if not values:
        return None
    # Fail-closed when type_text encodes a checkable expression (avoid dirty UI).
    expected = expected_value_from_type_text(str(data.get("text") or ""))
    if expected is not None and expected not in values:
        return None
    slim = [
        row
        for row in readings
        if isinstance(row, dict) and row.get("value") not in (None, "")
    ][:8]
    answer = format_readings_answer(slim, highlight=expected)
    evidence = f"{tool}:{mode}:readings={','.join(values[:4])}"
    if expected:
        evidence += f":expected={expected}"
    return ComputerEarlyStop(
        tool=tool,
        mode=mode,
        readings=slim,
        answer=answer,
        evidence=evidence[:240],
    )


def select_computer_early_stop(
    tool_results: list[tuple[str, str]],
) -> ComputerEarlyStop | None:
    """Pick the last successful compound computer tool in this step's results."""
    hit: ComputerEarlyStop | None = None
    for name, content in tool_results:
        cand = computer_compound_success(content, tool=name or "")
        if cand is not None:
            hit = cand
    return hit


# Tools that mutate the desktop (get_state / list / doctor alone are not evidence).
_COMPUTER_MUTATION_TOOLS = frozenset(
    {
        "computer_launch",
        "computer_click",
        "computer_click_sequence",
        "computer_set_value",
        "computer_type",
        "computer_key",
        "computer_hotkey",
        "computer_scroll",
        "computer_move",
    }
)


def has_verified_computer_evidence(
    tool_results: list[tuple[str, str]],
) -> bool:
    """True when a turn includes a verified desktop mutation (not just a report file).

    ``effect: unverifiable`` / ERROR / DENIED do not count — Electron apps often
    report ok with unverifiable insert while nothing appears on screen.
    """
    for name, content in tool_results:
        tool = name or ""
        if tool not in _COMPUTER_MUTATION_TOOLS:
            continue
        text = (content or "").strip()
        lowered = text.lower()
        if not text or text.startswith("ERROR") or text.startswith("DENIED"):
            continue
        if "unverifiable" in lowered:
            continue
        if '"ok":false' in lowered.replace(" ", "") or '"ok": false' in lowered:
            continue
        # Successful compound / typed / clicked action.
        if tool == "computer_click_sequence":
            if computer_compound_success(text, tool=tool) is not None:
                return True
            # Still accept ok JSON without early-stop shape (e.g. non-calc UI).
            data = _parse_tool_json(text)
            if data and data.get("ok") is True:
                return True
            continue
        data = _parse_tool_json(text)
        if data is not None:
            if data.get("ok") is True and data.get("verified") is not False:
                return True
            continue
        # Non-JSON success summaries (rare).
        if "error" not in lowered[:80]:
            return True
    return False


_COMPUTER_KEEP_KEYS = (
    "ok",
    "mode",
    "app",
    "text",
    "labels",
    "refs",
    "clicks",
    "readings",
    "timing",
    "loop",
    "error",
    "hint",
    "verified",
    "effect",
    "chars",
    "delivery_mode",
)


def compress_computer_tool_content(content: str, *, tool_name: str = "") -> str:
    """History compression for computer_* tool JSON (OSWorld-Human lever).

    Drops bulky driver dumps while keeping readings needed to stop/verify.
    """
    name = tool_name or ""
    if not name.startswith("computer_"):
        return content
    data = _parse_tool_json(content)
    if data is None:
        # Non-JSON / ERROR / DENIED — keep short.
        text = (content or "").strip()
        return text if len(text) <= 400 else text[:399] + "…"
    slim: dict[str, Any] = {}
    for key in _COMPUTER_KEEP_KEYS:
        if key in data and data[key] not in (None, "", []):
            slim[key] = data[key]
    if "readings" in slim and isinstance(slim["readings"], list):
        slim["readings"] = slim["readings"][:8]
    # Explicitly drop known heavy fields if present.
    for drop in ("snapshot", "tree_markdown", "elements", "result", "screenshot"):
        slim.pop(drop, None)
    if not slim:
        return (content or "")[:400]
    return json.dumps(slim, separators=(",", ":"))


def task_mentions_expected_value(task: str, readings: list[dict[str, Any]]) -> bool:
    """Optional soft check: if task embeds a number, prefer seeing it in readings."""
    vals = _reading_values(readings)
    if not vals:
        return False
    # When the user asks for a specific arithmetic result, require it.
    nums = re.findall(r"\b\d{1,6}\b", task or "")
    if len(nums) < 2:
        return True  # no strong expectation
    # e.g. 8+9 → expect 17 in readings if both operands present
    blob = " ".join(vals)
    for n in nums:
        if n in blob:
            return True
    # If task looks like "compute X" without stating answer, any reading is fine.
    return True
