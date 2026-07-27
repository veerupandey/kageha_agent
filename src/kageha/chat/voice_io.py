"""CLI voice I/O — record, play, STT/TTS round-trip helpers."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path


def voice_reply_enabled() -> bool:
    return os.environ.get("KAGEHA_VOICE_REPLY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def write_pcm_wav(path: Path, pcm: bytes, *, sample_rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


async def record_push_to_talk(
    dest: Path | None = None,
    *,
    seconds: float | None = None,
) -> Path:
    """Record microphone audio to WAV.

    Uses ``rec`` (sox), then ``ffmpeg``. Duration from ``seconds`` or
    ``KAGEHA_VOICE_SECONDS`` (default 8).
    """
    dur = float(
        seconds
        if seconds is not None
        else (os.environ.get("KAGEHA_VOICE_SECONDS") or "8")
    )
    dur = max(1.0, min(dur, 120.0))
    out = dest or Path(tempfile.mkstemp(prefix="kageha-voice-", suffix=".wav")[1])
    out.parent.mkdir(parents=True, exist_ok=True)

    rec = shutil.which("rec")
    if rec:
        cmd = [rec, "-q", "-c", "1", "-r", "16000", str(out), "trim", "0", str(dur)]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode == 0 and out.is_file() and out.stat().st_size > 44:
            return out
        raise RuntimeError(
            f"rec failed ({proc.returncode}): {(err or b'').decode(errors='replace')[:300]}"
        )

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        # Try macOS avfoundation default mic, then Linux pulse/alsa.
        attempts = [
            [ffmpeg, "-y", "-f", "avfoundation", "-i", ":0", "-t", str(dur), str(out)],
            [ffmpeg, "-y", "-f", "pulse", "-i", "default", "-t", str(dur), str(out)],
            [ffmpeg, "-y", "-f", "alsa", "-i", "default", "-t", str(dur), str(out)],
        ]
        last_err = ""
        for cmd in attempts:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await proc.communicate()
            if proc.returncode == 0 and out.is_file() and out.stat().st_size > 44:
                return out
            last_err = (err or b"").decode(errors="replace")[:300]
        raise RuntimeError(f"ffmpeg mic capture failed: {last_err}")

    raise RuntimeError(
        "Voice record needs `sox` (rec) or `ffmpeg` on PATH. "
        "Install sox (`brew install sox`) or ffmpeg."
    )


def play_audio(path: Path) -> None:
    """Play a local audio file (best-effort)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    players = [
        ["afplay", str(p)],
        ["aplay", str(p)],
        ["ffplay", "-nodisp", "-autoexit", str(p)],
    ]
    for cmd in players:
        if not shutil.which(cmd[0]):
            continue
        try:
            subprocess.run(cmd, check=False, capture_output=True, timeout=120)
            return
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("No audio player found (afplay/aplay/ffplay)")


async def synthesize_reply_wav(text: str, dest: Path) -> Path:
    """TTS reply via Gemini; writes WAV to dest."""
    from kageha.models.voice import resolve_voice_config, synthesize_gemini_tts

    cfg = resolve_voice_config()
    if cfg is None:
        raise RuntimeError("GEMINI_API_KEY missing for voice reply TTS")
    spoken = (text or "").strip()
    if not spoken:
        raise RuntimeError("empty TTS text")
    # Keep replies short for voice notes.
    if len(spoken) > 1200:
        spoken = spoken[:1197].rstrip() + "…"
    pcm = await synthesize_gemini_tts(
        spoken,
        model=cfg.model,
        voice=cfg.voice,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
    )
    write_pcm_wav(dest, pcm, sample_rate=24000)
    return dest


async def listen_once(*, prompt: str = "Listening… speak now") -> str:
    """Record once and transcribe. Returns user text."""
    from kageha.models.stt import transcribe_audio

    print(prompt, flush=True)
    path = await record_push_to_talk()
    try:
        text = await transcribe_audio(path)
    finally:
        try:
            path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
    return text.strip()
