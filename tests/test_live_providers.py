"""Credentialed provider qualification; excluded from ordinary test runs."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kageha.runtime.providers import ProviderControlPlane
from kageha.runtime.store import RuntimeStore


pytestmark = pytest.mark.live_provider


@pytest.mark.asyncio
async def test_three_independent_providers_are_healthy(tmp_path: Path):
    if os.environ.get("KAGEHA_LIVE_TESTS") != "1":
        pytest.skip("set KAGEHA_LIVE_TESTS=1 for billed provider checks")
    store = RuntimeStore(tmp_path / "runtime.db")
    try:
        health = await ProviderControlPlane(store).check_all(deep=True)
        assert {item.provider for item in health if item.available} == {
            "gemini",
            "openai",
            "siliconflow",
        }
    finally:
        store.close()
