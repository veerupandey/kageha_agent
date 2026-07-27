"""Practical agent citations for web search / fetch / research.

Contract (compact, Perplexity-style):
  Citation: {id, url, title, snippet?}
  Tool results expose numbered hits ``[1] …`` and/or a ``sources`` list.
  Final answers cite claims with ``[n]`` and end with a ``## Sources`` section.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypedDict
from urllib.parse import urlparse


class Citation(TypedDict, total=False):
    id: str
    url: str
    title: str
    snippet: str


WEB_CITE_TOOLS = frozenset(
    {
        "web_search",
        "parallel_web_search",
        "web_fetch",
        "research_run",
        "parallel_web_fetch",
        "headless_fetch",
        "browser_open",
        "browser_snapshot",
        "browse_logged_in",
    }
)

_URL_RE = re.compile(r"https?://[^\s\]\)>'\"<>]+", re.I)
_NUMBERED_HIT_RE = re.compile(
    r"^\[(\d+)\]\s*(.+?)\s*$"
    r"(?:\n[ \t]+(\S+))?"
    r"(?:\n[ \t]+(.+))?$",
    re.M,
)
_BULLET_HIT_RE = re.compile(
    r"^-\s+(.+?)\s*$"
    r"(?:\n[ \t]+(\S+))?"
    r"(?:\n[ \t]+(.+))?$",
    re.M,
)
_SOURCES_HEADING_RE = re.compile(r"(?im)^##\s+sources\s*$")
_INLINE_CITE_RE = re.compile(r"\[(\d{1,3})\]")
_MARKER_RE = re.compile(
    r"\n*<!--kageha:sources\s*(\[.*?\])\s*-->\s*$",
    re.S,
)


def compact_snippet(text: str, *, max_len: int = 160) -> str:
    s = " ".join((text or "").split())
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


def normalize_url(url: str) -> str:
    u = (url or "").strip().rstrip(".,;:)")
    if not u:
        return ""
    parsed = urlparse(u)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return u


def make_citation(
    *,
    cid: str | int,
    url: str,
    title: str = "",
    snippet: str = "",
) -> Citation | None:
    nu = normalize_url(url)
    if not nu:
        return None
    title_s = " ".join((title or "").split()).strip() or nu
    out: Citation = {"id": str(cid), "url": nu, "title": title_s[:200]}
    sn = compact_snippet(snippet)
    if sn:
        out["snippet"] = sn
    return out


def merge_citations(
    items: list[Citation | dict[str, Any] | None],
    *,
    start_id: int = 1,
    max_n: int = 20,
) -> list[Citation]:
    """Deduplicate by URL; renumber ids from start_id."""
    out: list[Citation] = []
    seen: set[str] = set()
    for raw in items:
        if not raw:
            continue
        url = normalize_url(str(raw.get("url") or ""))
        if not url or url in seen:
            continue
        seen.add(url)
        c = make_citation(
            cid=start_id + len(out),
            url=url,
            title=str(raw.get("title") or ""),
            snippet=str(raw.get("snippet") or ""),
        )
        if c is None:
            continue
        out.append(c)
        if len(out) >= max_n:
            break
    return out


def parse_search_hits(text: str) -> list[Citation]:
    """Parse numbered or bullet search blobs into citations."""
    body = (text or "").strip()
    if not body or body.startswith("ERROR:"):
        return []
    # Drop trailing machine marker / notes before parsing.
    body = _MARKER_RE.sub("", body)
    note_idx = body.find("\n\n(note:")
    if note_idx > 0:
        body = body[:note_idx]
    # Prefer JSON wrapper if present.
    if body.lstrip().startswith("{"):
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            src = data.get("sources")
            if isinstance(src, list) and src:
                return merge_citations([x for x in src if isinstance(x, dict)])
            searches = data.get("searches")
            if isinstance(searches, list):
                acc: list[Citation] = []
                for row in searches:
                    if not isinstance(row, dict):
                        continue
                    acc.extend(parse_search_hits(str(row.get("results") or "")))
                return merge_citations(acc)

    def _resolve_hit(
        *,
        cid: str | int,
        title: str,
        url: str,
        snippet: str,
    ) -> Citation | None:
        title_s = (title or "").strip()
        url_s = (url or "").strip()
        sn_s = (snippet or "").strip()
        if not normalize_url(url_s):
            # Second line may be a snippet; URL often lives in the title.
            found = _URL_RE.search(title_s) or _URL_RE.search(url_s)
            if found:
                if not sn_s and url_s and not normalize_url(url_s):
                    sn_s = url_s
                url_s = found.group(0)
                title_s = title_s.replace(url_s, "").strip(" -—|") or url_s
            elif normalize_url(title_s):
                url_s, title_s = title_s, title_s
            else:
                return None
        return make_citation(cid=cid, url=url_s, title=title_s, snippet=sn_s)

    hits: list[Citation] = []
    for m in _NUMBERED_HIT_RE.finditer(body):
        c = _resolve_hit(
            cid=m.group(1),
            title=m.group(2) or "",
            url=m.group(3) or "",
            snippet=m.group(4) or "",
        )
        if c:
            hits.append(c)
    if hits:
        return merge_citations(hits)

    for m in _BULLET_HIT_RE.finditer(body):
        c = _resolve_hit(
            cid=len(hits) + 1,
            title=m.group(1) or "",
            url=m.group(2) or "",
            snippet=m.group(3) or "",
        )
        if c:
            hits.append(c)
    if hits:
        return merge_citations(hits)

    # Last resort: bare URLs in grounding-style dumps.
    bare: list[Citation] = []
    for u in _URL_RE.findall(body):
        c = make_citation(cid=len(bare) + 1, url=u)
        if c:
            bare.append(c)
    return merge_citations(bare)


def parse_fetch_citation(text: str, *, fallback_url: str = "") -> Citation | None:
    """Extract title/url/snippet from a web_fetch / page extract blob."""
    body = (text or "").strip()
    if not body or body.startswith("ERROR:"):
        return None
    title = ""
    url = fallback_url
    for line in body.splitlines()[:12]:
        low = line.lower()
        if low.startswith("title:"):
            title = line.split(":", 1)[-1].strip()
        elif low.startswith("url:"):
            url = line.split(":", 1)[-1].strip() or url
    if not url:
        m = _URL_RE.search(body)
        if m:
            url = m.group(0)
    # Body after header blank line → snippet.
    snippet = ""
    if "\n\n" in body:
        snippet = body.split("\n\n", 1)[-1]
    return make_citation(cid=1, url=url, title=title, snippet=snippet)


def citations_from_pages(pages: list[dict[str, Any]]) -> list[Citation]:
    acc: list[Citation] = []
    for row in pages or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "")
        title = str(row.get("title") or "")
        text = str(row.get("text") or row.get("error") or "")
        if str(row.get("ok") or "").lower() in {"false", "0"} and not title:
            # Still cite the URL if fetch failed — user can see attempt.
            c = make_citation(cid=len(acc) + 1, url=url, title=title or url)
        else:
            parsed = parse_fetch_citation(text, fallback_url=url)
            if parsed is None:
                c = make_citation(cid=len(acc) + 1, url=url, title=title)
            else:
                if title:
                    parsed["title"] = title[:200]
                c = parsed
        if c:
            acc.append(c)
    return merge_citations(acc)


def format_numbered_hits(citations: list[Citation]) -> str:
    lines: list[str] = []
    for c in citations:
        lines.append(f"[{c['id']}] {c.get('title') or c['url']}")
        lines.append(f"  {c['url']}")
        sn = c.get("snippet") or ""
        if sn:
            lines.append(f"  {sn}")
    return "\n".join(lines)


def format_sources_section(citations: list[Citation]) -> str:
    if not citations:
        return ""
    lines = ["## Sources"]
    for c in citations:
        title = c.get("title") or c["url"]
        lines.append(f"[{c['id']}] [{title}]({c['url']})")
    return "\n".join(lines)


def sources_marker(citations: list[Citation]) -> str:
    """Compact machine trailer (stripped before display when desired)."""
    if not citations:
        return ""
    slim = [
        {
            "id": c["id"],
            "url": c["url"],
            "title": c.get("title") or c["url"],
            **({"snippet": c["snippet"]} if c.get("snippet") else {}),
        }
        for c in citations
    ]
    return f"\n\n<!--kageha:sources\n{json.dumps(slim, separators=(',', ':'))}\n-->"


def normalize_search_output(text: str) -> str:
    """Rewrite search blobs into numbered citeable hits (+ compact marker)."""
    raw = strip_sources_marker(text or "").strip()
    if not raw or raw.startswith("ERROR:") or raw.startswith("No results"):
        return text
    # Preserve fallback notes after the hit list.
    note = ""
    main = raw
    for sep in ("\n\n(note:", "\n\nSummary:\n", "\n\nDDG fallback:"):
        idx = main.find(sep)
        if idx > 0:
            # Keep Summary with the hits (useful); peel only (note: / DDG.
            if sep.startswith("\n\n(note:") or sep.startswith("\n\nDDG"):
                note = main[idx:]
                main = main[:idx]
            break
    citations = parse_search_hits(main)
    if not citations:
        return text
    body = format_numbered_hits(citations)
    # Re-attach Summary block if it was inside main.
    if "\n\nSummary:\n" in raw and "\n\nSummary:\n" not in body:
        sum_idx = raw.find("\n\nSummary:\n")
        if sum_idx > 0:
            # Summary may sit after hits; keep a short version.
            summary = raw[sum_idx : sum_idx + 2800]
            # Avoid double-appending if note already carved wrong.
            if summary.strip() and summary not in body:
                body = body + summary
    if note and note not in body:
        body = body + note
    return body + sources_marker(citations)


def attach_sources(
    payload: dict[str, Any],
    citations: list[Citation],
    *,
    max_n: int = 20,
) -> dict[str, Any]:
    out = dict(payload)
    merged = merge_citations(citations, max_n=max_n)
    out["sources"] = merged
    return out


def citations_from_tool_result(tool: str, content: str) -> list[Citation]:
    name = (tool or "").strip()
    body = content or ""
    if name not in WEB_CITE_TOOLS and not name.startswith("browser_"):
        return []
    if name in {"web_search"}:
        return parse_search_hits(body)
    if name == "parallel_web_search":
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return parse_search_hits(body)
        if isinstance(data, dict):
            if isinstance(data.get("sources"), list):
                return merge_citations(
                    [x for x in data["sources"] if isinstance(x, dict)]
                )
            acc: list[Citation] = []
            for row in data.get("searches") or []:
                if isinstance(row, dict):
                    acc.extend(parse_search_hits(str(row.get("results") or "")))
            return merge_citations(acc)
        return []
    if name in {"parallel_web_fetch", "headless_fetch"}:
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, dict):
            return []
        if isinstance(data.get("sources"), list):
            return merge_citations([x for x in data["sources"] if isinstance(x, dict)])
        pages = data.get("pages")
        if isinstance(pages, list):
            return citations_from_pages(pages)
        return []
    if name == "web_fetch":
        c = parse_fetch_citation(body)
        return [c] if c else []
    if name == "research_run":
        # Prefer structured marker / Sources section; else URLs from pages.
        marked = extract_sources_marker(body)
        if marked:
            return marked
        from_section = parse_sources_section(body)
        if from_section:
            return from_section
        # Collect page headings ### https://...
        acc: list[Citation] = []
        for m in re.finditer(r"(?m)^###\s+(https?://\S+)", body):
            c = make_citation(cid=len(acc) + 1, url=m.group(1))
            if c:
                acc.append(c)
        if acc:
            return merge_citations(acc)
        return parse_search_hits(body)
    # browser_* — pull current URL if present.
    c = parse_fetch_citation(body)
    if c:
        return [c]
    urls = _URL_RE.findall(body)
    return merge_citations(
        [make_citation(cid=i + 1, url=u) for i, u in enumerate(urls[:8])]
    )


def extract_sources_marker(text: str) -> list[Citation]:
    m = _MARKER_RE.search(text or "")
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return merge_citations([x for x in data if isinstance(x, dict)])


def strip_sources_marker(text: str) -> str:
    return _MARKER_RE.sub("", text or "").rstrip()


def parse_sources_section(text: str) -> list[Citation]:
    m = _SOURCES_HEADING_RE.search(text or "")
    if not m:
        return []
    block = (text or "")[m.end() :]
    # Stop at next heading.
    next_h = re.search(r"(?m)^##\s+\S", block)
    if next_h:
        block = block[: next_h.start()]
    acc: list[Citation] = []
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        cid_m = re.match(r"^\[(\d+)\]\s*(.+)$", line)
        if not cid_m:
            url_m = _URL_RE.search(line)
            if url_m:
                c = make_citation(cid=len(acc) + 1, url=url_m.group(0), title=line)
                if c:
                    acc.append(c)
            continue
        cid, rest = cid_m.group(1), cid_m.group(2)
        link_m = re.match(r"\[([^\]]+)\]\((https?://[^)]+)\)", rest)
        if link_m:
            c = make_citation(cid=cid, url=link_m.group(2), title=link_m.group(1))
        else:
            url_m = _URL_RE.search(rest)
            if not url_m:
                continue
            title = rest.replace(url_m.group(0), "").strip(" -—|")
            c = make_citation(cid=cid, url=url_m.group(0), title=title)
        if c:
            acc.append(c)
    return merge_citations(acc)


def collect_citations_from_messages(
    messages: list[Any],
    *,
    start: int = 0,
) -> list[Citation]:
    """Gather citations from tool-role messages in a chat history slice."""
    acc: list[Citation] = []
    for msg in messages[start:]:
        role = getattr(msg, "role", None) or (
            msg.get("role") if isinstance(msg, dict) else None
        )
        if role != "tool":
            continue
        name = getattr(msg, "name", None) or (
            msg.get("name") if isinstance(msg, dict) else None
        )
        content = getattr(msg, "content", None) or (
            msg.get("content") if isinstance(msg, dict) else None
        )
        if not name:
            continue
        acc.extend(citations_from_tool_result(str(name), str(content or "")))
    return merge_citations(acc)


def answer_has_sources(text: str) -> bool:
    body = text or ""
    if _SOURCES_HEADING_RE.search(body):
        return True
    if extract_sources_marker(body):
        return True
    # Inline markers plus at least one URL elsewhere.
    if _INLINE_CITE_RE.search(body) and _URL_RE.search(body):
        return True
    return False


def ensure_cited_answer(answer: str, citations: list[Citation]) -> str:
    """Append a Sources section when web evidence exists but answer omitted it."""
    text = (answer or "").rstrip()
    cites = merge_citations(citations)
    if not cites or not text:
        return answer
    if answer_has_sources(text):
        # Still attach marker for WebUI if missing.
        if not extract_sources_marker(text):
            return text + sources_marker(cites)
        return text
    section = format_sources_section(cites)
    return f"{text}\n\n{section}{sources_marker(cites)}"


def citations_for_display(text: str) -> tuple[str, list[Citation]]:
    """Split display text from structured sources (marker and/or ## Sources)."""
    body = strip_sources_marker(text or "")
    cites = extract_sources_marker(text or "") or parse_sources_section(body)
    return body, cites
