"""Disposable vector derivative isolated from user-visible knowledge bases."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Any, Iterable

from kageha.config import kageha_home
from kageha.knowledge.vgrag_engine import VgragEngine
from kageha.knowledge.zvec_engine import ZvecEngine
from kageha.memory.models import MemoryRecord


def embedding_model_defined() -> bool:
    """True when env/models.yaml + API key resolve to a real embedding backend."""
    try:
        from kageha.models.embeddings import resolve_embedding_config

        return resolve_embedding_config() is not None
    except Exception:
        return False


def vector_mode() -> str:
    """Resolve memory vector mode.

    ``auto`` (default) enables zvec whenever an embedding model is defined,
    otherwise stays off (FTS-only). Explicit ``zvec`` / ``vgrag`` / ``off`` win.
    """
    value = (os.environ.get("KAGEHA_MEMORY_EMBEDDINGS") or "auto").strip().lower()
    if value not in {"auto", "off", "zvec", "vgrag"}:
        value = "auto"
    if value == "auto":
        return "zvec" if embedding_model_defined() else "off"
    return value


class MemoryVectorIndex:
    """Private index that returns IDs only; canonical SQLite always re-filters."""

    def __init__(self) -> None:
        self.requested_mode = (
            (os.environ.get("KAGEHA_MEMORY_EMBEDDINGS") or "auto").strip().lower()
        )
        if self.requested_mode not in {"auto", "off", "zvec", "vgrag"}:
            self.requested_mode = "auto"
        self.mode = vector_mode()
        self.engine = "zvec" if self.mode == "off" else self.mode
        self.root = kageha_home() / "memory" / "vector_index"
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    def _engine(self):
        return VgragEngine() if self.engine == "vgrag" else ZvecEngine()

    @property
    def _manifest(self) -> Path:
        return self.root / "manifest.json"

    def ensure(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._manifest.is_file():
                return
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / "sources").mkdir(exist_ok=True)
            self._engine().create("memory_index", self.root)
            self._manifest.write_text(
                json.dumps(
                    {
                        "engine": self.engine,
                        "authority": "sqlite",
                        "visibility": "private_memory_derivative",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

    def _record_path(self, record: MemoryRecord) -> Path:
        return self.root / "sources" / f"{record.id}.txt"

    def index(self, record: MemoryRecord) -> bool:
        if not self.enabled or record.state != "confirmed":
            return False
        try:
            with self._lock:
                self.ensure()
                path = self._record_path(record)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    f"memory_id: {record.id}\nkind: {record.kind}\n---\n{record.content}\n",
                    encoding="utf-8",
                )
                self._engine().ingest("memory_index", self.root, [str(path)])
            return True
        except Exception:
            return False

    def search(self, query: str, *, top_k: int = 12) -> list[dict[str, Any]]:
        if not self.enabled or not (query or "").strip():
            return []
        try:
            with self._lock:
                self.ensure()
                hits = self._engine().search(
                    "memory_index",
                    self.root,
                    query,
                    top_k=max(1, min(50, top_k)),
                )
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for hit in hits or []:
            text = str(hit.get("text") or "")
            match = re.search(r"(?m)^memory_id:\s*(\S+)", text)
            memory_id = match.group(1) if match else ""
            if not memory_id:
                source = Path(str(hit.get("source") or ""))
                if source.suffix == ".txt":
                    memory_id = source.stem
            if not memory_id:
                continue
            out.append(
                {
                    "memory_id": memory_id,
                    "score": float(hit.get("score") or 0.0),
                }
            )
        return out

    def rebuild(self, records: Iterable[MemoryRecord]) -> int:
        if not self.enabled:
            return 0
        with self._lock:
            if self.root.exists():
                shutil.rmtree(self.root)
            self.ensure()
        count = 0
        for record in records:
            if self.index(record):
                count += 1
        return count
