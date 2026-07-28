"""Markdown → Rich renderable for the CLI chat panel.

Uses Rich's Markdown (headers, lists, tables, code, emphasis) and still
emits OSC-8 hyperlinks for https, file://, and bare local paths.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from markdown_it import MarkdownIt
from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import Markdown
from rich.style import Style

from kageha.chat.linkify import _FILE_EXT, _looks_like_path, _trim_url, href_for_target

# Fenced code blocks — leave contents untouched.
_FENCE = re.compile(r"(```.*?```|~~~.*?~~~)", re.DOTALL)
# Inline code spans.
_INLINE_CODE = re.compile(r"`[^`]+`")
# Already-linked markdown (images first).
_MD_IMG = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_MD_LINK = re.compile(r"(?<!!)\[[^\]]*\]\([^)]+\)")
_URL = re.compile(r"(?P<url>(?:https?|file)://[^\s<>\[\]\"']+)", re.I)
_PATH = re.compile(
    rf"(?P<path>"
    rf"(?:~|/Users|/home|/tmp|/var|/private|/opt|/Volumes)[^\s<>\[\]\"']+"
    rf"|(?:(?:\./|\.\./)?(?:artifacts|sessions|downloads)/[^\s<>\[\]\"']+)"
    rf"|(?:[A-Za-z0-9_./-]+\.(?:{_FILE_EXT}))"
    rf")",
    re.I,
)


def _md_escape_label(label: str) -> str:
    return label.replace("[", "\\[").replace("]", "\\]")


def _as_md_link(label: str, target: str) -> str:
    href = href_for_target(target) or target
    # Prefer file:// (clickable in Cursor/iTerm). markdown-it accepts it when
    # validateLink is patched in ChatMarkdown.
    return f"[{_md_escape_label(label)}]({href})"


def _linkify_segment(segment: str) -> str:
    """Wrap bare URLs/paths in markdown links; keep existing md links/code."""
    if not segment:
        return segment
    # Split out regions we must not rewrite.
    parts: list[tuple[str, bool]] = []  # (text, protect)
    idx = 0
    n = len(segment)
    while idx < n:
        candidates: list[tuple[int, int, str]] = []
        for cre in (_MD_IMG, _MD_LINK, _INLINE_CODE):
            m = cre.search(segment, idx)
            if m:
                candidates.append((m.start(), m.end(), m.group(0)))
        if not candidates:
            parts.append((segment[idx:], False))
            break
        candidates.sort(key=lambda x: x[0])
        start, end, raw = candidates[0]
        if start > idx:
            parts.append((segment[idx:start], False))
        parts.append((raw, True))
        idx = end

    out: list[str] = []
    for text, protect in parts:
        if protect:
            # Rewrite file:// already inside md links is unnecessary when
            # validateLink allows file://; leave as-is.
            out.append(text)
            continue
        out.append(_linkify_bare(text))
    return "".join(out)


def _linkify_bare(text: str) -> str:
    src = text
    out: list[str] = []
    i = 0
    n = len(src)
    while i < n:
        candidates: list[tuple[int, str, re.Match[str]]] = []
        for kind, cre in (("url", _URL), ("path", _PATH)):
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
        if kind == "url":
            raw = m.group("url")
            url, trail = _trim_url(raw)
            if url.lower().startswith("file://"):
                label = Path(unquote(urlparse(url).path)).name or url
            else:
                label = url
            out.append(_as_md_link(label, url))
            if trail:
                out.append(trail)
        else:
            raw = m.group("path")
            path, trail = _trim_url(raw)
            if not _looks_like_path(path):
                out.append(raw)
            else:
                label = Path(path).name if ("/" in path or path.startswith("~")) else path
                display = path if path.startswith(("/", "~")) else label
                out.append(_as_md_link(display, path))
                if trail:
                    out.append(trail)
        i = m.end()
    return "".join(out)


def prepare_markdown(text: str) -> str:
    """Normalize model markdown so the CLI renderer can hyperlink paths/URLs."""
    src = text or ""
    if not src.strip():
        return src
    chunks = _FENCE.split(src)
    rebuilt: list[str] = []
    for i, chunk in enumerate(chunks):
        if i % 2 == 1:
            # Fence delimiter groups from the capturing split.
            rebuilt.append(chunk)
        else:
            rebuilt.append(_linkify_segment(chunk))
    return "".join(rebuilt)


class ChatMarkdown(Markdown):
    """Rich Markdown with file:// hyperlinks and path auto-link preparation."""

    def __init__(
        self,
        markup: str,
        code_theme: str = "monokai",
        justify: Any = None,
        style: str | Style = "none",
        hyperlinks: bool = True,
        inline_code_lexer: str | None = None,
        inline_code_theme: str | None = None,
        *,
        prepare: bool = True,
    ) -> None:
        prepared = prepare_markdown(markup) if prepare else (markup or "")
        parser = MarkdownIt().enable("strikethrough").enable("table")
        # markdown-it blocks file:// by default (browser XSS concern). Safe here.
        parser.validateLink = lambda _url: True  # type: ignore[method-assign]
        self.markup = prepared
        self.parsed = parser.parse(prepared)
        self.code_theme = code_theme
        self.justify = justify
        self.style = style
        self.hyperlinks = hyperlinks
        self.inline_code_lexer = inline_code_lexer
        self.inline_code_theme = inline_code_theme or code_theme

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        yield from super().__rich_console__(console, options)


def render_chat_markdown(text: str) -> ChatMarkdown:
    """Build the renderable used by the assistant reply panel."""
    return ChatMarkdown(text or "", code_theme="monokai", hyperlinks=True)
