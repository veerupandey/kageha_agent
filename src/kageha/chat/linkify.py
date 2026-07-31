"""Make URLs and local file paths clickable in the terminal (OSC 8)."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from rich.style import Style
from rich.text import Text

# Image / video / common deliverable extensions → treat as file links.
_FILE_EXT = (
    r"png|jpe?g|gif|webp|svg|bmp|ico|"
    r"mp4|mov|webm|mkv|avi|m4v|"
    r"mp3|wav|m4a|aac|flac|"
    r"pdf|docx?|pptx?|xlsx?|"
    r"html?|md|txt|csv|json|ya?ml|toml|"
    r"zip|tar|gz|tgz|bz2|"
    r"py|ts|tsx|js|jsx|css|go|rs|java"
)

# Markdown image / link (image first so ![...](...) wins).
_MD_IMG = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_MD_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")

# Bare URLs (web + file).
_URL = re.compile(
    r"(?P<url>(?:https?|file)://[^\s<>\[\]\"']+)",
    re.I,
)

# Absolute / home paths and relative deliverable-looking paths.
_PATH = re.compile(
    rf"(?P<path>"
    rf"(?:~|/Users|/home|/tmp|/var|/private|/opt|/Volumes|/teamspace|/workspace)[^\s<>\[\]\"']+"
    rf"|(?:(?:\./|\.\./)?(?:artifacts|sessions|downloads)/[^\s<>\[\]\"']+)"
    rf"|(?:[A-Za-z0-9_./-]+\.(?:{_FILE_EXT}))"
    rf")",
    re.I,
)

_TRAIL_PUNCT = ".,;:!?)]}\"'"


def _trim_url(raw: str) -> tuple[str, str]:
    """Split trailing sentence punctuation from a matched URL/path."""
    url = raw
    trail = ""
    while url and url[-1] in _TRAIL_PUNCT:
        # Keep balanced ) in URLs like wikipedia (disambiguation)
        if url[-1] == ")" and url.count("(") >= url.count(")"):
            break
        trail = url[-1] + trail
        url = url[:-1]
    return url, trail


def href_for_target(target: str) -> str | None:
    """Normalize a link target to an href terminals can open."""
    t = (target or "").strip()
    if not t:
        return None
    low = t.lower()
    if low.startswith(("http://", "https://", "file://")):
        return t
    # Local path → file:// URI
    try:
        path = Path(t).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        return path.as_uri()
    except Exception:  # noqa: BLE001
        return None


def _link_style(href: str) -> Style:
    return Style(color="#38bdf8", underline=True, link=href)


def _append_linked(out: Text, label: str, target: str) -> None:
    href = href_for_target(target)
    if not href:
        out.append(label)
        return
    out.append(label, style=_link_style(href))


def linkify_text(text: str) -> Text:
    """Turn markdown links, bare URLs, and file paths into clickable Rich Text."""
    src = text or ""
    out = Text()
    i = 0
    n = len(src)

    while i < n:
        # Find the next special match at or after i.
        candidates: list[tuple[int, str, re.Match[str]]] = []
        for kind, cre in (
            ("md_img", _MD_IMG),
            ("md_link", _MD_LINK),
            ("url", _URL),
            ("path", _PATH),
        ):
            m = cre.search(src, i)
            if m:
                candidates.append((m.start(), kind, m))
        if not candidates:
            out.append(src[i:])
            break
        candidates.sort(key=lambda x: x[0])
        start, kind, m = candidates[0]
        if start > i:
            out.append(src[i:start])

        if kind == "md_img":
            alt = m.group(1) or "image"
            target = m.group(2)
            label = alt if alt.strip() else Path(unquote(urlparse(target).path)).name or target
            _append_linked(out, label, target)
        elif kind == "md_link":
            label, target = m.group(1), m.group(2)
            _append_linked(out, label, target)
        elif kind == "url":
            raw = m.group("url")
            url, trail = _trim_url(raw)
            # Prefer a short label for file URLs.
            if url.lower().startswith("file://"):
                label = Path(unquote(urlparse(url).path)).name or url
            else:
                label = url
            _append_linked(out, label, url)
            if trail:
                out.append(trail)
        else:  # path
            raw = m.group("path")
            path, trail = _trim_url(raw)
            # Avoid linking version-like fragments (e.g. 1/40) or lone words.
            if not _looks_like_path(path):
                out.append(raw)
            else:
                label = Path(path).name if "/" in path or path.startswith("~") else path
                # Show full path when it's a Saved: receipt style absolute path.
                display = path if path.startswith(("/", "~")) else label
                _append_linked(out, display, path)
                if trail:
                    out.append(trail)
        i = m.end()

    return out


def _looks_like_path(path: str) -> bool:
    p = path.strip()
    if len(p) < 3:
        return False
    # Skip step counters / ratios.
    if re.fullmatch(r"\d+/\d+", p):
        return False
    if p.startswith(("/", "~", "./", "../")):
        return True
    if "/" in p and re.search(rf"\.(?:{_FILE_EXT})$", p, re.I):
        return True
    if re.search(rf"\.(?:{_FILE_EXT})$", p, re.I):
        return True
    return False
