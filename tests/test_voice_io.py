"""Voice-as-channel helpers (STT mime + wav write + reply flag)."""

from __future__ import annotations

from pathlib import Path

from kageha.chat.voice_io import voice_reply_enabled, write_pcm_wav
from kageha.models.stt import _mime


def test_stt_mime_guesses():
    assert _mime(Path("a.wav")) == "audio/wav"
    assert _mime(Path("a.ogg")) == "audio/ogg"
    assert _mime(Path("a.m4a")) == "audio/mp4"


def test_write_pcm_wav(tmp_path: Path):
    dest = tmp_path / "t.wav"
    # 0.01s of silence at 24kHz 16-bit mono
    pcm = b"\x00\x00" * 240
    write_pcm_wav(dest, pcm, sample_rate=24000)
    assert dest.is_file()
    assert dest.stat().st_size > 44


def test_voice_reply_env(monkeypatch):
    monkeypatch.delenv("KAGEHA_VOICE_REPLY", raising=False)
    assert voice_reply_enabled() is False
    monkeypatch.setenv("KAGEHA_VOICE_REPLY", "1")
    assert voice_reply_enabled() is True
