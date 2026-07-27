"""Tests for blink-speed research backend."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.research import register_research_tools
from kageha.research.backend import ResearchBackend, _extract_urls_from_search, _is_thin
from kageha.research.cache import TtlCache


def _ctx(tmp_path: Path) -> HarnessContext:
    root = tmp_path / "session"
    root.mkdir(parents=True, exist_ok=True)
    ws = SessionWorkspace(run_id="t", root=root)
    return HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )


def test_research_tools_registered(tmp_path: Path) -> None:
    reg = register_research_tools(_ctx(tmp_path))
    names = set(reg.names())
    assert {"research_run", "parallel_web_fetch", "headless_fetch"} <= names


def test_extract_urls_from_search() -> None:
    text = "See https://example.com/a and https://example.com/a again https://docs.python.org/3/"
    urls = _extract_urls_from_search(text)
    assert urls[0] == "https://example.com/a"
    assert "https://docs.python.org/3/" in urls


def test_is_thin() -> None:
    assert _is_thin("ERROR: nope")
    assert _is_thin("title: x\nurl: y\n\nshort")
    assert not _is_thin("title: x\n\n" + ("word " * 200))


def test_ttl_cache() -> None:
    c = TtlCache(max_entries=2, ttl_s=60)
    c.set("a", 1)
    c.set("b", 2)
    assert c.get("a") == 1
    c.set("c", 3)  # evicts oldest if not moved — 'b' may go
    assert c.get("c") == 3


def test_research_run_flash_mocked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KAGEHA_RESEARCH_CACHE_TTL", "0")
    backend = ResearchBackend(search_cache=TtlCache(ttl_s=0), fetch_cache=TtlCache(ttl_s=0))

    async def fake_search(q: str) -> str:
        return f"1. Result — https://example.com/{q.replace(' ', '-')}\nSnippet about {q}"

    async def fake_fetch(url: str, max_chars: int = 8000) -> str:
        return f"title: Page\nurl: {url}\nmode: extract\n\n" + ("content " * 100)

    monkeypatch.setattr(backend, "search_one", fake_search)
    monkeypatch.setattr(backend, "fetch_one", fake_fetch)

    out = asyncio.run(backend.run("lightpanda browser", depth="flash", max_urls=3))
    assert "# Research (flash)" in out
    assert "https://example.com/" in out
    assert "Pages (HTTP extract)" in out
    assert "headless_enriched: 0" in out


def test_parallel_web_fetch_tool(tmp_path: Path, monkeypatch) -> None:
    import kageha.harness.browser.fetch as fetch_mod

    async def fake_fetch(url: str, max_chars: int = 12000, **kwargs):  # noqa: ANN003
        return f"title: T\nurl: {url}\nmode: extract\n\nhello from {url}"

    monkeypatch.setattr(fetch_mod, "fetch_url", fake_fetch)
    # Also patch where backend imports it at call time — backend imports fetch_url at module level
    import kageha.research.backend as be

    monkeypatch.setattr(be, "fetch_url", fake_fetch)

    reg = register_research_tools(_ctx(tmp_path))

    async def _run() -> str:
        return await reg.get("parallel_web_fetch").call(
            urls_json=json.dumps(["https://a.example/", "https://b.example/"])
        )

    out = asyncio.run(_run())
    data = json.loads(out)
    assert data["ok"] is True
    assert len(data["pages"]) == 2


def test_research_pack_in_core() -> None:
    from kageha.harness.tool_packs import CORE_PACK_NAMES

    assert "research" in CORE_PACK_NAMES
