"""Citation contract: parse/normalize + research/search wrappers expose sources."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.builtin import register
from kageha.harness.tools.research import register_research_tools
from kageha.models.base import ChatMessage
from kageha.models.registry import ModelRegistry
from kageha.models.router import ModelRouter
from kageha.research.backend import ResearchBackend
from kageha.research.cache import TtlCache
from kageha.research.citations import (
    citations_from_tool_result,
    collect_citations_from_messages,
    ensure_cited_answer,
    format_sources_section,
    merge_citations,
    normalize_search_output,
    parse_fetch_citation,
    parse_search_hits,
    strip_sources_marker,
)


def _ctx(tmp_path: Path) -> HarnessContext:
    root = tmp_path / "session"
    root.mkdir(parents=True, exist_ok=True)
    ws = SessionWorkspace(run_id="t", root=root)
    return HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )


def test_parse_bullet_and_numbered_hits() -> None:
    bullet = (
        "- Coaching Paper\n"
        "  https://arxiv.org/abs/2405.15250\n"
        "  An LLM-powered coaching study.\n"
        "- Other\n"
        "  https://example.com/b\n"
        "  more"
    )
    cites = parse_search_hits(bullet)
    assert len(cites) == 2
    assert cites[0]["id"] == "1"
    assert "arxiv.org" in cites[0]["url"]
    assert "Coaching" in cites[0]["title"]
    assert cites[0].get("snippet")

    numbered = normalize_search_output(bullet)
    assert numbered.startswith("[1] ")
    assert "https://arxiv.org/abs/2405.15250" in numbered
    assert "<!--kageha:sources" in numbered
    again = parse_search_hits(numbered)
    assert again[0]["url"] == cites[0]["url"]


def test_normalize_preserves_errors() -> None:
    assert normalize_search_output("ERROR: boom").startswith("ERROR:")
    assert "No results" in normalize_search_output("No results (DuckDuckGo blocked)")


def test_parse_fetch_and_ensure_answer() -> None:
    fetch = (
        "title: Docs\n"
        "url: https://docs.example/api\n"
        "status: 200\n"
        "mode: extract\n\n"
        "Body text about the API."
    )
    c = parse_fetch_citation(fetch)
    assert c is not None
    assert c["url"] == "https://docs.example/api"
    assert c["title"] == "Docs"

    answer = ensure_cited_answer("The API supports X [1].", [c])
    assert "## Sources" in answer
    assert "docs.example/api" in answer
    # Idempotent when Sources already present.
    again = ensure_cited_answer(strip_sources_marker(answer), [c])
    assert again.count("## Sources") == 1


def test_merge_dedupes_urls() -> None:
    merged = merge_citations(
        [
            {"id": "9", "url": "https://a.example/", "title": "A"},
            {"id": "2", "url": "https://a.example/", "title": "A dup"},
            {"id": "3", "url": "https://b.example/", "title": "B"},
        ]
    )
    assert [c["url"] for c in merged] == [
        "https://a.example/",
        "https://b.example/",
    ]
    assert merged[0]["id"] == "1"


def test_collect_from_tool_messages() -> None:
    search = normalize_search_output(
        "- Hit\n  https://news.example/story\n  blurb"
    )
    msgs = [
        ChatMessage(role="user", content="q"),
        ChatMessage(role="tool", name="web_search", content=search),
        ChatMessage(
            role="tool",
            name="web_fetch",
            content="title: Story\nurl: https://news.example/story\n\nfull",
        ),
    ]
    cites = collect_citations_from_messages(msgs)
    assert len(cites) == 1
    assert cites[0]["url"] == "https://news.example/story"


def test_web_search_exposes_numbered_sources(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("KAGEHA_WEB_SEARCH", "ddg")
    ws = SessionWorkspace.create("ws-cite")
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )
    reg = register(ctx)
    tool = reg.get("web_search")
    with patch(
        "kageha.harness.tools.builtin._ddg_search",
        new=AsyncMock(
            return_value="- Cite Me\n  https://cite.example/\n  snippet text"
        ),
    ):
        out = asyncio.run(tool.call(query="citation test"))
    assert out.startswith("[1] ")
    assert "cite.example" in out
    cites = citations_from_tool_result("web_search", out)
    assert cites and cites[0]["url"] == "https://cite.example/"


def test_parallel_web_search_sources_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ws = SessionWorkspace.create("ws-pws")
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )
    reg = register(ctx)
    tool = reg.get("parallel_web_search")

    async def fake_search(query: str) -> str:
        return normalize_search_output(
            f"- {query}\n  https://ex.example/{query}\n  snip"
        )

    with patch(
        "kageha.harness.tools.builtin._web_search", side_effect=fake_search
    ):
        out = asyncio.run(
            tool.call(queries_json='["alpha", "beta"]')
        )
    data = json.loads(out)
    assert data["ok"] is True
    assert isinstance(data.get("sources"), list)
    assert len(data["sources"]) >= 2
    assert all("url" in s and "id" in s for s in data["sources"])


def test_parallel_web_fetch_sources_key(tmp_path, monkeypatch) -> None:
    import kageha.research.backend as be

    async def fake_fetch(url: str, max_chars: int = 12000, **kwargs):  # noqa: ANN003
        return f"title: T\nurl: {url}\nmode: extract\n\nhello from {url}"

    monkeypatch.setattr(be, "fetch_url", fake_fetch)
    reg = register_research_tools(_ctx(tmp_path))

    async def _run() -> str:
        return await reg.get("parallel_web_fetch").call(
            urls_json=json.dumps(["https://a.example/", "https://b.example/"])
        )

    out = asyncio.run(_run())
    data = json.loads(out)
    assert data["ok"] is True
    assert len(data["sources"]) == 2
    assert data["sources"][0]["url"] == "https://a.example/"


def test_research_run_includes_sources_section(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KAGEHA_RESEARCH_CACHE_TTL", "0")
    backend = ResearchBackend(
        search_cache=TtlCache(ttl_s=0), fetch_cache=TtlCache(ttl_s=0)
    )

    async def fake_search(q: str) -> str:
        return normalize_search_output(
            f"- Result — https://example.com/{q.replace(' ', '-')}\n  snip"
        )

    async def fake_fetch(url: str, max_chars: int = 8000) -> str:
        return f"title: Page\nurl: {url}\nmode: extract\n\n" + ("content " * 100)

    monkeypatch.setattr(backend, "search_one", fake_search)
    monkeypatch.setattr(backend, "fetch_one", fake_fetch)

    out = asyncio.run(backend.run("lightpanda browser", depth="flash", max_urls=3))
    assert "## Sources" in out
    assert "https://example.com/" in out
    section = format_sources_section(
        citations_from_tool_result("research_run", out)
    )
    assert section.startswith("## Sources")
