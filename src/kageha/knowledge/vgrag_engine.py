"""Multi-hop Graph KB via Vector Graph RAG (Milvus Lite), with Zvec-like fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kageha.knowledge.zvec_engine import ZvecEngine, _load_source


class VgragEngine:
    name = "vgrag"

    def _rag(self, root: Path) -> Any:
        try:
            from vector_graph_rag import VectorGraphRAG  # type: ignore
        except ImportError as e:
            raise ImportError(
                "GraphRAG extra not installed. Run: uv sync --extra graphrag"
            ) from e
        db = str(root / "vgrag" / "milvus.db")
        (root / "vgrag").mkdir(parents=True, exist_ok=True)
        return VectorGraphRAG(milvus_uri=db, collection_prefix=root.name)

    def create(self, kb_id: str, root: Path) -> None:
        (root / "vgrag").mkdir(exist_ok=True)
        # Also prepare flat fallback store for environments without VGRAG
        ZvecEngine().create(kb_id, root)

    def ingest(self, kb_id: str, root: Path, sources: list[str]) -> dict[str, Any]:
        texts = []
        for src in sources:
            texts.append(_load_source(src, root / "raw"))
        try:
            rag = self._rag(root)
            # Prefer rebuild for small sets; upsert when available
            if hasattr(rag, "rebuild_texts"):
                rag.rebuild_texts(texts)
            elif hasattr(rag, "add_texts"):
                rag.add_texts(texts)
            return {"passages": len(texts), "engine": "vgrag", "sources": len(sources)}
        except ImportError:
            # Fallback to flat engine so KB still works offline
            return {
                **ZvecEngine().ingest(kb_id, root, sources),
                "engine": "vgrag-fallback-zvec",
                "warning": "vector-graph-rag not installed; used zvec fallback",
            }

    def search(self, kb_id: str, root: Path, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        try:
            result = self.query(kb_id, root, query)
            passages = result.get("passages") or []
            return passages[:top_k]
        except Exception:
            return ZvecEngine().search(kb_id, root, query, top_k=top_k)

    def query(self, kb_id: str, root: Path, query: str) -> dict[str, Any]:
        try:
            rag = self._rag(root)
            result = rag.query(query)
            answer = getattr(result, "answer", None) or str(result)
            passages = []
            for p in getattr(result, "passages", None) or getattr(result, "contexts", None) or []:
                if isinstance(p, dict):
                    passages.append(p)
                else:
                    passages.append({"text": str(p)})
            return {"answer": answer, "passages": passages, "engine": "vgrag"}
        except ImportError:
            fb = ZvecEngine().query(kb_id, root, query)
            fb["engine"] = "vgrag-fallback-zvec"
            return fb
