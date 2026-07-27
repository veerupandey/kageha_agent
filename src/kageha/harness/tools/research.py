"""Blink-speed research tools (core pack)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kageha.harness.tools.base import ToolRegistry, tool

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext


def register_research_tools(ctx: "HarnessContext") -> ToolRegistry:
    del ctx  # unused — research is process-global + workspace-agnostic
    reg = ToolRegistry()

    @tool(
        description=(
            "BLINK research: one call does parallel search + parallel page extract. "
            "depth=flash (HTTP only, fastest), standard (flash + warm headless JS for "
            "thin pages; Lightpanda/Chromium pool), deep (same + guidance for browser_* "
            "interactive control). Prefer this over serial web_search→web_fetch loops. "
            "Output includes a ## Sources list — cite claims with [n] in the answer."
        )
    )
    async def research_run(
        query: str,
        depth: str = "flash",
        max_urls: int = 5,
        queries_json: str = "",
        max_chars: int = 5000,
    ) -> str:
        from kageha.research.backend import research_run as _run

        return await _run(
            query,
            depth=depth,
            max_urls=max_urls,
            queries_json=queries_json,
            max_chars=max_chars,
        )

    @tool(
        description=(
            "Fetch many public URLs in parallel (HTTP extract, cached). "
            "urls_json is a JSON array of http(s) URLs (max 10). "
            "Faster than serial web_fetch for multi-source reads. "
            "Returns pages[] plus sources[] citations."
        )
    )
    async def parallel_web_fetch(urls_json: str, max_chars: int = 8000) -> str:
        import json

        from kageha.research.backend import ResearchBackend
        from kageha.research.citations import attach_sources, citations_from_pages

        try:
            urls = json.loads(urls_json or "[]")
        except json.JSONDecodeError as e:
            return f"ERROR: urls_json must be JSON array: {e}"
        if not isinstance(urls, list) or not urls:
            return "ERROR: urls_json must be a non-empty JSON array of URLs"
        backend = ResearchBackend()
        rows = await backend.fetch_many(
            [str(u) for u in urls],
            max_chars=max(500, min(20000, int(max_chars))),
        )
        payload = attach_sources(
            {"ok": True, "pages": rows},
            citations_from_pages(rows),
        )
        return json.dumps(payload, indent=2)[:20000]

    @tool(
        description=(
            "Extract pages via the warm headless pool (Chromium or Lightpanda CDP). "
            "urls_json JSON array. Use when web_fetch is thin/empty but you need JS. "
            "For interactive click/type use browser_* pack instead. "
            "Returns pages[] plus sources[] citations."
        )
    )
    async def headless_fetch(urls_json: str, max_chars: int = 6000) -> str:
        import json

        from kageha.research.backend import ResearchBackend
        from kageha.research.citations import attach_sources, citations_from_pages

        try:
            urls = json.loads(urls_json or "[]")
        except json.JSONDecodeError as e:
            return f"ERROR: urls_json must be JSON array: {e}"
        if not isinstance(urls, list) or not urls:
            return "ERROR: urls_json must be a non-empty JSON array of URLs"
        backend = ResearchBackend()
        try:
            rows = await backend.headless_enrich(
                [str(u) for u in urls],
                max_chars=max(500, min(20000, int(max_chars))),
            )
        except Exception as e:  # noqa: BLE001
            return f"ERROR: headless pool: {e}"
        payload = attach_sources(
            {"ok": True, "pages": rows},
            citations_from_pages(rows),
        )
        return json.dumps(payload, indent=2)[:20000]

    for t in (research_run, parallel_web_fetch, headless_fetch):
        if hasattr(t, "name"):
            reg.register(t)  # type: ignore[arg-type]
    return reg
