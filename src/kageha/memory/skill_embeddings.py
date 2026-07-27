"""Gemini-first embedding retrieval for skills.

Uses ``models.yaml`` ``embedding:`` (override with ``KAGEHA_EMBEDDING_*`` env).
Falls back silently when no embedding key is available — callers keep token match.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kageha.config import kageha_home
from kageha.models.embeddings import EmbeddingClient, resolve_embedding_config
from kageha.models.registry import ModelRegistry

if TYPE_CHECKING:
    from kageha.memory.skills import Skill


def skill_embed_enabled() -> bool:
    raw = (os.environ.get("KAGEHA_SKILL_EMBEDDINGS") or "auto").strip().lower()
    if raw in {"0", "false", "off", "no"}:
        return False
    if raw in {"1", "true", "on", "yes", "force"}:
        return True
    return True  # auto


def _cache_path() -> Path:
    return kageha_home() / "cache" / "skill_embeddings.json"


def _fingerprint(skill: "Skill") -> str:
    try:
        mtime = skill.path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    blob = f"{skill.name}\n{skill.description}\n{mtime}"
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _doc_text(skill: "Skill") -> str:
    # Keep short for cost; name + description carry most retrieval signal.
    return f"Skill: {skill.name}\n{skill.description}".strip()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


@dataclass
class SkillEmbedHit:
    name: str
    score: float


class SkillEmbeddingIndex:
    """On-disk cache of skill vectors keyed by fingerprint + embedding model."""

    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or ModelRegistry.load()
        self.path = _cache_path()
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            self._data = {"skills": {}}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = raw if isinstance(raw, dict) else {"skills": {}}
            self._data.setdefault("skills", {})
        except (OSError, json.JSONDecodeError):
            self._data = {"skills": {}}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def _cfg_meta(self) -> tuple[str, str, int] | None:
        cfg = resolve_embedding_config(self.registry)
        if cfg is None:
            return None
        return cfg.provider, cfg.model, int(cfg.dimensions)

    def ensure(self, skills: dict[str, "Skill"]) -> bool:
        """Embed any missing/stale skills. Returns False if embeddings unavailable."""
        if not skill_embed_enabled():
            return False
        meta = self._cfg_meta()
        if meta is None:
            return False
        provider, model, dimensions = meta
        cached_model = str(self._data.get("model") or "")
        cached_dim = int(self._data.get("dimensions") or 0)
        if cached_model != model or cached_dim != dimensions:
            self._data = {
                "provider": provider,
                "model": model,
                "dimensions": dimensions,
                "skills": {},
                "updated": time.time(),
            }

        client = EmbeddingClient.from_registry(self.registry)
        if client is None:
            return False

        pending: list[tuple[str, str, str]] = []  # name, fingerprint, text
        store: dict[str, Any] = self._data.setdefault("skills", {})
        for name, skill in skills.items():
            fp = _fingerprint(skill)
            entry = store.get(name) or {}
            if entry.get("fingerprint") == fp and entry.get("vector"):
                continue
            pending.append((name, fp, _doc_text(skill)))

        if pending:
            texts = [t for _, _, t in pending]
            vectors = embed_or_none(client, texts, task_type="RETRIEVAL_DOCUMENT")
            if vectors is None or len(vectors) != len(pending):
                return False
            for (name, fp, _), vec in zip(pending, vectors):
                store[name] = {"fingerprint": fp, "vector": vec}
            self._data["provider"] = provider
            self._data["model"] = model
            self._data["dimensions"] = dimensions
            self._data["updated"] = time.time()
            # Drop vectors for skills that no longer exist.
            for gone in list(store.keys()):
                if gone not in skills:
                    del store[gone]
            self._save()
        return True

    def search(self, query: str, *, limit: int = 5) -> list[SkillEmbedHit]:
        if not skill_embed_enabled() or not (query or "").strip():
            return []
        if not self._data.get("skills"):
            return []
        client = EmbeddingClient.from_registry(self.registry)
        if client is None:
            return []
        qvecs = embed_or_none(client, [query.strip()], task_type="RETRIEVAL_QUERY")
        if not qvecs:
            return []
        qv = qvecs[0]
        hits: list[SkillEmbedHit] = []
        for name, entry in (self._data.get("skills") or {}).items():
            vec = entry.get("vector") if isinstance(entry, dict) else None
            if not isinstance(vec, list) or not vec:
                continue
            score = _cosine(qv, [float(x) for x in vec])
            # Keep in sync with skills.EMBED_HIT_FLOOR (avoid circular import).
            if score > 0.22:
                hits.append(SkillEmbedHit(name=name, score=score))
        hits.sort(key=lambda h: (-h.score, h.name))
        return hits[:limit]


def embed_or_none(
    client: EmbeddingClient,
    texts: list[str],
    *,
    task_type: str,
) -> list[list[float]] | None:
    try:
        import asyncio

        async def _run() -> list[list[float]]:
            return await client.embed(texts, task_type=task_type)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, _run()).result()
        return asyncio.run(_run())
    except Exception:  # noqa: BLE001
        return None


_INDEX: SkillEmbeddingIndex | None = None


def get_skill_embedding_index(
    registry: ModelRegistry | None = None,
    *,
    force_new: bool = False,
) -> SkillEmbeddingIndex:
    global _INDEX
    if force_new or _INDEX is None:
        _INDEX = SkillEmbeddingIndex(registry)
    return _INDEX
