"""JSONL event log + simple redaction."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_SECRET = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|token|password|secret)\s*[:=]\s*['\"]?([^\s'\"]+)"
)
_SECRET_KEY = re.compile(
    r"(?i)^(api[_-]?key|authorization|bearer|access[_-]?token|"
    r"refresh[_-]?token|token|password|secret|client[_-]?secret)$"
)


def redact(value: Any) -> Any:
    if isinstance(value, str):
        from kageha.memory.security import inspect_memory_text

        inspected = inspect_memory_text(value)
        if inspected.blocked:
            return inspected.safe_text
        return _SECRET.sub(r"\1=***", value)
    if isinstance(value, dict):
        return {
            k: (
                "***"
                if _SECRET_KEY.search(str(k))
                else redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


@dataclass
class EventLog:
    path: Path | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    sink: Callable[[str, dict[str, Any]], None] | None = None

    def emit(self, kind: str, data: dict[str, Any] | None = None) -> None:
        evt = {
            "ts": time.time(),
            "kind": kind,
            "data": redact(data or {}),
        }
        self.events.append(evt)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(evt) + "\n")
        if self.sink is not None:
            self.sink(kind, dict(evt["data"]))
