"""Fast HTTP page fetch + main-content extraction (no Chromium).

Use this before browser_* when the page is public/static. Beats launching
Playwright for docs, blogs, READMEs, and most marketing pages.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse


class _TextExtractor(HTMLParser):
    """Lightweight readability-ish extractor (stdlib only)."""

    _SKIP = frozenset({"script", "style", "noscript", "svg", "iframe", "nav", "footer", "aside"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self._chunks: list[str] = []
        self._links: list[tuple[str, str]] = []
        self._capture_link: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if t == "title":
            self._in_title = True
            return
        if t == "a":
            href = ""
            for k, v in attrs:
                if k == "href" and v:
                    href = v
                    break
            self._capture_link = href
            self._link_text = []
            return
        if t in {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "section", "article"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if t == "title":
            self._in_title = False
            return
        if t == "a" and self._capture_link is not None:
            text = " ".join("".join(self._link_text).split()).strip()
            if text and self._capture_link:
                self._links.append((text[:120], self._capture_link))
            self._capture_link = None
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
            return
        text = data.strip()
        if not text:
            return
        if self._capture_link is not None:
            self._link_text.append(text)
        self._chunks.append(text + " ")


def _normalize_text(raw: str, max_chars: int) -> str:
    text = re.sub(r"[ \t]+", " ", raw)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if len(text) > max_chars:
        return text[: max_chars - 20] + "\n\n…[truncated]"
    return text


async def fetch_url(
    url: str,
    *,
    max_chars: int = 12000,
    timeout_s: float = 25.0,
    include_links: bool = True,
    max_links: int = 30,
) -> str:
    """GET a URL and return title + extracted text (+ optional links)."""
    import httpx

    u = (url or "").strip()
    if not u:
        return "ERROR: url is required"
    parsed = urlparse(u)
    if parsed.scheme not in {"http", "https"}:
        return "ERROR: only http/https URLs are supported for web_fetch"
    headers = {
        "User-Agent": (
            "KagehaAgent/0.4 (+https://github.com/kageha; research fetch; "
            "compatible; like Firefox)"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        async with httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=True,
            headers=headers,
        ) as client:
            resp = await client.get(u)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: fetch failed: {e}"

    final = str(resp.url)
    ctype = (resp.headers.get("content-type") or "").lower()
    body = resp.text or ""
    if resp.status_code >= 400:
        return f"ERROR: HTTP {resp.status_code} for {final}\ncontent-type: {ctype}\n{body[:500]}"

    if "html" not in ctype and not body.lstrip().lower().startswith("<!doctype") and "<html" not in body[:500].lower():
        # Plain text / markdown / json — return as-is.
        text = _normalize_text(body, max_chars)
        return (
            f"url: {final}\nstatus: {resp.status_code}\n"
            f"content-type: {ctype or 'unknown'}\nmode: raw\n\n{text}"
        )

    parser = _TextExtractor()
    try:
        parser.feed(body)
        parser.close()
    except Exception:
        # Extremely broken HTML — strip tags crudely.
        text = _normalize_text(re.sub(r"<[^>]+>", " ", body), max_chars)
        return f"url: {final}\nstatus: {resp.status_code}\nmode: crude\n\n{text}"

    title = " ".join(parser.title.split()).strip() or "(no title)"
    text = _normalize_text("".join(parser._chunks), max_chars)
    lines = [
        f"title: {title}",
        f"url: {final}",
        f"status: {resp.status_code}",
        "mode: extract",
        "",
        text,
    ]
    if include_links and parser._links:
        lines.append("")
        lines.append("## Links")
        seen: set[str] = set()
        n = 0
        for label, href in parser._links:
            abs_href = urljoin(final, href)
            if abs_href in seen:
                continue
            seen.add(abs_href)
            lines.append(f"- {label}: {abs_href}")
            n += 1
            if n >= max_links:
                break
    return "\n".join(lines)


def fetch_url_sync(url: str, **kwargs: Any) -> str:
    import asyncio

    return asyncio.get_event_loop().run_until_complete(fetch_url(url, **kwargs))
