"""Nano Banana (Gemini image) client + tools."""

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
from kageha.harness.tool_packs import CORE_PACK_NAMES
from kageha.harness.tools.nano_banana import register_nano_banana_tools
from kageha.models.nano_banana import (
    NanoBananaClient,
    NanoBananaImage,
    resolve_nano_banana_model,
    _walk_images,
)


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


def test_nano_banana_in_core_packs() -> None:
    assert "nano_banana" in CORE_PACK_NAMES


def test_resolve_nano_banana_model(monkeypatch) -> None:
    monkeypatch.delenv("KAGEHA_NANO_BANANA_MODEL", raising=False)
    assert resolve_nano_banana_model() == "gemini-3.1-flash-image"
    assert resolve_nano_banana_model("pro") == "gemini-3-pro-image"
    assert resolve_nano_banana_model("lite") == "gemini-3.1-flash-lite-image"
    assert (
        resolve_nano_banana_model("gemini-3.1-flash-image")
        == "gemini-3.1-flash-image"
    )


def test_walk_images_from_interactions_shape() -> None:
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
    body = {
        "id": "abc",
        "output_image": {"type": "image", "mime_type": "image/png", "data": png},
    }
    out: list[tuple[bytes, str]] = []
    _walk_images(body, out)
    assert out and out[-1][1] == "image/png"
    assert out[-1][0].startswith(b"\x89PNG")


def test_walk_images_from_generate_content_shape() -> None:
    png = base64.b64encode(b"imgbytes").decode()
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "here"},
                        {"inlineData": {"mimeType": "image/png", "data": png}},
                    ]
                }
            }
        ]
    }
    out: list[tuple[bytes, str]] = []
    _walk_images(body, out)
    assert out and out[-1][0] == b"imgbytes"


@pytest.mark.asyncio
async def test_tools_register_and_require_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    reg = register_nano_banana_tools(_ctx(tmp_path))
    assert "nano_banana_generate" in reg.tools
    assert "nano_banana_edit" in reg.tools
    msg = await reg.get("nano_banana_generate").call(prompt="a banana")
    assert "GEMINI_API_KEY" in msg


@pytest.mark.asyncio
async def test_generate_saves_artifact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    ctx = _ctx(tmp_path)
    reg = register_nano_banana_tools(ctx)
    fake = NanoBananaImage(
        data=b"\x89PNG\r\n\x1a\nfakedata",
        mime_type="image/png",
        model="gemini-3.1-flash-image",
        interaction_id="ix1",
        text="ok",
    )
    with patch.object(
        NanoBananaClient, "generate", new=AsyncMock(return_value=fake)
    ):
        raw = await reg.get("nano_banana_generate").call(
            prompt="matcha tin on marble",
            filename="slide_1.png",
            aspect_ratio="4:5",
        )
    data = json.loads(raw)
    assert data["path"].endswith("artifacts/slide_1.png")
    assert data["model"] == "gemini-3.1-flash-image"
    saved = ctx.workspace.path(data["path"])
    assert saved.is_file()
    assert saved.read_bytes().startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_edit_uses_reference_image(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    ctx = _ctx(tmp_path)
    ref = ctx.workspace.path("artifacts/product.png")
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_bytes(b"product-bytes")
    reg = register_nano_banana_tools(ctx)
    fake = NanoBananaImage(
        data=b"edited",
        mime_type="image/png",
        model="gemini-3.1-flash-image",
    )
    mock_gen = AsyncMock(return_value=fake)
    with patch.object(NanoBananaClient, "generate", new=mock_gen):
        raw = await reg.get("nano_banana_edit").call(
            prompt="put product on ceramic tray",
            image_paths="artifacts/product.png",
            filename="artifacts/edit.png",
        )
    data = json.loads(raw)
    assert data["path"].endswith("artifacts/edit.png")
    kwargs = mock_gen.await_args.kwargs
    assert kwargs["reference_images"][0][0] == b"product-bytes"


@pytest.mark.asyncio
async def test_interactions_http_happy_path(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    png = base64.b64encode(b"PNGDATA").decode()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "id": "i1",
        "output_image": {"type": "image", "mime_type": "image/png", "data": png},
    }
    client_cm = MagicMock()
    client_cm.__aenter__ = AsyncMock(return_value=client_cm)
    client_cm.__aexit__ = AsyncMock(return_value=None)
    client_cm.post = AsyncMock(return_value=resp)

    with patch("kageha.models.nano_banana.httpx.AsyncClient", return_value=client_cm):
        out = await NanoBananaClient().generate("draw a banana", model="banana2")
    assert out.data == b"PNGDATA"
    assert out.model == "gemini-3.1-flash-image"
    assert out.interaction_id == "i1"
