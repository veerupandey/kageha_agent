"""WebUI + CLI parity for /browser and /research."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from kageha.app_server import AppServer
from kageha.webui.server import WebUIApp


@pytest.fixture()
def webui_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WebUIApp:
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KAGEHA_BROWSER_PACK", "0")
    app = WebUIApp(AppServer())
    yield app
    app.close()


def _call(
    app: WebUIApp,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    query: dict[str, list[str]] | None = None,
) -> tuple[int, Any]:
    import json

    raw = json.dumps(body or {}).encode() if body is not None else b""
    st, data, _ctype = app.handle(method, path, query or {}, raw)
    payload = json.loads(data.decode()) if data else {}
    return st, payload


def test_api_browser_get_lists_backends(webui_app: WebUIApp) -> None:
    st, data = _call(webui_app, "GET", "/api/browser")
    assert st == 200
    assert data.get("ok") is True
    ids = {b["id"] for b in data.get("backends") or []}
    assert {"http", "lightpanda", "comet", "chromium", "headless"} <= ids
    assert "status" in data


def test_api_browser_post_use_backend(webui_app: WebUIApp, tmp_path: Path) -> None:
    st, data = _call(
        webui_app,
        "POST",
        "/api/browser",
        body={"backend": "lightpanda"},
    )
    assert st == 200
    assert data.get("ok") is True
    assert "lightpanda" in (data.get("message") or "")
    prefs_path = tmp_path / "home" / "browser.json"
    assert prefs_path.is_file()


def test_api_comet_get_status(webui_app: WebUIApp, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "kageha.chat.comet.ensure_comet",
        AsyncMock(return_value="Comet is not reachable at http://127.0.0.1:9222"),
    )
    st, data = _call(webui_app, "GET", "/api/comet")
    assert st == 200
    assert data.get("ok") is True
    assert "Comet" in (data.get("message") or "")


def test_api_comet_post_start(webui_app: WebUIApp, monkeypatch: pytest.MonkeyPatch) -> None:
    mock = AsyncMock(return_value="Comet ready at http://127.0.0.1:9222")
    monkeypatch.setattr("kageha.chat.comet.ensure_comet", mock)
    st, data = _call(webui_app, "POST", "/api/comet", body={"action": "start"})
    assert st == 200
    assert data.get("ok") is True
    assert data.get("action") == "start"
    assert "Comet" in (data.get("message") or "")
    mock.assert_awaited_once()
    assert mock.await_args.kwargs.get("launch") is True


def test_api_slash_catalog(webui_app: WebUIApp) -> None:
    st, data = _call(webui_app, "GET", "/api/slash-catalog")
    assert st == 200
    assert data.get("ok") is True
    cmds = data.get("commands") or []
    ids = {c.get("id") for c in cmds}
    assert {"plan", "goal", "normal", "new", "ask", "auto"} <= ids
    assert "task" not in ids
    assert "comet" not in ids  # canonical command is /browser comet
    assert "permissions" in ids
    assert "permissions-ask" in ids
    assert "browser-diagnose" in ids
    diagnose = next(c for c in data["commands"] if c["id"] == "browser-diagnose")
    assert diagnose["usage"] == "/browser diagnose <url>"
    assert "best-of-n" not in ids
    assert "labs" not in ids
    caps = data.get("capabilities") or {}
    assert caps.get("comet") is True
    assert caps.get("browser") is True
    assert caps.get("models") is True
    # Never advertise stub-only entries without a handler surface.
    assert all(c.get("label", "").startswith("/") for c in cmds)


def test_chat_slash_browser_bypasses_agent(
    webui_app: WebUIApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = {"rpc": False}

    def boom(*a, **k):  # noqa: ANN001, ANN003
        called["rpc"] = True
        raise AssertionError("agent loop should not run for /browser")

    monkeypatch.setattr(webui_app, "rpc", boom)
    st, data = _call(
        webui_app,
        "POST",
        "/api/chat",
        body={"message": "/browser list", "thread_id": "t1"},
    )
    assert st == 200
    assert data.get("quick") is True
    assert "lightpanda" in (data.get("message") or "")
    assert called["rpc"] is False


def test_chat_slash_research_bypasses_agent(
    webui_app: WebUIApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run(query: str, depth: str = "", **kwargs):  # noqa: ANN003
        return f"# Research ({depth or 'flash'})\nquery: {query}"

    monkeypatch.setattr("kageha.research.backend.research_run", fake_run)

    def boom(*a, **k):  # noqa: ANN001, ANN003
        raise AssertionError("agent loop should not run for /research")

    monkeypatch.setattr(webui_app, "rpc", boom)
    st, data = _call(
        webui_app,
        "POST",
        "/api/chat",
        body={"message": "/research flash what is kageha", "thread_id": "t1"},
    )
    assert st == 200
    assert data.get("quick") is True
    assert "kageha" in (data.get("message") or "")


def test_react_slash_wires_browser_handler() -> None:
    """React frontend owns /browser slash handling (legacy app.js removed)."""
    root = Path(__file__).resolve().parents[1]
    slash = (root / "src/kageha/webui/frontend/src/lib/slash.ts").read_text(encoding="utf-8")
    catalog = (root / "src/kageha/webui/frontend/src/api/slashCatalog.ts").read_text(
        encoding="utf-8"
    )
    assert "postBrowser" in slash
    assert "/api/browser" in slash
    assert 'label: "/browser"' in catalog
    assert 'label: "/research"' in catalog
    assert 'kind: "browser"' in catalog


def test_cli_browser_and_research_help() -> None:
    from typer.testing import CliRunner

    from kageha.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["browser", "--help"])
    assert result.exit_code == 0
    assert "status" in result.stdout
    assert "use" in result.stdout
    assert "research" in result.stdout

    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "depth" in result.stdout or "query" in result.stdout.lower()
