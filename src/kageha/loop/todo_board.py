"""Parse session todo.md into a structured live board payload."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_CHECK_RE = re.compile(r"^[-*]\s+\[([ xX])\]\s+(.*)$")
_ID_PREFIX_RE = re.compile(r"^(?:`([^`]+)`|([A-Za-z]+\d+))\s*[:\-]?\s*(.*)$")


def parse_todo_markdown(markdown: str, *, label: str = "todos", limit: int = 24) -> dict[str, Any]:
    """Return ``{label, done, total, items[{id,text,done}]}`` from checkbox markdown."""
    items: list[dict[str, Any]] = []
    for raw in (markdown or "").splitlines():
        cm = _CHECK_RE.match(raw.strip())
        if not cm:
            continue
        done = cm.group(1).lower() == "x"
        body = cm.group(2).strip()
        # Normalize goal-card style `- [x] `g1` …` / `- [ ] p1: …`
        body = re.sub(r"`([^`]+)`\s*", r"\1: ", body, count=1)
        item_id = ""
        text = body
        im = _ID_PREFIX_RE.match(body)
        if im:
            item_id = (im.group(1) or im.group(2) or "").strip()
            rest = (im.group(3) or "").strip()
            if rest:
                text = rest
            elif item_id:
                text = item_id
        items.append(
            {
                "id": item_id,
                "text": text[:200],
                "done": done,
            }
        )
        if len(items) >= max(1, int(limit)):
            break
    done_n = sum(1 for it in items if it.get("done"))
    return {
        "label": (label or "todos").strip() or "todos",
        "done": done_n,
        "total": len(items),
        "items": items,
    }


def parse_todo_file(path: Path, *, label: str = "todos", limit: int = 24) -> dict[str, Any] | None:
    """Read ``todo.md`` (or any checklist path) into a board payload, or None if empty/missing."""
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    board = parse_todo_markdown(text, label=label, limit=limit)
    if not board["total"]:
        return None
    return board


def _checkbox_item_id(body: str) -> str:
    body_norm = re.sub(r"`([^`]+)`\s*", r"\1: ", (body or "").strip(), count=1)
    im = _ID_PREFIX_RE.match(body_norm)
    if not im:
        return ""
    return (im.group(1) or im.group(2) or "").strip()


def apply_done_ids_to_todo_markdown(markdown: str, done_ids: set[str]) -> tuple[str, bool]:
    """Flip unchecked boxes to ``[x]`` for ids in ``done_ids``. Returns (text, changed)."""
    if not done_ids:
        return markdown, False
    changed = False
    out: list[str] = []
    for raw in (markdown or "").splitlines():
        line = raw
        stripped = raw.strip()
        cm = _CHECK_RE.match(stripped)
        if cm and cm.group(1).lower() != "x":
            item_id = _checkbox_item_id(cm.group(2))
            if item_id and item_id in done_ids:
                lead = raw[: raw.find("-")] if "-" in raw else ""
                line = f"{lead}- [x] {cm.group(2).strip()}"
                changed = True
        out.append(line)
    new_md = "\n".join(out)
    if (markdown or "").endswith("\n") and new_md and not new_md.endswith("\n"):
        new_md += "\n"
    return new_md, changed


def collect_todo_done_ids(
    *,
    goal: Any | None = None,
    stages: list[Any] | None = None,
    include_all_goals: bool = False,
    include_all_plan_steps: bool = False,
    markdown: str = "",
) -> set[str]:
    """Ids to mark done in todo.md from goal card + plan stages."""
    done: set[str] = set()
    if goal is not None:
        for item in getattr(goal, "items", None) or []:
            iid = str(getattr(item, "id", "") or "").strip()
            if not iid:
                continue
            if include_all_goals or bool(getattr(item, "passes", False)):
                done.add(iid)
    if stages:
        for stage in stages:
            sid = str(getattr(stage, "id", "") or "").strip()
            if not sid:
                continue
            status = str(getattr(stage, "status", "") or "").lower()
            if include_all_plan_steps or status == "done":
                done.add(sid)
    if include_all_plan_steps and not stages:
        for raw in (markdown or "").splitlines():
            cm = _CHECK_RE.match(raw.strip())
            if not cm:
                continue
            item_id = _checkbox_item_id(cm.group(2))
            if item_id and re.match(r"p\d+$", item_id, re.IGNORECASE):
                done.add(item_id)
    return done


def sync_todo_file_from_progress(
    path: Path,
    *,
    goal: Any | None = None,
    stages: list[Any] | None = None,
    success: bool = False,
) -> bool:
    """Write goal/plan progress into todo.md checkboxes. Returns True if file changed."""
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    done_ids = collect_todo_done_ids(
        goal=goal,
        stages=stages,
        include_all_goals=success,
        include_all_plan_steps=success,
        markdown=text,
    )
    new_text, changed = apply_done_ids_to_todo_markdown(text, done_ids)
    if not changed:
        return False
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError:
        return False
    return True


def board_log_lines(board: dict[str, Any]) -> str:
    """CLI/progress log text matching historical ``[kageha] todos: N/M`` format."""
    label = str(board.get("label") or "todos")
    done = int(board.get("done") or 0)
    total = int(board.get("total") or 0)
    lines = [f"[kageha] {label}: {done}/{total}"]
    for it in board.get("items") or []:
        if not isinstance(it, dict):
            continue
        mark = "x" if it.get("done") else " "
        item_id = str(it.get("id") or "").strip()
        text = str(it.get("text") or "").strip()
        body = f"{item_id}: {text}" if item_id and text and not text.startswith(item_id) else (text or item_id)
        lines.append(f"- [{mark}] {body}")
    return "\n".join(lines)
