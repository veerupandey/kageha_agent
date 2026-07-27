"""Thin MediaProvider registry — Gemini + Fal (+ custom via register_provider)."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from kageha.config import env_key
from kageha.models.fal import FalClient

GEMINI_IMAGE_MODELS = {
    "nano-banana-pro": "gemini-3-pro-image",
    "nano_banana_pro": "gemini-3-pro-image",
    "gemini-3-pro-image": "gemini-3-pro-image",
    "nano-banana-2": "gemini-3.1-flash-image",
    "nano_banana_2": "gemini-3.1-flash-image",
    "gemini-3.1-flash-image": "gemini-3.1-flash-image",
    "nano-banana": "gemini-2.5-flash-image",
    "nano_banana": "gemini-2.5-flash-image",
    "gemini-2.5-flash-image": "gemini-2.5-flash-image",
}
DEFAULT_GEMINI_IMAGE = "gemini-3-pro-image"
GEMINI_BASE = (
    env_key("GEMINI_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta"
).rstrip("/")

FAL_IMAGE_MODELS = {
    "flux": "fal-ai/flux/dev",
    "flux-schnell": "fal-ai/flux/schnell",
}
FAL_T2V_MODELS = {
    "wan": "fal-ai/wan/v2.2-a14b/text-to-video",
    "wan22": "fal-ai/wan/v2.2-a14b/text-to-video",
    "minimax": "fal-ai/minimax-video/video-01",
}


@dataclass(frozen=True)
class MediaCapabilities:
    image: bool = False
    edit: bool = False
    i2v: bool = False
    t2v: bool = False


@runtime_checkable
class MediaProvider(Protocol):
    name: str
    capabilities: MediaCapabilities

    async def generate_image(
        self,
        prompt: str,
        *,
        dest_dir: Path,
        filename: str = "",
        model: str = "",
        aspect_ratio: str = "1:1",
        image_size: str = "2K",
        image_path: str = "",
    ) -> dict[str, Any]: ...

    async def text_to_video(
        self,
        prompt: str,
        *,
        dest_dir: Path,
        filename: str = "clip.mp4",
        model: str = "",
    ) -> dict[str, Any]: ...


_REGISTRY: dict[str, MediaProvider] = {}


def register_provider(provider: MediaProvider) -> None:
    _REGISTRY[provider.name] = provider


def get_provider(name: str) -> MediaProvider | None:
    key = (name or "").strip().lower()
    if key in _REGISTRY:
        return _REGISTRY[key]
    # Lazy builtins
    _ensure_builtins()
    return _REGISTRY.get(key)


def list_providers() -> list[dict[str, Any]]:
    _ensure_builtins()
    out = []
    for p in _REGISTRY.values():
        out.append(
            {
                "name": p.name,
                "capabilities": {
                    "image": p.capabilities.image,
                    "edit": p.capabilities.edit,
                    "i2v": p.capabilities.i2v,
                    "t2v": p.capabilities.t2v,
                },
            }
        )
    return out


def _mime_to_ext(mime: str) -> str:
    m = (mime or "").lower()
    if "jpeg" in m or "jpg" in m:
        return ".jpg"
    if "webp" in m:
        return ".webp"
    if "gif" in m:
        return ".gif"
    return ".png"


class GeminiImageProvider:
    name = "gemini"
    capabilities = MediaCapabilities(image=True)

    async def generate_image(
        self,
        prompt: str,
        *,
        dest_dir: Path,
        filename: str = "",
        model: str = "",
        aspect_ratio: str = "1:1",
        image_size: str = "2K",
        image_path: str = "",
    ) -> dict[str, Any]:
        api_key = env_key("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            return {"ok": False, "error": "GEMINI_API_KEY not configured"}
        key = (model or "nano-banana-pro").strip()
        model_id = GEMINI_IMAGE_MODELS.get(
            key, GEMINI_IMAGE_MODELS.get(key.lower(), key)
        )
        if model_id not in GEMINI_IMAGE_MODELS.values() and not model_id.startswith(
            "gemini-"
        ):
            return {
                "ok": False,
                "error": (
                    f"unknown Gemini image model {model!r}. "
                    f"Use one of: {', '.join(sorted(set(GEMINI_IMAGE_MODELS)))}"
                ),
            }

        parts: list[dict[str, Any]] = []
        if image_path.strip():
            src = Path(image_path.strip())
            if not src.is_file():
                return {"ok": False, "error": f"image not found: {image_path}"}
            mime = "image/png" if src.suffix.lower() == ".png" else "image/jpeg"
            if src.suffix.lower() == ".webp":
                mime = "image/webp"
            parts.append(
                {
                    "inlineData": {
                        "mimeType": mime,
                        "data": base64.b64encode(src.read_bytes()).decode(),
                    }
                }
            )
        ar = (aspect_ratio or "").strip() or "1:1"
        size = (image_size or "").strip().upper() or "2K"
        if size not in {"1K", "2K", "4K"}:
            size = "2K"
        parts.append(
            {
                "text": (
                    f"OUTPUT FORMAT (mandatory): aspect ratio {ar}. "
                    "Fill the full frame; do not crop to a different ratio.\n\n"
                    + prompt
                )
            }
        )
        image_cfg: dict[str, Any] = {"aspectRatio": ar}
        if model_id.startswith("gemini-3"):
            image_cfg["imageSize"] = size
        gen_cfg: dict[str, Any] = {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": image_cfg,
        }
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": gen_cfg,
        }
        url = f"{GEMINI_BASE}/models/{model_id}:generateContent"
        headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 400 and "responseModalities" in (resp.text or ""):
                payload["generationConfig"] = {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": dict(image_cfg),
                }
                resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 400 and "imageSize" in (resp.text or ""):
                payload["generationConfig"] = {
                    "responseModalities": ["TEXT", "IMAGE"],
                    "imageConfig": {"aspectRatio": ar},
                }
                resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                return {
                    "ok": False,
                    "error": f"Gemini {resp.status_code}: {(resp.text or '')[:600]}",
                }
            data = resp.json()

        candidates = data.get("candidates") or []
        if not candidates:
            return {"ok": False, "error": f"no candidates: {json.dumps(data)[:500]}"}
        out_parts = ((candidates[0].get("content") or {}).get("parts")) or []
        image_bytes: bytes | None = None
        image_mime = "image/png"
        for p in out_parts:
            inline = p.get("inlineData") or p.get("inline_data")
            if isinstance(inline, dict) and inline.get("data"):
                image_mime = str(
                    inline.get("mimeType") or inline.get("mime_type") or image_mime
                )
                image_bytes = base64.b64decode(inline["data"])
        if not image_bytes:
            return {"ok": False, "error": "no image in response"}

        name = filename.strip()
        if not name:
            name = (
                f"gemini_{model_id.replace('/', '_').replace('.', '_')}"
                f"{_mime_to_ext(image_mime)}"
            )
        elif "." not in Path(name).name:
            name = name + _mime_to_ext(image_mime)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name
        dest.write_bytes(image_bytes)
        return {
            "ok": True,
            "path": str(dest),
            "model": model_id,
            "bytes": len(image_bytes),
            "mime": image_mime,
            "provider": self.name,
        }

    async def text_to_video(
        self,
        prompt: str,
        *,
        dest_dir: Path,
        filename: str = "clip.mp4",
        model: str = "",
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "gemini provider does not support text_to_video; use fal",
        }


class FalMediaProvider:
    name = "fal"
    capabilities = MediaCapabilities(image=True, edit=True, i2v=True, t2v=True)

    def __init__(self) -> None:
        self._fal = FalClient()

    async def generate_image(
        self,
        prompt: str,
        *,
        dest_dir: Path,
        filename: str = "still.png",
        model: str = "flux",
        aspect_ratio: str = "1:1",
        image_size: str = "2K",
        image_path: str = "",
    ) -> dict[str, Any]:
        del aspect_ratio, image_size, image_path
        if not self._fal.available:
            return {"ok": False, "error": "FAL_KEY not configured"}
        model_id = FAL_IMAGE_MODELS.get(model, model)
        if not model_id.startswith("fal-ai/"):
            return {"ok": False, "error": f"model not allowlisted: {model}"}
        result = await self._fal.run(
            model_id, {"prompt": prompt, "image_size": "portrait_16_9"}
        )
        url = None
        if isinstance(result, dict):
            if result.get("images"):
                url = result["images"][0].get("url")
            elif isinstance(result.get("image"), dict):
                url = result["image"].get("url")
            elif isinstance(result.get("image"), str):
                url = result["image"]
        if not url:
            return {"ok": False, "error": f"no image url: {json.dumps(result)[:500]}"}
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / (filename or "still.png")
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        return {
            "ok": True,
            "path": str(dest),
            "url": url,
            "model": model_id,
            "bytes": dest.stat().st_size,
            "provider": self.name,
        }

    async def text_to_video(
        self,
        prompt: str,
        *,
        dest_dir: Path,
        filename: str = "clip.mp4",
        model: str = "wan",
    ) -> dict[str, Any]:
        if not self._fal.available:
            return {"ok": False, "error": "FAL_KEY not configured"}
        model_id = FAL_T2V_MODELS.get(model, model)
        if not model_id.startswith("fal-ai/"):
            return {"ok": False, "error": f"model not allowlisted: {model}"}
        result = await self._fal.run(model_id, {"prompt": prompt})
        url = None
        if isinstance(result, dict):
            video = result.get("video")
            if isinstance(video, dict):
                url = video.get("url")
            elif isinstance(video, str):
                url = video
            else:
                url = result.get("video_url")
        if not url:
            return {"ok": False, "error": f"no video url: {json.dumps(result)[:500]}"}
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / (filename or "clip.mp4")
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        return {
            "ok": True,
            "path": str(dest),
            "url": url,
            "model": model_id,
            "bytes": dest.stat().st_size,
            "provider": self.name,
        }


def _ensure_builtins() -> None:
    if "gemini" not in _REGISTRY:
        register_provider(GeminiImageProvider())  # type: ignore[arg-type]
    if "fal" not in _REGISTRY:
        register_provider(FalMediaProvider())  # type: ignore[arg-type]
