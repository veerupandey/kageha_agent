"""E2E-style tests for WebUI STT/TTS + audio artifact kind."""

from __future__ import annotations

import asyncio
import json
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from kageha.webui import server as webui_server
from kageha.webui.server import WebUIApp, _artifact_file_kind, _session_file_mimetype


def _silent_wav(path: Path, *, frames: int = 480) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * frames)


def _multipart(field: str, filename: str, data: bytes, ctype: str) -> tuple[bytes, str]:
    boundary = "----kagehaVoiceBoundary7"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


def _run_coro_threadsafe(coro, _loop):
    loop = asyncio.new_event_loop()

    class Fut:
        def result(self, timeout=None):  # noqa: ANN001
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

    return Fut()


def test_artifact_kind_and_mimetype_for_audio(tmp_path: Path):
    wav = tmp_path / "voiceover.wav"
    _silent_wav(wav)
    assert _artifact_file_kind(wav) == "audio"
    assert _session_file_mimetype(wav) in {"audio/wav", "audio/x-wav"}
    assert _artifact_file_kind(tmp_path / "clip.mp3") == "audio"
    assert ".wav" in webui_server._MEDIA_EXTS
    assert ".mp3" in webui_server._AUDIO_EXTS


def test_session_file_mimetype_sniffs_jpeg_disguised_as_png(tmp_path: Path):
    """Gemini / nano_banana often writes JPEG bytes into a .png path."""
    fake = tmp_path / "nano_banana_edit.png"
    # Minimal JPEG SOI + APP0-ish header bytes are enough for the sniffer.
    fake.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 28)
    assert _session_file_mimetype(fake) == "image/jpeg"
    real_png = tmp_path / "chart.png"
    real_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
    assert _session_file_mimetype(real_png) == "image/png"


def test_session_tts_returns_wav(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    app = WebUIApp.__new__(WebUIApp)
    app._loop = object()  # only required for attribute access; patched below
    session_id = "voice_test_sess"

    class _Ws:
        root = tmp_path / session_id

        def path(self, rel: str) -> Path:
            return self.root / rel

    _Ws.root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app, "_session_workspace", lambda _sid: _Ws())

    async def fake_synthesize(text: str, dest: Path) -> Path:
        assert "hello" in text.lower()
        _silent_wav(dest)
        return dest

    with (
        patch("asyncio.run_coroutine_threadsafe", side_effect=_run_coro_threadsafe),
        patch("kageha.chat.voice_io.synthesize_reply_wav", new=fake_synthesize),
    ):
        status, data, ctype, extra = app._session_tts(
            session_id, b'{"text":"Hello voice"}'
        )

    assert status == 200
    assert ctype == "audio/wav"
    assert data[:4] == b"RIFF"
    assert "reply.wav" in extra.get("Content-Disposition", "")


def test_session_stt_transcribes_multipart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    app = WebUIApp.__new__(WebUIApp)
    app._loop = object()
    session_id = "voice_stt_sess"
    wav = tmp_path / "mic.wav"
    _silent_wav(wav)
    body, ctype = _multipart("file", "mic.wav", wav.read_bytes(), "audio/wav")

    class _Ws:
        root = tmp_path / session_id

        def path(self, rel: str) -> Path:
            return self.root / rel

    _Ws.root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app, "_session_workspace", lambda _sid: _Ws())

    async def fake_transcribe(path: Path, *, language: str = "") -> str:
        assert path.is_file()
        return "hello from mic"

    with (
        patch("asyncio.run_coroutine_threadsafe", side_effect=_run_coro_threadsafe),
        patch("kageha.models.stt.transcribe_audio", new=fake_transcribe),
    ):
        status, payload, out_ctype = app._session_stt(
            session_id, body, {"Content-Type": ctype}
        )

    assert status == 200
    assert "application/json" in out_ctype
    data = json.loads(payload.decode())
    assert data["text"] == "hello from mic"
    assert data["session_id"] == session_id


def test_voice_pack_is_core():
    from kageha.harness.tool_packs import CORE_PACK_NAMES

    assert "voice" in CORE_PACK_NAMES
