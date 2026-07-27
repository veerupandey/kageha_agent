"""Secure on-disk credential store for OAuth connections."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kageha.config import kageha_home

_FILE_MODE = 0o600
_DIR_MODE = 0o700


def connections_dir() -> Path:
    """``~/.kageha/connections`` (or ``$KAGEHA_HOME/connections``)."""
    d = kageha_home() / "connections"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, _DIR_MODE)
    except OSError:
        pass
    return d


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class ConnectionStore:
    """JSON files per provider under ``connections_dir()``, mode 0600."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else connections_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, _DIR_MODE)
        except OSError:
            pass

    def path_for(self, provider_id: str) -> Path:
        safe = "".join(c for c in provider_id if c.isalnum() or c in "-_")
        if not safe or safe != provider_id:
            raise ValueError(f"invalid provider id: {provider_id!r}")
        return self.root / f"{safe}.json"

    def exists(self, provider_id: str) -> bool:
        return self.path_for(provider_id).is_file()

    def load(self, provider_id: str) -> dict[str, Any] | None:
        path = self.path_for(provider_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return data

    def save(self, provider_id: str, payload: dict[str, Any]) -> Path:
        path = self.path_for(provider_id)
        body = dict(payload)
        body.setdefault("provider", provider_id)
        body["updated_at"] = _utcnow_iso()
        text = json.dumps(body, indent=2, sort_keys=True) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{provider_id}.",
            suffix=".tmp",
            dir=str(self.root),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp_path, _FILE_MODE)
            os.replace(tmp_path, path)
            try:
                os.chmod(path, _FILE_MODE)
            except OSError:
                pass
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return path

    def delete(self, provider_id: str) -> bool:
        path = self.path_for(provider_id)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def list_stored(self) -> list[str]:
        out: list[str] = []
        for p in sorted(self.root.glob("*.json")):
            out.append(p.stem)
        return out

    def file_mode(self, provider_id: str) -> int | None:
        path = self.path_for(provider_id)
        if not path.is_file():
            return None
        return stat.S_IMODE(path.stat().st_mode)
