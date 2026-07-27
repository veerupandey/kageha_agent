"""Speech-to-text for voice-as-channel (OpenAI Whisper or Gemini multimodal)."""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

import httpx

from kageha.config import env_key


async def transcribe_audio(path: Path, *, language: str = "") -> str:
    """Transcribe a local audio file to text.

    Prefers OpenAI Whisper when ``OPENAI_API_KEY`` is set; otherwise Gemini
    multimodal with ``GEMINI_API_KEY``.
    """
    audio_path = Path(path).expanduser().resolve()
    if not audio_path.is_file():
        raise FileNotFoundError(f"audio not found: {audio_path}")
    provider = (
        os.environ.get("KAGEHA_STT_PROVIDER") or ""
    ).strip().lower()
    if provider in {"openai", "whisper"} or (
        not provider and env_key("OPENAI_API_KEY")
    ):
        return await _transcribe_openai(audio_path, language=language)
    if provider in {"gemini", ""} or env_key("GEMINI_API_KEY"):
        return await _transcribe_gemini(audio_path, language=language)
    raise RuntimeError(
        "STT needs OPENAI_API_KEY (Whisper) or GEMINI_API_KEY; "
        "optional KAGEHA_STT_PROVIDER=openai|gemini"
    )


async def _transcribe_openai(path: Path, *, language: str = "") -> str:
    key = env_key("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY missing for Whisper STT")
    base = (
        env_key("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    ).rstrip("/")
    model = (os.environ.get("KAGEHA_STT_MODEL") or "whisper-1").strip()
    data: dict[str, str] = {"model": model}
    if language.strip():
        data["language"] = language.strip()
    async with httpx.AsyncClient(timeout=120.0) as client:
        with path.open("rb") as fh:
            resp = await client.post(
                f"{base}/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (path.name, fh, _mime(path))},
                data=data,
            )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Whisper STT failed {resp.status_code}: {(resp.text or '')[:400]}"
            )
        payload = resp.json()
    text = str(payload.get("text") or "").strip()
    if not text:
        raise RuntimeError("Whisper STT returned empty text")
    return text


async def _transcribe_gemini(path: Path, *, language: str = "") -> str:
    key = env_key("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing for Gemini STT")
    base = (
        env_key("GEMINI_BASE_URL")
        or "https://generativelanguage.googleapis.com/v1beta"
    ).rstrip("/")
    model = (
        os.environ.get("KAGEHA_STT_MODEL") or "gemini-2.5-flash"
    ).strip()
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    prompt = "Transcribe this audio to plain text only. No commentary."
    if language.strip():
        prompt += f" Language hint: {language.strip()}."
    url = f"{base}/models/{model}:generateContent"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"inline_data": {"mime_type": _mime(path), "data": b64}},
                    {"text": prompt},
                ],
            }
        ]
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            url,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=payload,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Gemini STT failed {resp.status_code}: {(resp.text or '')[:400]}"
            )
        data = resp.json()
    parts = (
        ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts")
    ) or []
    texts = [str(p.get("text") or "").strip() for p in parts if p.get("text")]
    text = "\n".join(t for t in texts if t).strip()
    if not text:
        raise RuntimeError("Gemini STT returned empty text")
    return text


def _mime(path: Path) -> str:
    suffix = path.suffix.lower()
    known = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
        ".oga": "audio/ogg",
    }
    if suffix in known:
        return known[suffix]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"
