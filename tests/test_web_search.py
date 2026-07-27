"""web_search parsing helpers (offline HTML fixtures)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.builtin import register
from kageha.models.registry import ModelRegistry
from kageha.models.router import ModelRouter


HTML_FIXTURE = """
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Farxiv.org%2Fabs%2F2405.15250">
Coaching Copilot Paper
</a>
<td class="result__snippet">An LLM-powered coaching study.</td>
"""


def test_web_search_parses_ddg_html(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("KAGEHA_WEB_SEARCH", "ddg")
    ws = SessionWorkspace.create("ws")
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )
    reg = register(ctx)
    tool = reg.get("web_search")
    assert tool is not None

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = HTML_FIXTURE
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        out = asyncio.run(tool.call(query="LLM coaching"))

    assert "Coaching Copilot" in out
    assert "arxiv.org/abs/2405.15250" in out


def test_web_search_uses_gemini_then_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("KAGEHA_WEB_SEARCH", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    ws = SessionWorkspace.create("ws-gem")
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )
    reg = register(ctx)
    tool = reg.get("web_search")
    assert tool is not None

    with (
        patch(
            "kageha.harness.tools.builtin._gemini_web_search",
            new=AsyncMock(return_value="ERROR: Gemini down"),
        ),
        patch(
            "kageha.harness.tools.builtin._ddg_search",
            new=AsyncMock(return_value="- ddg hit\n  https://example.com"),
        ) as ddg,
    ):
        out = asyncio.run(tool.call(query="bedrock"))
    assert "ddg hit" in out
    ddg.assert_awaited_once()


def test_web_search_uses_brave_when_key(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("KAGEHA_WEB_SEARCH", "auto")
    monkeypatch.setenv("BRAVE_API_KEY", "brave-test-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    ws = SessionWorkspace.create("ws-brave")
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )
    reg = register(ctx)
    tool = reg.get("web_search")

    with (
        patch(
            "kageha.harness.tools.builtin._brave_web_search",
            new=AsyncMock(
                return_value="- Brave Hit\n  https://brave.example\n  snippet"
            ),
        ) as brave,
        patch(
            "kageha.harness.tools.builtin._gemini_web_search",
            new=AsyncMock(return_value="- should not use"),
        ) as gem,
        patch(
            "kageha.harness.tools.builtin._ddg_search",
            new=AsyncMock(return_value="- should not use"),
        ) as ddg,
    ):
        out = asyncio.run(tool.call(query="privacy search"))
    assert "Brave Hit" in out
    brave.assert_awaited_once()
    gem.assert_not_awaited()
    ddg.assert_not_awaited()


def test_brave_search_parses_api_json(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    monkeypatch.setenv("KAGEHA_WEB_SEARCH", "brave")
    payload = {
        "web": {
            "results": [
                {
                    "title": "Brave Docs",
                    "url": "https://api.search.brave.com/app/documentation",
                    "description": "Search API docs",
                }
            ]
        }
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value=payload)
    mock_resp.text = ""
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get = AsyncMock(return_value=mock_resp)

    from kageha.harness.tools.builtin import _brave_web_search

    with patch("httpx.AsyncClient", return_value=mock_client):
        out = asyncio.run(_brave_web_search("brave api"))
    assert "Brave Docs" in out
    assert "api.search.brave.com" in out


def test_web_search_backend_auto_picks_brave(monkeypatch):
    from kageha.config import web_search_backend

    monkeypatch.setenv("KAGEHA_WEB_SEARCH", "auto")
    monkeypatch.setenv("BRAVE_API_KEY", "x")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    assert web_search_backend() == "brave"
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    assert web_search_backend() == "gemini"


def test_web_search_backend_auto_picks_tavily_then_perplexity(monkeypatch):
    from kageha.config import web_search_backend

    monkeypatch.setenv("KAGEHA_WEB_SEARCH", "auto")
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    assert web_search_backend() == "tavily"
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-test")
    assert web_search_backend() == "perplexity"
    monkeypatch.setenv("KAGEHA_WEB_SEARCH", "tavily")
    assert web_search_backend() == "tavily"
    monkeypatch.setenv("KAGEHA_WEB_SEARCH", "perplexity")
    assert web_search_backend() == "perplexity"


def test_tavily_search_parses_api_json(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-k")
    payload = {
        "results": [
            {
                "title": "Tavily Docs",
                "url": "https://docs.tavily.com/",
                "content": "Tavily Search API",
            }
        ],
        "answer": "Tavily is a search API for agents.",
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value=payload)
    mock_resp.text = ""
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=mock_resp)

    from kageha.harness.tools.builtin import _tavily_web_search

    with patch("httpx.AsyncClient", return_value=mock_client):
        out = asyncio.run(_tavily_web_search("tavily api"))
    assert "Tavily Docs" in out
    assert "docs.tavily.com" in out
    assert "Summary:" in out
    mock_client.post.assert_awaited_once()
    call_kwargs = mock_client.post.await_args.kwargs
    assert "Authorization" in call_kwargs["headers"]
    assert call_kwargs["json"]["query"] == "tavily api"


def test_perplexity_search_parses_api_json(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-k")
    payload = {
        "results": [
            {
                "title": "Perplexity Search",
                "url": "https://docs.perplexity.ai/api-reference/search-post",
                "snippet": "Structured web results",
            }
        ],
        "id": "test-id",
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value=payload)
    mock_resp.text = ""
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=mock_resp)

    from kageha.harness.tools.builtin import _perplexity_web_search

    with patch("httpx.AsyncClient", return_value=mock_client):
        out = asyncio.run(_perplexity_web_search("perplexity search"))
    assert "Perplexity Search" in out
    assert "docs.perplexity.ai" in out
    mock_client.post.assert_awaited_once()
    assert mock_client.post.await_args.args[0] == "https://api.perplexity.ai/search"


def test_web_search_uses_tavily_when_key(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("KAGEHA_WEB_SEARCH", "auto")
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    ws = SessionWorkspace.create("ws-tavily")
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )
    reg = register(ctx)
    tool = reg.get("web_search")

    with (
        patch(
            "kageha.harness.tools.builtin._tavily_web_search",
            new=AsyncMock(
                return_value="- Tavily Hit\n  https://tavily.example\n  snippet"
            ),
        ) as tavily,
        patch(
            "kageha.harness.tools.builtin._brave_web_search",
            new=AsyncMock(return_value="- should not use"),
        ) as brave,
        patch(
            "kageha.harness.tools.builtin._gemini_web_search",
            new=AsyncMock(return_value="- should not use"),
        ) as gem,
        patch(
            "kageha.harness.tools.builtin._ddg_search",
            new=AsyncMock(return_value="- should not use"),
        ) as ddg,
    ):
        out = asyncio.run(tool.call(query="agent search"))
    assert "Tavily Hit" in out
    tavily.assert_awaited_once()
    brave.assert_not_awaited()
    gem.assert_not_awaited()
    ddg.assert_not_awaited()


def test_web_search_brave_falls_back_to_tavily(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("KAGEHA_WEB_SEARCH", "brave")
    monkeypatch.setenv("BRAVE_API_KEY", "brave-k")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-k")
    ws = SessionWorkspace.create("ws-brave-tavily")
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )
    reg = register(ctx)
    tool = reg.get("web_search")

    with (
        patch(
            "kageha.harness.tools.builtin._brave_web_search",
            new=AsyncMock(return_value="ERROR: Brave down"),
        ),
        patch(
            "kageha.harness.tools.builtin._tavily_web_search",
            new=AsyncMock(return_value="- Tavily rescue\n  https://tavily.example"),
        ) as tavily,
        patch(
            "kageha.harness.tools.builtin._gemini_web_search",
            new=AsyncMock(return_value="- should not use"),
        ) as gem,
        patch(
            "kageha.harness.tools.builtin._ddg_search",
            new=AsyncMock(return_value="- should not use"),
        ) as ddg,
    ):
        out = asyncio.run(tool.call(query="fallback"))
    assert "Tavily rescue" in out
    assert "Brave down" in out
    tavily.assert_awaited_once()
    gem.assert_not_awaited()
    ddg.assert_not_awaited()


def test_web_search_uses_perplexity_when_pinned(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("KAGEHA_WEB_SEARCH", "perplexity")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-test-key")
    ws = SessionWorkspace.create("ws-pplx")
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )
    reg = register(ctx)
    tool = reg.get("web_search")

    with (
        patch(
            "kageha.harness.tools.builtin._perplexity_web_search",
            new=AsyncMock(
                return_value="- Pplx Hit\n  https://perplexity.example\n  snippet"
            ),
        ) as pplx,
        patch(
            "kageha.harness.tools.builtin._gemini_web_search",
            new=AsyncMock(return_value="- should not use"),
        ) as gem,
        patch(
            "kageha.harness.tools.builtin._ddg_search",
            new=AsyncMock(return_value="- should not use"),
        ) as ddg,
    ):
        out = asyncio.run(tool.call(query="citations"))
    assert "Pplx Hit" in out
    pplx.assert_awaited_once()
    gem.assert_not_awaited()
    ddg.assert_not_awaited()


def test_web_search_prefers_gemini_when_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("KAGEHA_WEB_SEARCH", "gemini")
    ws = SessionWorkspace.create("ws-gem2")
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )
    reg = register(ctx)
    tool = reg.get("web_search")

    with (
        patch(
            "kageha.harness.tools.builtin._gemini_web_search",
            new=AsyncMock(
                return_value="- Gemini Source\n  https://ai.google.dev\n\nSummary:\nok"
            ),
        ),
        patch(
            "kageha.harness.tools.builtin._ddg_search",
            new=AsyncMock(return_value="- should not use"),
        ) as ddg,
    ):
        out = asyncio.run(tool.call(query="gemini api"))
    assert "Gemini Source" in out
    ddg.assert_not_awaited()


def test_parallel_web_search_fans_out(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("KAGEHA_WEB_SEARCH", "ddg")
    ws = SessionWorkspace.create("ws2")
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )
    reg = register(ctx)
    tool = reg.get("parallel_web_search")
    assert tool is not None

    async def fake_search(query: str) -> str:
        await asyncio.sleep(0.05)
        return f"- hit for {query}"

    with patch("kageha.harness.tools.builtin._web_search", side_effect=fake_search):
        import time

        t0 = time.perf_counter()
        out = asyncio.run(
            tool.call(queries_json='["alpha coaching", "beta coaching"]')
        )
        elapsed = time.perf_counter() - t0

    data = __import__("json").loads(out)
    assert data["ok"] is True
    assert len(data["searches"]) == 2
    assert elapsed < 0.09
