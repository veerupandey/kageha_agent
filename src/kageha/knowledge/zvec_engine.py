"""Flat semantic KB engine backed by Zvec (with JSONL fallback)."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import httpx


def _require_extra() -> Any:
    try:
        import zvec  # type: ignore

        return zvec
    except ImportError as e:
        raise ImportError(
            "Zvec extra not installed. Run: uv sync --extra zvec"
        ) from e


def _chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks = []
    i = 0
    while i < len(text):
        chunks.append(text[i : i + size])
        i += max(1, size - overlap)
    return chunks


def _simple_embed(text: str, dim: int = 64) -> list[float]:
    """Deterministic bag-of-hashes embedding for offline/fallback use."""
    vec = [0.0] * dim
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if not tokens:
        return vec
    for tok in tokens:
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    """True cosine similarity in ``[0, 1]``-ish range for unit-ish embeddings."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom <= 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / denom))


def _close_collection(collection: Any) -> None:
    """Close Zvec collections on versions that expose an explicit close."""
    close = getattr(collection, "close", None)
    if callable(close):
        close()


def _meta_path(root: Path) -> Path:
    return root / "zvec" / "embed_meta.json"


def _load_meta(root: Path) -> dict[str, Any]:
    path = _meta_path(root)
    if not path.is_file():
        return {"provider": "hash", "model": "bag-of-hashes", "dimensions": 64}
    try:
        return json.loads(path.read_text()) or {}
    except Exception:
        return {"provider": "hash", "model": "bag-of-hashes", "dimensions": 64}


def _write_meta(root: Path, meta: dict[str, Any]) -> None:
    path = _meta_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2) + "\n")


def _resolve_embed_backend() -> dict[str, Any]:
    """Return meta describing the active embedder (API or hash)."""
    try:
        from kageha.models.embeddings import resolve_embedding_config

        cfg = resolve_embedding_config()
        if cfg:
            return {
                "provider": cfg.provider,
                "model": cfg.model,
                "dimensions": cfg.dimensions,
            }
    except Exception:
        pass
    return {"provider": "hash", "model": "bag-of-hashes", "dimensions": 64}


def _embed_many(
    texts: list[str],
    *,
    meta: dict[str, Any],
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> list[list[float]]:
    if not texts:
        return []
    if meta.get("provider") == "hash":
        dim = int(meta.get("dimensions") or 64)
        return [_simple_embed(t, dim=dim) for t in texts]
    from kageha.models.embeddings import EmbeddingClient

    client = EmbeddingClient.from_registry()
    if client is None:
        raise RuntimeError(
            f"KB expects {meta.get('provider')} embeddings but no API key is available"
        )
    # Batch to stay under provider request limits.
    out: list[list[float]] = []
    batch_size = 32
    import asyncio

    async def _batched() -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]
            vectors.extend(await client.embed(chunk, task_type=task_type))
        return vectors

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            out = pool.submit(asyncio.run, _batched()).result()
    else:
        out = asyncio.run(_batched())
    if len(out) != len(texts):
        raise RuntimeError(f"Embed count mismatch: got {len(out)} for {len(texts)} texts")
    return out


class ZvecEngine:
    name = "zvec"

    def create(self, kb_id: str, root: Path) -> None:
        (root / "zvec").mkdir(exist_ok=True)
        (root / "chunks.jsonl").touch()
        meta = _resolve_embed_backend()
        _write_meta(root, meta)
        dim = int(meta.get("dimensions") or 64)
        # Try real zvec; otherwise JSONL+embed is fine
        try:
            zvec = _require_extra()
            schema = zvec.CollectionSchema(
                name=kb_id,
                vectors=zvec.VectorSchema("embedding", zvec.DataType.VECTOR_FP32, dim),
            )
            path = str(root / "zvec" / "collection")
            if not Path(path).exists():
                _close_collection(zvec.create_and_open(path=path, schema=schema))
        except ImportError:
            (root / "zvec" / "FALLBACK").write_text("jsonl-embed\n")

    def ingest(self, kb_id: str, root: Path, sources: list[str]) -> dict[str, Any]:
        chunks_path = root / "chunks.jsonl"
        meta = _load_meta(root)
        if not _meta_path(root).is_file():
            meta = _resolve_embed_backend()
            _write_meta(root, meta)
        n = 0
        docs: list[dict[str, Any]] = []
        pending_texts: list[str] = []
        pending_meta: list[tuple[str, str]] = []
        for src in sources:
            text = _load_source(src, root / "raw")
            for i, chunk in enumerate(_chunk_text(text)):
                cid = hashlib.sha1(f"{src}:{i}:{chunk[:64]}".encode()).hexdigest()[:16]
                pending_texts.append(chunk)
                pending_meta.append((cid, src))
                with chunks_path.open("a") as f:
                    f.write(json.dumps({"id": cid, "source": src, "text": chunk}) + "\n")
                n += 1
        try:
            embeddings = _embed_many(
                pending_texts,
                meta=meta,
                task_type="RETRIEVAL_DOCUMENT",
            )
        except (httpx.HTTPError, OSError, RuntimeError, TimeoutError) as exc:
            # Quota, network, or provider failures must not make an offline KB unusable.
            # Keep the configured dimension so the already-created Zvec schema remains valid.
            meta = {
                "provider": "hash",
                "model": "bag-of-hashes-fallback",
                "dimensions": int(meta.get("dimensions") or 64),
                "fallback_reason": type(exc).__name__,
            }
            _write_meta(root, meta)
            embeddings = _embed_many(
                pending_texts,
                meta=meta,
                task_type="RETRIEVAL_DOCUMENT",
            )
        for (cid, src), chunk, emb in zip(pending_meta, pending_texts, embeddings):
            docs.append(
                {
                    "id": cid,
                    "source": src,
                    "text": chunk,
                    "embedding": emb,
                }
            )
        # Always persist embeddings for cosine rescoring / offline fallback.
        emb_path = root / "zvec" / "embeddings.jsonl"
        with emb_path.open("a") as f:
            for d in docs:
                f.write(json.dumps(d) + "\n")
        # Upsert into zvec if available
        try:
            zvec = _require_extra()
            path = str(root / "zvec" / "collection")
            col = zvec.open(path)
            col.insert(
                [
                    zvec.Doc(
                        id=d["id"],
                        vectors={"embedding": d["embedding"]},
                        fields={"text": d["text"], "source": d["source"]},
                    )
                    for d in docs
                ]
            )
            _close_collection(col)
        except Exception:
            pass
        return {
            "chunks": n,
            "sources": len(sources),
            "embedding": {
                "provider": meta.get("provider"),
                "model": meta.get("model"),
                "dimensions": meta.get("dimensions"),
            },
        }

    def search(self, kb_id: str, root: Path, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        meta = _load_meta(root)
        q = _embed_many([query], meta=meta, task_type="RETRIEVAL_QUERY")[0]
        emb_path = root / "zvec" / "embeddings.jsonl"
        by_id: dict[str, dict[str, Any]] = {}
        if emb_path.is_file():
            for line in emb_path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                doc_id = str(d.get("id") or "")
                if doc_id:
                    by_id[doc_id] = d

        # Prefer zvec for candidate generation, then rescore with true cosine.
        try:
            zvec = _require_extra()
            path = str(root / "zvec" / "collection")
            col = zvec.open(path)
            # Pull a wider candidate set so cosine rescoring can reorder well.
            results = col.query(
                queries=zvec.Query("embedding", vector=q),
                topk=max(top_k, min(50, top_k * 4)),
            )
            _close_collection(col)
            out = []
            for r in results or []:
                fields = getattr(r, "fields", None) or {}
                doc_id = str(getattr(r, "id", "") or "")
                stored = by_id.get(doc_id) or {}
                emb = stored.get("embedding") or []
                score = (
                    _cosine(q, emb)
                    if emb
                    else float(getattr(r, "score", 0.0) or 0.0)
                )
                out.append(
                    {
                        "id": doc_id,
                        "text": fields.get("text") or stored.get("text") or str(r),
                        "score": score,
                        "source": fields.get("source") or stored.get("source") or "",
                    }
                )
            if out:
                out.sort(key=lambda row: float(row.get("score") or 0.0), reverse=True)
                return out[:top_k]
        except Exception:
            pass
        # Fallback scan over persisted embeddings
        scored: list[tuple[float, dict]] = []
        for d in by_id.values():
            score = _cosine(q, d.get("embedding") or [])
            scored.append((score, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "id": d["id"],
                "text": d["text"],
                "score": s,
                "source": d.get("source", ""),
            }
            for s, d in scored[:top_k]
        ]

    def query(self, kb_id: str, root: Path, query: str) -> dict[str, Any]:
        hits = self.search(kb_id, root, query, top_k=5)
        return {
            "answer": "\n\n".join(h["text"] for h in hits),
            "passages": hits,
            "engine": "zvec",
        }


def _load_source(src: str, raw_dir: Path) -> str:
    raw_dir.mkdir(parents=True, exist_ok=True)
    p = Path(src)
    if p.is_file():
        data = p.read_bytes()
        dest = raw_dir / p.name
        dest.write_bytes(data)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("latin-1", errors="replace")
    if src.startswith("http://") or src.startswith("https://"):
        import httpx

        resp = httpx.get(src, timeout=60.0, follow_redirects=True)
        resp.raise_for_status()
        name = hashlib.sha1(src.encode()).hexdigest()[:12] + ".html"
        (raw_dir / name).write_bytes(resp.content)
        return resp.text
    # Treat as literal text
    return src
