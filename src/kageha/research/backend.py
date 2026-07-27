"""ResearchBackend — blink-speed multi-tier research in one call.

Eliminates the slow LLM loop of search → (think) → fetch → (think) → browse
by doing the fan-out inside the tool.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from kageha.harness.browser.fetch import fetch_url
from kageha.research.cache import FETCH_CACHE, SEARCH_CACHE, TtlCache


_URL_RE = re.compile(r"https?://[^\s\]\)>'\"<>]+", re.I)


def research_depth_default() -> str:
    raw = (os.environ.get("KAGEHA_RESEARCH_DEPTH") or "flash").strip().lower()
    if raw in {"flash", "fast", "quick"}:
        return "flash"
    if raw in {"standard", "std", "headless"}:
        return "standard"
    if raw in {"deep", "browser", "full"}:
        return "deep"
    return "flash"


def research_max_urls() -> int:
    raw = (os.environ.get("KAGEHA_RESEARCH_MAX_URLS") or "5").strip()
    try:
        return max(1, min(10, int(raw)))
    except ValueError:
        return 5


def research_max_queries() -> int:
    raw = (os.environ.get("KAGEHA_RESEARCH_MAX_QUERIES") or "4").strip()
    try:
        return max(1, min(8, int(raw)))
    except ValueError:
        return 4


def _extract_urls_from_search(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for m in _URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(".,;:)")
        # Drop common tracker junk tails
        if u in seen:
            continue
        host = urlparse(u).netloc.lower()
        if not host or (host.endswith("google.com") and "/search" in u):
            continue
        seen.add(u)
        found.append(u)
    return found


def _is_thin(extract: str, *, min_chars: int = 400) -> bool:
    if not extract or extract.startswith("ERROR:"):
        return True
    # Strip headers
    body = extract
    if "\n\n" in extract:
        body = extract.split("\n\n", 1)[-1]
    return len(body.strip()) < min_chars


@dataclass
class ResearchBackend:
    search_cache: TtlCache = field(default_factory=lambda: SEARCH_CACHE)
    fetch_cache: TtlCache = field(default_factory=lambda: FETCH_CACHE)

    async def search_one(self, query: str) -> str:
        from kageha.harness.tools.builtin import _web_search

        q = (query or "").strip()
        if not q:
            return "ERROR: empty query"
        key = self.search_cache.key("search", q.lower())
        hit = self.search_cache.get(key)
        if hit is not None:
            return f"[cache]\n{hit}"
        out = await _web_search(q)
        self.search_cache.set(key, out)
        return out

    async def fetch_one(self, url: str, max_chars: int = 8000) -> str:
        u = (url or "").strip()
        if not u:
            return "ERROR: empty url"
        key = self.fetch_cache.key("fetch", u, str(max_chars))
        hit = self.fetch_cache.get(key)
        if hit is not None:
            return f"[cache]\n{hit}"
        out = await fetch_url(u, max_chars=max_chars)
        if not out.startswith("ERROR:"):
            self.fetch_cache.set(key, out)
        return out

    async def fetch_many(
        self, urls: list[str], *, max_chars: int = 8000
    ) -> list[dict[str, str]]:
        clean = [u.strip() for u in urls if str(u).strip()][: research_max_urls()]

        async def one(u: str) -> dict[str, str]:
            text = await self.fetch_one(u, max_chars=max_chars)
            return {"url": u, "ok": "false" if text.startswith("ERROR:") else "true", "text": text}

        return list(await asyncio.gather(*[one(u) for u in clean]))

    async def headless_enrich(
        self, urls: list[str], *, max_chars: int = 6000
    ) -> list[dict[str, str]]:
        from kageha.research.pool import get_pool, headless_backend

        if headless_backend() == "http":
            return [
                {
                    "url": u,
                    "ok": "false",
                    "error": "headless backend=http",
                    "text": "",
                    "backend": "http",
                }
                for u in urls
            ]
        pool = await get_pool()
        return await pool.extract_urls(urls, max_chars=max_chars)

    async def run(
        self,
        query: str,
        *,
        depth: str | None = None,
        max_urls: int | None = None,
        queries_json: str = "",
        max_chars: int = 5000,
    ) -> str:
        """One-shot research. depth=flash|standard|deep."""
        q = (query or "").strip()
        if not q:
            return "ERROR: query is required"

        depth_n = (depth or research_depth_default()).strip().lower()
        if depth_n in {"fast", "quick"}:
            depth_n = "flash"
        if depth_n in {"std", "headless"}:
            depth_n = "standard"
        if depth_n in {"browser", "full"}:
            depth_n = "deep"
        if depth_n not in {"flash", "standard", "deep"}:
            depth_n = "flash"

        n_urls = max_urls if max_urls is not None else research_max_urls()
        n_urls = max(1, min(10, int(n_urls)))

        # Build query fan-out
        queries: list[str] = [q]
        if queries_json.strip():
            try:
                extra = json.loads(queries_json)
                if isinstance(extra, list):
                    for item in extra:
                        s = str(item).strip()
                        if s and s not in queries:
                            queries.append(s)
            except json.JSONDecodeError:
                pass
        # Auto-expand a couple of angles for blink coverage (cheap).
        if len(queries) == 1:
            queries.append(f"{q} overview")
            queries.append(f"{q} latest")
        queries = queries[: research_max_queries()]

        search_results = await asyncio.gather(*[self.search_one(qq) for qq in queries])

        urls: list[str] = []
        seen: set[str] = set()
        for block in search_results:
            for u in _extract_urls_from_search(block):
                if u in seen:
                    continue
                seen.add(u)
                urls.append(u)
                if len(urls) >= n_urls:
                    break
            if len(urls) >= n_urls:
                break

        fetches = await self.fetch_many(urls, max_chars=max_chars) if urls else []

        # standard/deep: headless enrich thin pages
        enriched: list[dict[str, str]] = []
        thin_urls = [
            f["url"]
            for f in fetches
            if _is_thin(f.get("text") or "")
        ]
        if depth_n in {"standard", "deep"} and thin_urls:
            try:
                enriched = await self.headless_enrich(thin_urls, max_chars=max_chars)
            except Exception as e:  # noqa: BLE001
                enriched = [
                    {
                        "url": u,
                        "ok": "false",
                        "error": str(e),
                        "text": "",
                        "backend": "error",
                    }
                    for u in thin_urls
                ]

        # deep: hint that interactive browser pack is available (don't block blink path)
        deep_note = ""
        if depth_n == "deep":
            deep_note = (
                "\n## Deep mode\n"
                "Interactive control: enable browser pack "
                "(`KAGEHA_TOOL_PACKS=browser`) then use browser_connect / "
                "browser_open / browser_snapshot / browser_click. "
                "For logged-in pages: browser_connect(target='comet').\n"
            )

        return self._format(
            query=q,
            depth=depth_n,
            queries=queries,
            search_results=list(search_results),
            fetches=fetches,
            enriched=enriched,
            deep_note=deep_note,
        )

    def _format(
        self,
        *,
        query: str,
        depth: str,
        queries: list[str],
        search_results: list[str],
        fetches: list[dict[str, str]],
        enriched: list[dict[str, str]],
        deep_note: str,
    ) -> str:
        lines: list[str] = [
            f"# Research ({depth})",
            f"query: {query}",
            f"queries: {json.dumps(queries)}",
            f"sources_fetched: {len(fetches)}",
            f"headless_enriched: {len(enriched)}",
            "",
            "## Search",
        ]
        for i, (qq, block) in enumerate(zip(queries, search_results), 1):
            snippet = (block or "")[:1800]
            lines.append(f"### Q{i}: {qq}")
            lines.append(snippet)
            lines.append("")

        if fetches:
            lines.append("## Pages (HTTP extract)")
            for f in fetches:
                text = (f.get("text") or "")[:4500]
                lines.append(f"### {f.get('url')}")
                lines.append(text)
                lines.append("")

        if enriched:
            lines.append("## Pages (headless JS extract)")
            for e in enriched:
                status = "ok" if e.get("ok") == "true" else f"fail:{e.get('error', '')}"
                lines.append(
                    f"### {e.get('url')} [{status}] backend={e.get('backend', '?')}"
                )
                title = e.get("title") or ""
                if title:
                    lines.append(f"title: {title}")
                body = (e.get("text") or "")[:4500]
                if body:
                    lines.append(body)
                lines.append("")

        if deep_note:
            lines.append(deep_note.strip())

        from kageha.research.citations import (
            citations_from_pages,
            format_sources_section,
            merge_citations,
            parse_search_hits,
            sources_marker,
        )

        page_cites = citations_from_pages(fetches)
        if enriched:
            page_cites = merge_citations(page_cites + citations_from_pages(enriched))
        search_cites: list = []
        for block in search_results:
            search_cites.extend(parse_search_hits(block or ""))
        # Prefer fetched pages; fill gaps from search hits.
        sources = merge_citations(page_cites + search_cites, max_n=12)
        if sources:
            lines.append("")
            lines.append(format_sources_section(sources))

        lines.append("")
        lines.append(
            "## Next\n"
            "Synthesize a brief with inline citations [1], [2], … matching ## Sources. "
            "Never invent URLs. Only open browser_* if a source needs login or interaction."
        )
        out = "\n".join(lines)
        if sources:
            out = out + sources_marker(sources)
        # Cap tool payload so the model stays fast.
        return out[:24000]


_DEFAULT = ResearchBackend()


async def research_run(
    query: str,
    depth: str = "",
    max_urls: int = 0,
    queries_json: str = "",
    max_chars: int = 5000,
) -> str:
    return await _DEFAULT.run(
        query,
        depth=depth or None,
        max_urls=max_urls or None,
        queries_json=queries_json,
        max_chars=max_chars,
    )
