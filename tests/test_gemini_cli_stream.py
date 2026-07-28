"""Antigravity / gemini-cli best-effort stdout streaming."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kageha.models.base import ChatMessage
from kageha.models.gemini_cli import GeminiCliModel
from kageha.models.streaming import collect_stream


@pytest.mark.asyncio
async def test_gemini_cli_stream_chunks_stdout():
    model = GeminiCliModel(
        model_id="antigravity",
        provider="antigravity",
        model="gemini-flash",
    )

    class FakeStdout:
        def __init__(self) -> None:
            self._chunks = [b"Hel", b"lo", b""]

        async def read(self, _n: int) -> bytes:
            return self._chunks.pop(0)

    class FakeStderr:
        async def read(self, _n: int) -> bytes:
            return b""

    proc = AsyncMock()
    proc.stdout = FakeStdout()
    proc.stderr = FakeStderr()
    proc.returncode = 0
    proc.wait = AsyncMock(return_value=0)

    with (
        patch("shutil.which", return_value="/usr/bin/gemini"),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        resp = await collect_stream(
            model.stream([ChatMessage(role="user", content="hi")]),
            model_id=model.model_id,
        )
    assert resp.message.content == "Hello"
