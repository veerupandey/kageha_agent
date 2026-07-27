"""Project/user KB attach lists."""

from __future__ import annotations

from pathlib import Path

import yaml

from kageha.config import kageha_home


def _paths() -> list[Path]:
    return [
        kageha_home() / "kb.yaml",
        Path.cwd() / ".kageha" / "kb.yaml",
    ]


def attached_kbs() -> list[str]:
    ids: list[str] = []
    for p in _paths():
        if p.is_file():
            data = yaml.safe_load(p.read_text()) or {}
            for kid in data.get("knowledge_bases") or []:
                if kid not in ids:
                    ids.append(kid)
    return ids


def attach(kb_id: str, *, project: bool = True) -> Path:
    target = Path.cwd() / ".kageha" / "kb.yaml" if project else kageha_home() / "kb.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if target.is_file():
        data = yaml.safe_load(target.read_text()) or {}
    lst = list(data.get("knowledge_bases") or [])
    if kb_id not in lst:
        lst.append(kb_id)
    data["knowledge_bases"] = lst
    target.write_text(yaml.safe_dump(data, sort_keys=False))
    return target


def detach(kb_id: str, *, project: bool = True) -> None:
    target = Path.cwd() / ".kageha" / "kb.yaml" if project else kageha_home() / "kb.yaml"
    if not target.is_file():
        return
    data = yaml.safe_load(target.read_text()) or {}
    data["knowledge_bases"] = [x for x in (data.get("knowledge_bases") or []) if x != kb_id]
    target.write_text(yaml.safe_dump(data, sort_keys=False))
