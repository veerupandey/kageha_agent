"""Tests for tiered web_fetch (no Chromium)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx

from kageha.harness.approvals import ApprovalGate
from kageha.harness.browser.fetch import fetch_url
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.builtin import register

_HTML = """<!DOCTYPE html><html><head><title>Doc Title</title></head>
<body><nav>skip</nav><article><h1>Hello</h1><p>Main content here.</p>
<a href="/more">More</a></article>
<script>evil()</script></body></html>"""


def test_fetch_url_extracts_html(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *a, **k):  # noqa: ANN001, ARG002
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):  # noqa: ANN001, ARG002
            return False

        async def get(self, url: str):  # noqa: ARG002
            class Resp:
                status_code = 200
                headers = {"content-type": "text/html"}
                text = _HTML

                @property
                def url(self):
                    return "https://example.com/doc"

            return Resp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    out = asyncio.run(fetch_url("https://example.com/doc"))
    assert "Doc Title" in out
    assert "Main content" in out
    assert "evil()" not in out
    assert "More" in out
    assert "mode: extract" in out


def test_fetch_url_rejects_non_http() -> None:
    out = asyncio.run(fetch_url("file:///tmp/x"))
    assert out.startswith("ERROR:")


def test_web_fetch_tool_registered(tmp_path: Path) -> None:
    root = tmp_path / "session"
    root.mkdir()
    ws = SessionWorkspace(run_id="t", root=root)
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )
    reg = register(ctx)
    assert "web_fetch" in reg.names()
