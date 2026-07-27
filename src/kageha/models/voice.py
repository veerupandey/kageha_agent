"""Gemini TTS voice config + synthesis (preview models)."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

import httpx

from kageha.config import env_key
from kageha.models.registry import ModelRegistry

_DEFAULT_VOICE_MODEL = "gemini-3.1-flash-tts-preview"
_DEFAULT_VOICE_NAME = "Kore"


@dataclass(frozen=True)
class VoiceConfig:
    provider: str
    model: str
    voice: str
    api_key: str
    base_url: str


def resolve_voice_config(registry: ModelRegistry | None = None) -> VoiceConfig | None:
    """Resolve Gemini TTS settings from env + models.yaml ``voice:``."""
    reg = registry or ModelRegistry.load()
    cfg = dict(reg.voice or {})
    provider = (
        (os.environ.get("KAGEHA_VOICE_PROVIDER") or "").strip().lower()
        or str(cfg.get("provider") or "gemini").lower()
    )
    if provider and provider != "gemini":
        raise ValueError(
            f"Unsupported voice provider {provider!r}; only 'gemini' is wired. "
            "Unset KAGEHA_VOICE_PROVIDER or set it to gemini."
        )
    provider = "gemini"
    model = (
        (os.environ.get("KAGEHA_VOICE_MODEL") or "").strip()
        or str(cfg.get("model") or _DEFAULT_VOICE_MODEL)
    )
    voice = (
        (os.environ.get("KAGEHA_VOICE_NAME") or "").strip()
        or str(cfg.get("voice") or _DEFAULT_VOICE_NAME)
    )
    pc = reg.providers.get("gemini")
    key = env_key(pc.api_key_env) if pc else env_key("GEMINI_API_KEY")
    if not key:
        return None
    base = (
        (pc.base_url if pc else "")
        or env_key("GEMINI_BASE_URL")
        or "https://generativelanguage.googleapis.com/v1beta"
    )
    return VoiceConfig(
        provider="gemini",
        model=model,
        voice=voice or _DEFAULT_VOICE_NAME,
        api_key=key,
        base_url=base.rstrip("/"),
    )


async def synthesize_gemini_tts(
    text: str,
    *,
    model: str,
    voice: str,
    api_key: str,
    base_url: str,
) -> bytes:
    """Return raw 16-bit PCM audio bytes from Gemini TTS generateContent."""
    url = f"{base_url.rstrip('/')}/models/{model}:generateContent"
    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice or _DEFAULT_VOICE_NAME}
                }
            },
        },
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            detail = (resp.text or "").replace("\n", " ")[:600]
            raise httpx.HTTPStatusError(
                f"Client error '{resp.status_code}' for url '{resp.request.url}' — {detail}",
                request=resp.request,
                response=resp,
            )
        data = resp.json()

    parts = (
        ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts")
    ) or []
    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data") or {}
        b64 = inline.get("data")
        if b64:
            return base64.b64decode(b64)
    raise RuntimeError("Gemini TTS response had no audio inlineData")
