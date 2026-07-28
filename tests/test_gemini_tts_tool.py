"""Unit tests for gemini_tts tool registration + filename normalization."""

from kageha.harness.tools.voice import _normalize_filename, register_voice_tools


class _Ws:
    root = None

    def path(self, rel: str):
        from pathlib import Path

        return Path("/tmp") / rel  # noqa: S108 — test stub only


class _Ctx:
    workspace = _Ws()


def test_normalize_filename_forces_artifacts_wav():
    assert _normalize_filename("ad.mp3") == "artifacts/ad.wav"
    assert _normalize_filename("artifacts/hello.wav") == "artifacts/hello.wav"
    assert _normalize_filename("../evil.wav") == "artifacts/evil.wav"
    assert _normalize_filename("") == "artifacts/voiceover.wav"


def test_register_voice_tools_exposes_gemini_tts():
    reg = register_voice_tools(_Ctx())  # type: ignore[arg-type]
    assert "gemini_tts" in reg.names()
