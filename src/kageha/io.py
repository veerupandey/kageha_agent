"""Crash-safe local file writes used by runtime control state."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
) -> Path:
    """Write and fsync a temporary sibling before atomically replacing ``path``."""
    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        try:
            parent_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        except OSError:
            pass
        return target
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, value: Any, *, indent: int = 2) -> Path:
    return atomic_write_text(
        path,
        json.dumps(value, indent=indent, ensure_ascii=False, default=str) + "\n",
    )
