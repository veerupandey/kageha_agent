"""Embedding client with Gemini-first defaults and OpenAI-compat fallback.

Config (highest precedence first):
  1. Env: ``KAGEHA_EMBEDDING_PROVIDER``, ``KAGEHA_EMBEDDING_MODEL``,
     ``KAGEHA_EMBEDDING_DIMENSIONS``
  2. ``models.yaml`` ``embedding:`` block (copy to ``~/.kageha/models.yaml``)
  3. Built-in Gemini defaults
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from kageha.config import env_key
from kageha.models.registry import ModelRegistry

# Gemini embedding-001 supports Matryoshka dims; 768 is a good quality/size tradeoff.
_DEFAULT_GEMINI_MODEL = "gemini-embedding-001"
_DEFAULT_GEMINI_DIM = 768
_DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
_DEFAULT_OPENAI_DIM = 1536


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str
    model: str
    dimensions: int
    api_key: str
    base_url: str


def resolve_embedding_config(registry: ModelRegistry | None = None) -> EmbeddingConfig | None:
    """Pick an embedding backend from env + models.yaml + available API keys.

    Preference order:
      1. Env / yaml preferred provider if its key is present
      2. Gemini (default stack)
      3. OpenAI / SiliconFlow openai-compat
    """
    reg = registry or ModelRegistry.load()
    cfg = dict(reg.embedding or {})
    env_provider = (os.environ.get("KAGEHA_EMBEDDING_PROVIDER") or "").strip().lower()
    env_model = (os.environ.get("KAGEHA_EMBEDDING_MODEL") or "").strip()
    env_dim_raw = (os.environ.get("KAGEHA_EMBEDDING_DIMENSIONS") or "").strip()
    preferred = env_provider or (cfg.get("provider") or "gemini").lower()
    model = env_model or str(cfg.get("model") or "")
    if env_dim_raw.isdigit():
        dim: int | None = int(env_dim_raw)
    else:
        dim = int(cfg.get("dimensions") or 0) or None

    candidates: list[tuple[str, str, int, str, str]] = []

    def _add(provider: str, default_model: str, default_dim: int) -> None:
        pc = reg.providers.get(provider)
        if not pc:
            return
        key = env_key(pc.api_key_env)
        if not key:
            return
        # Explicit model/dim from env or yaml apply to the preferred provider;
        # other candidates keep their provider defaults.
        if preferred == provider and model:
            m = model
        else:
            m = default_model
        if preferred == provider and dim:
            d = dim
        else:
            d = default_dim
        candidates.append((provider, m, d, key, pc.base_url or ""))

    # Prefer configured provider first, then Gemini, then openai-compat.
    order = [preferred]
    for p in ("gemini", "openai", "siliconflow"):
        if p not in order:
            order.append(p)

    for p in order:
        if p == "gemini":
            _add("gemini", _DEFAULT_GEMINI_MODEL, _DEFAULT_GEMINI_DIM)
        elif p in ("openai", "siliconflow"):
            _add(p, _DEFAULT_OPENAI_MODEL, _DEFAULT_OPENAI_DIM)

    if not candidates:
        return None
    provider, model_name, dimensions, api_key, base_url = candidates[0]
    if provider == "gemini" and not base_url:
        base_url = "https://generativelanguage.googleapis.com/v1beta"
    if provider != "gemini" and not base_url:
        base_url = "https://api.openai.com/v1"
    return EmbeddingConfig(
        provider=provider,
        model=model_name,
        dimensions=dimensions,
        api_key=api_key,
        base_url=base_url.rstrip("/"),
    )


class EmbeddingClient:
    def __init__(self, config: EmbeddingConfig) -> None:
        self.config = config

    @classmethod
    def from_registry(cls, registry: ModelRegistry | None = None) -> EmbeddingClient | None:
        cfg = resolve_embedding_config(registry)
        return cls(cfg) if cfg else None

    async def embed(
        self,
        texts: list[str],
        *,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> list[list[float]]:
        if not texts:
            return []
        if self.config.provider == "gemini":
            return await self._embed_gemini(texts, task_type=task_type)
        return await self._embed_openai_compat(texts)

    async def _embed_gemini(
        self,
        texts: list[str],
        *,
        task_type: str,
    ) -> list[list[float]]:
        url = f"{self.config.base_url}/models/{self.config.model}:batchEmbedContents"
        headers = {
            "x-goog-api-key": self.config.api_key,
            "Content-Type": "application/json",
        }
        requests = []
        for text in texts:
            req: dict[str, Any] = {
                "model": f"models/{self.config.model}",
                "content": {"parts": [{"text": text}]},
                "taskType": task_type,
            }
            if self.config.dimensions:
                req["outputDimensionality"] = self.config.dimensions
            requests.append(req)
        payload = {"requests": requests}
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                # Single-text fallback for older endpoints / smaller batches.
                out: list[list[float]] = []
                for text in texts:
                    one = await self._embed_gemini_one(
                        client, headers, text, task_type=task_type
                    )
                    out.append(one)
                return out
            data = resp.json()
        embeddings = data.get("embeddings") or []
        if len(embeddings) != len(texts):
            raise RuntimeError(
                f"Gemini embed count mismatch: got {len(embeddings)} for {len(texts)} texts"
            )
        return [list(e.get("values") or []) for e in embeddings]

    async def _embed_gemini_one(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        text: str,
        *,
        task_type: str,
    ) -> list[float]:
        url = f"{self.config.base_url}/models/{self.config.model}:embedContent"
        payload: dict[str, Any] = {
            "model": f"models/{self.config.model}",
            "content": {"parts": [{"text": text}]},
            "taskType": task_type,
        }
        if self.config.dimensions:
            payload["outputDimensionality"] = self.config.dimensions
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            detail = (resp.text or "").replace("\n", " ")[:500]
            raise httpx.HTTPStatusError(
                f"Client error '{resp.status_code}' for url '{resp.request.url}' — {detail}",
                request=resp.request,
                response=resp,
            )
        data = resp.json()
        values = ((data.get("embedding") or {}).get("values")) or []
        return list(values)

    async def _embed_openai_compat(self, texts: list[str]) -> list[list[float]]:
        url = f"{self.config.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.config.model,
            "input": texts,
        }
        if self.config.dimensions and self.config.model.startswith("text-embedding-3"):
            payload["dimensions"] = self.config.dimensions
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                detail = (resp.text or "").replace("\n", " ")[:500]
                raise httpx.HTTPStatusError(
                    f"Client error '{resp.status_code}' for url '{resp.request.url}' — {detail}",
                    request=resp.request,
                    response=resp,
                )
            data = resp.json()
        items = sorted(data.get("data") or [], key=lambda x: x.get("index", 0))
        return [list(i.get("embedding") or []) for i in items]


def embed_texts_sync(
    texts: list[str],
    *,
    registry: ModelRegistry | None = None,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> list[list[float]] | None:
    """Sync helper for engines that are not async yet. Returns None if unavailable."""
    client = EmbeddingClient.from_registry(registry)
    if client is None:
        return None
    import asyncio

    async def _run() -> list[list[float]]:
        return await client.embed(texts, task_type=task_type)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # Avoid nested-loop deadlock; caller should use async path.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()
    return asyncio.run(_run())
