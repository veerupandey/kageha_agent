"""Engine-agnostic KnowledgeBase facade."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

from kageha.config import kb_root


@runtime_checkable
class KBEngine(Protocol):
    name: str

    def create(self, kb_id: str, root: Path) -> None: ...
    def ingest(self, kb_id: str, root: Path, sources: list[str]) -> dict[str, Any]: ...
    def search(self, kb_id: str, root: Path, query: str, top_k: int = 5) -> list[dict[str, Any]]: ...
    def query(self, kb_id: str, root: Path, query: str) -> dict[str, Any]: ...


@dataclass
class KnowledgeBase:
    kb_id: str
    engine: str
    root: Path
    manifest: dict

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.yaml"

    def save_manifest(self) -> None:
        self.manifest_path.write_text(yaml.safe_dump(self.manifest, sort_keys=False))


def kb_path(kb_id: str) -> Path:
    return kb_root() / kb_id


def load_manifest(kb_id: str) -> dict:
    path = kb_path(kb_id) / "manifest.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"KB not found: {kb_id}")
    return yaml.safe_load(path.read_text()) or {}


def list_kbs() -> list[dict]:
    root = kb_root()
    out = []
    for d in sorted(root.iterdir()) if root.exists() else []:
        if d.is_dir() and (d / "manifest.yaml").is_file():
            m = yaml.safe_load((d / "manifest.yaml").read_text()) or {}
            out.append({"id": d.name, **m})
    return out


class KnowledgeFacade:
    def __init__(self) -> None:
        self.engines: dict[str, KBEngine] = {}
        self._load_engines()

    def _load_engines(self) -> None:
        # Built-ins with lazy import inside engine modules
        from kageha.knowledge.zvec_engine import ZvecEngine
        from kageha.knowledge.vgrag_engine import VgragEngine

        for eng in (ZvecEngine(), VgragEngine()):
            self.engines[eng.name] = eng

    def create(self, kb_id: str, engine: str = "zvec", sources: list[str] | None = None) -> KnowledgeBase:
        if engine not in self.engines:
            raise ValueError(f"Unknown engine {engine}. Available: {list(self.engines)}")
        root = kb_path(kb_id)
        root.mkdir(parents=True, exist_ok=True)
        (root / "raw").mkdir(exist_ok=True)
        eng = self.engines[engine]
        eng.create(kb_id, root)
        manifest = {
            "id": kb_id,
            "engine": engine,
            "sources": sources or [],
        }
        kb = KnowledgeBase(kb_id=kb_id, engine=engine, root=root, manifest=manifest)
        kb.save_manifest()
        if sources:
            self.ingest(kb_id, sources)
            kb.manifest = load_manifest(kb_id)
        return kb

    def ingest(self, kb_id: str, sources: list[str]) -> dict[str, Any]:
        manifest = load_manifest(kb_id)
        eng = self.engines[manifest["engine"]]
        root = kb_path(kb_id)
        result = eng.ingest(kb_id, root, sources)
        seen = list(manifest.get("sources") or [])
        for s in sources:
            if s not in seen:
                seen.append(s)
        manifest["sources"] = seen
        manifest["stats"] = result
        (root / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
        return result

    def search(self, kb_id: str, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        manifest = load_manifest(kb_id)
        eng = self.engines[manifest["engine"]]
        return eng.search(kb_id, kb_path(kb_id), query, top_k=top_k)

    def query(self, kb_id: str, query: str) -> dict[str, Any]:
        manifest = load_manifest(kb_id)
        eng = self.engines[manifest["engine"]]
        return eng.query(kb_id, kb_path(kb_id), query)

    def delete(self, kb_id: str) -> None:
        import shutil

        root = kb_path(kb_id)
        if root.exists():
            shutil.rmtree(root)
