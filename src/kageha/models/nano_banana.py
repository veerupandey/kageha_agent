"""Gemini Nano Banana image generation (Interactions API + generateContent fallback).

Nano Banana is Google's native Gemini image stack:
  - nano / banana2-lite → gemini-3.1-flash-lite-image
  - banana2 (default)   → gemini-3.1-flash-image
  - pro                 → gemini-3-pro-image
  - legacy              → gemini-2.5-flash-image
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

import httpx

from kageha.config import env_key

DEFAULT_MODEL = "gemini-3.1-flash-image"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

NANO_BANANA_MODELS: dict[str, str] = {
    "banana2": "gemini-3.1-flash-image",
    "nano-banana-2": "gemini-3.1-flash-image",
    "flash": "gemini-3.1-flash-image",
    "default": "gemini-3.1-flash-image",
    "banana2-lite": "gemini-3.1-flash-lite-image",
    "lite": "gemini-3.1-flash-lite-image",
    "nano-banana-2-lite": "gemini-3.1-flash-lite-image",
    "pro": "gemini-3-pro-image",
    "nano-banana-pro": "gemini-3-pro-image",
    "legacy": "gemini-2.5-flash-image",
    "nano-banana": "gemini-2.5-flash-image",
    # Direct model ids also allowed when they match known prefixes.
}


@dataclass(frozen=True)
class NanoBananaImage:
    data: bytes
    mime_type: str
    model: str
    interaction_id: str = ""
    text: str = ""


def resolve_nano_banana_model(explicit: str | None = None) -> str:
    raw = (
        (explicit or "").strip()
        or (os.environ.get("KAGEHA_NANO_BANANA_MODEL") or "").strip()
        or "banana2"
    )
    key = raw.lower()
    if key in NANO_BANANA_MODELS:
        return NANO_BANANA_MODELS[key]
    if raw.startswith("gemini-") and "image" in raw:
        return raw
    return DEFAULT_MODEL


def _mime_for_path(path: str) -> str:
    low = path.lower()
    if low.endswith(".jpg") or low.endswith(".jpeg"):
        return "image/jpeg"
    if low.endswith(".webp"):
        return "image/webp"
    if low.endswith(".gif"):
        return "image/gif"
    return "image/png"


def _ext_for_mime(mime: str) -> str:
    m = (mime or "").lower()
    if "jpeg" in m or "jpg" in m:
        return ".jpg"
    if "webp" in m:
        return ".webp"
    if "gif" in m:
        return ".gif"
    return ".png"


def _walk_images(node: Any, out: list[tuple[bytes, str]]) -> None:
    """Collect base64 image payloads from nested Interactions / generateContent JSON."""
    if isinstance(node, dict):
        # Interactions convenience / output blocks
        data = node.get("data")
        mime = str(node.get("mime_type") or node.get("mimeType") or "")
        if isinstance(data, str) and data and (
            mime.startswith("image/")
            or node.get("type") in {"image", "output_image"}
            or "inlineData" in node
            or "inline_data" in node
        ):
            try:
                out.append((base64.b64decode(data), mime or "image/png"))
            except Exception:  # noqa: BLE001
                pass
        inline = node.get("inlineData") or node.get("inline_data")
        if isinstance(inline, dict):
            blob = inline.get("data")
            imime = str(inline.get("mimeType") or inline.get("mime_type") or "image/png")
            if isinstance(blob, str) and blob:
                try:
                    out.append((base64.b64decode(blob), imime))
                except Exception:  # noqa: BLE001
                    pass
        # Nested convenience property
        if "output_image" in node:
            _walk_images(node.get("output_image"), out)
        for v in node.values():
            _walk_images(v, out)
    elif isinstance(node, list):
        for item in node:
            _walk_images(item, out)


def _extract_text(node: Any, chunks: list[str]) -> None:
    if isinstance(node, dict):
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            chunks.append(node["text"])
        elif isinstance(node.get("text"), str) and "inlineData" not in node and "inline_data" not in node:
            # generateContent parts
            if node.get("text"):
                chunks.append(node["text"])
        for v in node.values():
            _extract_text(v, chunks)
    elif isinstance(node, list):
        for item in node:
            _extract_text(item, chunks)


class NanoBananaClient:
    """Thin Gemini image client — no google-genai SDK required."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = BASE_URL,
        timeout: float = 180.0,
    ) -> None:
        self.api_key = api_key or env_key("GEMINI_API_KEY") or ""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }

    async def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        reference_images: list[tuple[bytes, str]] | None = None,
        aspect_ratio: str = "1:1",
        image_size: str = "1K",
        mime_type: str = "image/png",
        use_google_search: bool = False,
    ) -> NanoBananaImage:
        if not self.available:
            raise RuntimeError("GEMINI_API_KEY not set")
        model_id = resolve_nano_banana_model(model)
        refs = list(reference_images or [])

        try:
            return await self._via_interactions(
                prompt,
                model_id=model_id,
                refs=refs,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                mime_type=mime_type,
                use_google_search=use_google_search,
            )
        except Exception as interactions_err:  # noqa: BLE001
            # Older keys / regions may only expose generateContent image models.
            try:
                return await self._via_generate_content(
                    prompt,
                    model_id=model_id,
                    refs=refs,
                )
            except Exception as gen_err:  # noqa: BLE001
                raise RuntimeError(
                    f"Nano Banana failed via Interactions ({interactions_err}) "
                    f"and generateContent ({gen_err})"
                ) from gen_err

    async def _via_interactions(
        self,
        prompt: str,
        *,
        model_id: str,
        refs: list[tuple[bytes, str]],
        aspect_ratio: str,
        image_size: str,
        mime_type: str,
        use_google_search: bool,
    ) -> NanoBananaImage:
        inputs: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for data, mime in refs[:14]:
            inputs.append(
                {
                    "type": "image",
                    "data": base64.b64encode(data).decode("ascii"),
                    "mime_type": mime or "image/png",
                }
            )
        payload: dict[str, Any] = {
            "model": model_id,
            "input": inputs,
            "response_format": {
                "type": "image",
                "mime_type": mime_type or "image/png",
                "aspect_ratio": aspect_ratio or "1:1",
                "image_size": image_size or "1K",
            },
        }
        if use_google_search and "lite" not in model_id:
            payload["tools"] = [{"type": "google_search"}]

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/interactions",
                headers=self._headers(),
                json=payload,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:800]}")
            body = resp.json()

        images: list[tuple[bytes, str]] = []
        _walk_images(body, images)
        if not images:
            raise RuntimeError(f"no image in Interactions response: {str(body)[:500]}")
        data, mime = images[-1]
        texts: list[str] = []
        _extract_text(body, texts)
        return NanoBananaImage(
            data=data,
            mime_type=mime or mime_type or "image/png",
            model=model_id,
            interaction_id=str(body.get("id") or ""),
            text="\n".join(t for t in texts if t).strip()[:2000],
        )

    async def _via_generate_content(
        self,
        prompt: str,
        *,
        model_id: str,
        refs: list[tuple[bytes, str]],
    ) -> NanoBananaImage:
        parts: list[dict[str, Any]] = [{"text": prompt}]
        for data, mime in refs[:14]:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": mime or "image/png",
                        "data": base64.b64encode(data).decode("ascii"),
                    }
                }
            )
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
            },
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/models/{model_id}:generateContent",
                headers=self._headers(),
                json=payload,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:800]}")
            body = resp.json()

        images: list[tuple[bytes, str]] = []
        _walk_images(body, images)
        if not images:
            raise RuntimeError(f"no image in generateContent response: {str(body)[:500]}")
        data, mime = images[-1]
        texts: list[str] = []
        _extract_text(body, texts)
        return NanoBananaImage(
            data=data,
            mime_type=mime or "image/png",
            model=model_id,
            text="\n".join(t for t in texts if t).strip()[:2000],
        )


__all__ = [
    "NANO_BANANA_MODELS",
    "NanoBananaClient",
    "NanoBananaImage",
    "resolve_nano_banana_model",
    "_ext_for_mime",
    "_mime_for_path",
]
