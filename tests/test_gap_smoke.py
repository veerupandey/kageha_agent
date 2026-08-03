"""Smoke coverage for previously untested keepable surfaces."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from kageha.cli import app
from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tool_packs import OPTIONAL_PACK_NAMES, resolve_enabled_packs
from kageha.harness.tools.media import register_media_tools
from kageha.harness.tools.paths import rel_to_workspace
from kageha.models.fal import FalClient
from kageha.project.job_worker import main as job_worker_main
from kageha.research.pool import headless_backend, headless_cdp_endpoint


def test_paths_rel_to_workspace(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    dest = root / "artifacts" / "a.png"
    dest.parent.mkdir()
    dest.write_bytes(b"x")
    assert rel_to_workspace(dest, root) == "artifacts/a.png"


def test_media_pack_registers_fal_tools(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("FAL_API_KEY", raising=False)
    monkeypatch.setenv("KAGEHA_TOOL_PACKS", "media")
    assert "media" in OPTIONAL_PACK_NAMES
    assert "media" in resolve_enabled_packs(policy={})

    root = tmp_path / "session"
    root.mkdir()
    (root / "artifacts").mkdir()
    ctx = HarnessContext(
        workspace=SessionWorkspace(run_id="media", root=root),
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )
    reg = register_media_tools(ctx)
    names = set(reg.names())
    assert {
        "fal_generate_image",
        "fal_edit_image",
        "fal_image_to_video",
        "fal_text_to_video",
    } <= names

    async def _missing_key():
        tool = reg.get("fal_generate_image")
        assert tool is not None
        out = await tool.call(prompt="a red cube")
        assert "FAL_KEY" in out

    asyncio.run(_missing_key())


def test_fal_client_allowlist_and_health():
    client = FalClient(api_key="test-key")
    assert client.available

    async def _run():
        health = await client.health()
        assert health["ok"] is True
        with pytest.raises(ValueError, match="allowlisted"):
            await client.run("evil/model", {"prompt": "x"})

    asyncio.run(_run())


@pytest.mark.live_provider
def test_fal_live_auth_and_schnell_image(tmp_path: Path, monkeypatch):
    """Genuinely live: billed Fal auth + cheap flux-schnell still (REL-002, Req 3.3).

    Requires an explicit opt-in (KAGEHA_LIVE_TESTS=1) in addition to configured
    credentials, so ambient FAL_KEY/FAL_API_KEY env vars never trigger an
    implicit billed call during an ordinary test run.
    """
    if os.environ.get("KAGEHA_LIVE_TESTS") != "1":
        pytest.skip("set KAGEHA_LIVE_TESTS=1 for billed provider checks")

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    client = FalClient()
    if not client.available:
        pytest.skip("FAL_KEY / FAL_API_KEY not set")

    async def _live():
        health = await client.health()
        assert health["ok"] is True
        import httpx

        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.get(
                "https://api.fal.ai/v1/models",
                headers={"Authorization": f"Key {client.api_key}"},
                params={"limit": 1},
            )
        assert r.status_code == 200, r.text[:300]

        root = tmp_path / "session"
        root.mkdir()
        (root / "artifacts").mkdir()
        monkeypatch.setenv("FAL_API_KEY", client.api_key)
        ctx = HarnessContext(
            workspace=SessionWorkspace(run_id="fal-live", root=root),
            approvals=ApprovalGate(auto_approve=True),
            router=SimpleNamespace(),
        )
        reg = register_media_tools(ctx)
        tool = reg.get("fal_generate_image")
        assert tool is not None
        out = await tool.call(
            prompt="simple flat red square icon, minimal",
            model="flux-schnell",
            filename="fal_smoke.png",
        )
        assert not out.startswith("ERROR"), out
        data = json.loads(out)
        assert (root / data["path"]).is_file()
        assert (root / data["path"]).stat().st_size > 100

    asyncio.run(_live())


def test_job_worker_usage_and_missing():
    assert job_worker_main([]) == 2
    assert job_worker_main(["missing-job-id-xyz"]) == 1


def test_research_pool_backend_helpers(monkeypatch):
    monkeypatch.setenv("KAGEHA_HEADLESS_BACKEND", "http")
    assert headless_backend() == "http"
    monkeypatch.setenv("KAGEHA_HEADLESS_CDP", "http://127.0.0.1:9333")
    assert headless_cdp_endpoint() == "http://127.0.0.1:9333"


def test_mcp_server_builds_registry():
    from kageha.mcp.server import _build_registry

    async def _go():
        ctx = await _build_registry(auto_approve=True)
        assert ctx.tools is not None
        assert len(ctx.tools.names()) > 5

    asyncio.run(_go())


def test_remote_ping_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    from kageha.app_server_listen import serve_unix
    from kageha.chat.remote_turn import remote_ping

    sock = tmp_path / "kageha.sock"

    async def _go():
        attach = f"unix://{sock}"
        for _ in range(50):
            if sock.exists():
                break
            await asyncio.sleep(0.02)
        pong = await remote_ping(attach)
        assert isinstance(pong, dict)
        assert pong.get("pong") is True

    async def _main():
        task = asyncio.create_task(serve_unix(str(sock)))
        try:
            await _go()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(_main())


def test_remote_turn_parses_thread_result(monkeypatch):
    from kageha.chat import remote_turn as rt

    class _Fake:
        def __init__(self, url: str) -> None:
            self.url = url

        async def handle(self, req: dict):
            method = req.get("method")
            if method == "thread/start":
                return {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
            if method == "thread/turn":
                return {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"answer": "hi", "thread_id": "t1"},
                }
            return {"jsonrpc": "2.0", "id": req.get("id"), "error": "bad"}

        def close(self) -> None:
            return None

    monkeypatch.setattr(rt, "RemoteAppServer", _Fake)
    monkeypatch.setattr(rt, "resolve_attach_url", lambda a: a)

    async def _go():
        out = await rt.remote_turn(attach="unix:///tmp/x.sock", message="hi")
        assert out["answer"] == "hi"
        assert out["thread_id"]

    asyncio.run(_go())


def test_cli_version_and_help():
    runner = CliRunner()
    ver = runner.invoke(app, ["version"])
    assert ver.exit_code == 0
    assert ver.stdout.strip()
    help_out = runner.invoke(app, ["--help"])
    assert help_out.exit_code == 0
    for name in (
        "setup",
        "chat",
        "run",
        "webui",
        "sessions",
        "server",
        "research",
        "models",
        "skills",
        "mcp",
        "memory",
        "runtime",
        "worktree",
        "jobs",
        "project",
        "browser",
        "computer",
    ):
        assert name in help_out.stdout
    assert "doctor" not in help_out.stdout
    # Burden commands removed from public CLI
    assert "soak" not in help_out.stdout
    runtime_help = runner.invoke(app, ["runtime", "--help"])
    assert runtime_help.exit_code == 0
    assert "benchmark" not in runtime_help.stdout
    assert "soak" not in runtime_help.stdout
    models_help = runner.invoke(app, ["models", "--help"])
    assert models_help.exit_code == 0
    assert "doctor" not in models_help.stdout
    computer_help = runner.invoke(app, ["computer", "--help"])
    assert computer_help.exit_code == 0
    assert "doctor" not in computer_help.stdout


def test_repl_help_documents_modes():
    from kageha.chat.repl import HELP

    assert "/plan" in HELP
    assert "/goal" in HELP
    assert "/build" in HELP
    assert "Cursor" not in HELP
    assert "Codex" not in HELP


def test_container_helper_removed():
    import importlib.util

    spec = importlib.util.find_spec("kageha.harness.container")
    assert spec is None


def test_knowledge_dead_modules_removed():
    import importlib.util

    assert importlib.util.find_spec("kageha.knowledge.ingest") is None
    assert importlib.util.find_spec("kageha.knowledge.registry") is None
