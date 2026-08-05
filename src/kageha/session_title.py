"""Cursor-style session titles: provisional from first message, upgrade as chat develops."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

TITLE_SOURCE_AUTO = "auto"
TITLE_SOURCE_USER = "user"

_WEAK_EXACT = frozenset(
    {
        "hi",
        "hey",
        "hello",
        "yo",
        "sup",
        "hola",
        "howdy",
        "ok",
        "okay",
        "k",
        "kk",
        "yes",
        "yep",
        "yeah",
        "y",
        "no",
        "nope",
        "n",
        "thanks",
        "thank you",
        "thx",
        "ty",
        "test",
        "testing",
        "ping",
        "pong",
        "hmm",
        "hm",
        "hmmm",
        "help",
        "?",
        "??",
        "...",
        "continue",
        "go",
        "go on",
        "next",
        "please",
        "pls",
        "sure",
        "cool",
        "nice",
        "great",
        "good",
        "done",
        "stop",
        "cancel",
        "p",
        "a",
        "b",
        "c",
    }
)

_SLASH_PREFIX = re.compile(r"^/[a-z0-9_-]+\b", re.I)


def clip_title(text: str, *, limit: int = 60) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


def normalize_title_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def is_weak_title(text: str) -> bool:
    """True for greetings, single letters, and other non-descriptive labels."""
    value = normalize_title_text(text)
    if not value:
        return True
    low = value.lower().rstrip(".!?")
    if low in _WEAK_EXACT:
        return True
    if len(value) <= 2:
        return True
    # Slash-only commands without a useful argument
    if value.startswith("/") and len(value.split()) <= 1:
        return True
    words = [w for w in re.split(r"\s+", low) if w]
    if len(words) <= 2 and all(w in _WEAK_EXACT for w in words):
        return True
    return False


def title_score(text: str) -> int:
    """Higher = better session label candidate."""
    value = normalize_title_text(text)
    if not value:
        return -1
    if is_weak_title(value):
        return 0
    words = value.split()
    # Prefer concise labels over long assistant sentences.
    length_score = len(value) if len(value) <= 48 else max(0, 72 - len(value))
    score = length_score + min(len(words), 8) * 4
    if re.search(r"[/\\.]|artifacts/|SKILL\.md|\.md\b|\.png\b|\.py\b", value, re.I):
        score += 8
    if any(ch.isupper() for ch in value[1:]):
        score += 4
    if value.startswith("/"):
        score -= 10
    # Sentence-y assistant openers are weaker labels.
    if re.match(
        r"^(i |i'm |i’ve |here |sure |okay |done |finished |working |got it)",
        value,
        re.I,
    ):
        score -= 20
    return score


def title_from_message(message: str) -> str:
    value = normalize_title_text(message)
    # Drop leading slash command token when there is a useful argument.
    if value.startswith("/"):
        parts = value.split(None, 1)
        if len(parts) == 2:
            value = parts[1]
        else:
            value = _SLASH_PREFIX.sub("", value).strip()
    # Sidebar labels should be glanceable, not a copy of the whole prompt.
    words = value.split()
    if len(words) > 8:
        value = " ".join(words[:8]) + "…"
    return clip_title(value)


def title_from_artifact_path(path: str) -> str | None:
    name = Path(str(path or "")).name
    if not name or name.lower() in {"skill.md", "todo.md", "plan.md", "readme.md"}:
        return None
    stem = Path(name).stem.strip()
    if not stem or is_weak_title(stem):
        return None
    # market_research / The-Daily-Film-Edit → readable label
    pretty = re.sub(r"[_\-]+", " ", stem).strip()
    pretty = re.sub(r"\s+", " ", pretty)
    # Drop common export/size suffixes: "… 4x5", "… v2"
    pretty = re.sub(r"\b\d+\s*[x×]\s*\d+\b", "", pretty, flags=re.I).strip()
    pretty = re.sub(r"\bv\d+\b", "", pretty, flags=re.I).strip()
    pretty = re.sub(r"\s+", " ", pretty)
    if not pretty or is_weak_title(pretty):
        return None
    if pretty.islower() or "_" in stem or "-" in stem:
        pretty = pretty.title()
    return clip_title(pretty)


def _artifact_rank_bonus(path: str) -> int:
    lower = str(path or "").lower()
    bonus = 18
    if lower.endswith((".md", ".txt", ".pdf", ".docx", ".html")):
        bonus += 12
    elif lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        bonus += 2
    if re.search(r"\d+\s*[x×]\s*\d+", Path(lower).stem):
        bonus -= 10
    if any(tok in lower for tok in ("nano_banana", "screenshot", "tmp", "draft")):
        bonus -= 8
    return bonus


def title_from_assistant_text(text: str) -> str | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    # Prefer a markdown heading.
    for line in raw.splitlines():
        m = re.match(r"^#{1,3}\s+(.+)$", line.strip())
        if m:
            candidate = title_from_message(m.group(1))
            if candidate and not is_weak_title(candidate):
                return candidate
    # First meaningful sentence / line
    for line in raw.splitlines():
        line = normalize_title_text(line)
        if not line or line.startswith("```") or line.startswith("|"):
            continue
        line = re.sub(r"^[-*•]\s+", "", line)
        line = re.sub(r"^\d+\.\s+", "", line)
        candidate = title_from_message(line)
        if candidate and title_score(candidate) >= 20:
            return candidate
    return None


def pick_best_title(
    *,
    user_message: str = "",
    assistant_message: str = "",
    artifact_paths: list[str] | None = None,
) -> str | None:
    scored: list[tuple[int, str]] = []
    um = title_from_message(user_message)
    if um:
        # User text is the primary signal when it's substantive.
        bonus = 0 if is_weak_title(um) else 25
        scored.append((title_score(um) + bonus, um))
    for path in artifact_paths or []:
        at = title_from_artifact_path(path)
        if at:
            scored.append((title_score(at) + _artifact_rank_bonus(path), at))
    am = title_from_assistant_text(assistant_message)
    if am:
        scored.append((title_score(am), am))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def apply_session_title(
    meta: dict[str, Any],
    *,
    candidate: str | None,
    force_user: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Update meta in-place. Returns (meta, changed).

    Auto titles may upgrade while still weak; manual (user) titles are never overwritten
    unless force_user is True (PATCH rename).
    """
    out = dict(meta)
    source = str(out.get("title_source") or "").strip().lower()
    current = normalize_title_text(str(out.get("title") or ""))

    if force_user:
        clipped = clip_title(candidate or "")
        out["title"] = clipped
        out["title_source"] = TITLE_SOURCE_USER
        return out, clipped != current or source != TITLE_SOURCE_USER

    if source == TITLE_SOURCE_USER:
        return out, False

    clipped = clip_title(candidate or "")
    if not clipped:
        return out, False

    if not current:
        out["title"] = clipped
        out["title_source"] = TITLE_SOURCE_AUTO
        return out, True

    # Upgrade provisional/weak auto titles only — keep a good auto title stable.
    if is_weak_title(current) and title_score(clipped) > title_score(current):
        out["title"] = clipped
        out["title_source"] = TITLE_SOURCE_AUTO
        return out, True

    return out, False


def load_workspace_title(session_id: str) -> str | None:
    """Read title from ~/.kageha/sessions/{id}/session.json if present."""
    try:
        from kageha.config import sessions_dir

        path = sessions_dir() / str(session_id) / "session.json"
        if not path.is_file():
            return None
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        title = normalize_title_text(str(data.get("title") or ""))
        return title or None
    except Exception:  # noqa: BLE001
        return None
