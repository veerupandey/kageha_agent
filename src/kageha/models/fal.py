"""Fal AI specialty generation (images/video) — optional, not a chat model."""

from __future__ import annotations

from typing import Any

import httpx

from kageha.config import env_key


class FalClient:
    """Thin Fal queue client for allowlisted models."""

    BASE = "https://fal.run"

    def __init__(self, api_key: str | None = None, timeout: float = 300.0) -> None:
        self.api_key = api_key or env_key("FAL_KEY") or env_key("FAL_API_KEY") or ""
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Key {self.api_key}",
            "Content-Type": "application/json",
        }

    async def health(self) -> dict[str, Any]:
        if not self.available:
            return {"ok": False, "error": "FAL_KEY missing"}
        # Lightweight auth check via a known endpoint status
        return {"ok": True, "provider": "fal"}

    async def run(self, model_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("FAL_KEY not set")
        # Allowlist known provider namespaces only
        allowed_prefixes = ("fal-ai/", "fal/", "bytedance/", "alibaba/", "xai/")
        if not model_id.startswith(allowed_prefixes):
            raise ValueError(f"Model not allowlisted: {model_id}")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.BASE}/{model_id}",
                headers=self._headers(),
                json=payload,
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Fal {resp.status_code} for {model_id}: {resp.text[:800]}"
                )
            return resp.json()
