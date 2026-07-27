"""Media tools: Gemini Nano Banana Pro + Fal (mocked HTTP)."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.media import (
    DEFAULT_GEMINI_IMAGE_MODEL,
    GEMINI_IMAGE_MODELS,
    register_media_tools,
)
from kageha.models import fal as fal_mod


def _ctx(tmp_path: Path) -> HarnessContext:
    root = tmp_path / "session"
    root.mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(exist_ok=True)
    ws = SessionWorkspace(run_id="test", root=root)
    return HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )


def test_gemini_model_aliases():
    assert GEMINI_IMAGE_MODELS["nano-banana-pro"] == "gemini-3-pro-image"
    assert DEFAULT_GEMINI_IMAGE_MODEL == "gemini-3-pro-image"


def test_media_tools_registered_with_network_risk(tmp_path: Path):
    reg = register_media_tools(_ctx(tmp_path))
    names = set(reg.names())
    assert "gemini_generate_image" in names
    assert "fal_generate_image" in names
    assert "fal_text_to_video" in names
    assert "fal_image_to_video" in names
    for name in (
        "gemini_generate_image",
        "fal_generate_image",
        "fal_text_to_video",
        "download_media",
        "siliconflow_image",
    ):
        assert reg.get(name).risk_class == "network"


@pytest.mark.asyncio
async def test_gemini_generate_image_mocked(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    ctx = _ctx(tmp_path)
    reg = register_media_tools(ctx)
    tool = reg.get("gemini_generate_image")
    assert tool is not None

    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
    fake_body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "here you go"},
                        {"inlineData": {"mimeType": "image/png", "data": png}},
                    ]
                }
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = fake_body
    mock_resp.text = ""

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("kageha.harness.tools.media.httpx.AsyncClient", return_value=mock_client):
        out = await tool.call(
            prompt="a red cube",
            model="nano-banana-pro",
            filename="cube.png",
            aspect_ratio="4:5",
        )

    data = json.loads(out)
    assert data["model"] == "gemini-3-pro-image"
    assert data["path"] == "artifacts/cube.png"
    assert data["aspectRatio"] == "4:5"
    assert (ctx.workspace.root / data["path"]).is_file()
    call_kwargs = mock_client.post.await_args.kwargs
    gen_cfg = call_kwargs["json"]["generationConfig"]
    assert "responseModalities" in gen_cfg
    assert gen_cfg["imageConfig"]["aspectRatio"] == "4:5"
    assert "gemini-3-pro-image" in mock_client.post.await_args.args[0]


@pytest.mark.asyncio
async def test_gemini_generate_image_requires_key(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    reg = register_media_tools(_ctx(tmp_path))
    out = await reg.get("gemini_generate_image").call(prompt="x")
    assert out.startswith("ERROR: GEMINI_API_KEY")


@pytest.mark.asyncio
async def test_fal_generate_image_mocked(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fal-test")
    ctx = _ctx(tmp_path)

    async def fake_run(self, model_id: str, payload: dict):
        assert model_id == "fal-ai/flux/dev"
        assert "prompt" in payload
        return {"images": [{"url": "https://cdn.example/img.png"}]}

    get_resp = MagicMock()
    get_resp.content = b"imgbytes"
    get_resp.raise_for_status = MagicMock()
    http_client = AsyncMock()
    http_client.get = AsyncMock(return_value=get_resp)
    http_client.__aenter__ = AsyncMock(return_value=http_client)
    http_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch.object(fal_mod.FalClient, "run", new=fake_run),
        patch("kageha.harness.tools.media.httpx.AsyncClient", return_value=http_client),
    ):
        reg = register_media_tools(ctx)
        out = await reg.get("fal_generate_image").call(prompt="sunset", filename="s.png")

    data = json.loads(out)
    assert data["model"] == "fal-ai/flux/dev"
    assert data["path"] == "artifacts/s.png"
    assert (ctx.workspace.root / "artifacts/s.png").read_bytes() == b"imgbytes"


@pytest.mark.asyncio
async def test_fal_generate_image_missing_key(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("FAL_API_KEY", raising=False)
    reg = register_media_tools(_ctx(tmp_path))
    out = await reg.get("fal_generate_image").call(prompt="x")
    assert "FAL_KEY" in out
