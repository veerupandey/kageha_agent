"""First-class Gemini TTS tool (always-on core pack).

Requires ``GEMINI_API_KEY``. Writes WAV under ``artifacts/``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from kageha.chat.voice_io import write_pcm_wav
from kageha.harness.tools.base import ToolRegistry, tool
from kageha.harness.tools.paths import rel_to_workspace
from kageha.models.voice import resolve_voice_config, synthesize_gemini_tts

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _normalize_filename(filename: str) -> str:
    name = (filename or "voiceover.wav").strip().lstrip("/")
    if not name:
        name = "voiceover.wav"
    base = Path(name).name
    stem = _SAFE_NAME.sub("_", Path(base).stem).strip("._") or "voiceover"
    return f"artifacts/{stem}.wav"


def register_voice_tools(ctx: "HarnessContext") -> ToolRegistry:
    reg = ToolRegistry()

    @tool(
        description=(
            "FIRST-CLASS text-to-speech via Gemini TTS. "
            "Use for voiceovers, spoken ads, narrated scripts, and audio deliverables. "
            "Do NOT shell out to curl/say/ffmpeg inventively for Gemini TTS. "
            "Requires GEMINI_API_KEY. Saves a WAV under artifacts/. "
            "Optional voice name (e.g. Kore, Puck, Charon) — defaults from models.yaml."
        ),
        risk_class="network",
    )
    async def gemini_tts(
        text: str,
        filename: str = "voiceover.wav",
        voice: str = "",
    ) -> str:
        spoken = (text or "").strip()
        if not spoken:
            return "ERROR: text is required"
        if len(spoken) > 4000:
            spoken = spoken[:3997].rstrip() + "…"

        cfg = resolve_voice_config()
        if cfg is None:
            return (
                "ERROR: GEMINI_API_KEY not configured. "
                "Set GEMINI_API_KEY in .env for Gemini TTS."
            )
        voice_name = (voice or "").strip() or cfg.voice
        try:
            pcm = await synthesize_gemini_tts(
                spoken,
                model=cfg.model,
                voice=voice_name,
                api_key=cfg.api_key,
                base_url=cfg.base_url,
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: Gemini TTS failed: {exc}"

        rel = _normalize_filename(filename)
        dest = ctx.workspace.path(rel)
        write_pcm_wav(dest, pcm, sample_rate=24000)
        return json.dumps(
            {
                "path": rel_to_workspace(dest, ctx.workspace.root),
                "bytes": dest.stat().st_size,
                "mime_type": "audio/wav",
                "model": cfg.model,
                "voice": voice_name,
                "chars": len(spoken),
            }
        )

    if hasattr(gemini_tts, "name"):
        reg.register(gemini_tts)  # type: ignore[arg-type]
    return reg
