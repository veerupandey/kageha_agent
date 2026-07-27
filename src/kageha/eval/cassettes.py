"""Cassette record/replay for deterministic model tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from kageha.models.base import ChatMessage, ChatResponse, ChatUsage, ToolCall


class CassetteStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def key(self, messages: list[ChatMessage], model: str) -> str:
        blob = json.dumps(
            {
                "model": model,
                "messages": [
                    {
                        "role": m.role,
                        "content": m.content,
                        "tool_calls": [
                            {"name": t.name, "arguments": t.arguments} for t in m.tool_calls
                        ],
                    }
                    for m in messages
                ],
            },
            sort_keys=True,
        )
        return hashlib.sha1(blob.encode()).hexdigest()

    def path_for(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def load(self, key: str) -> ChatResponse | None:
        p = self.path_for(key)
        if not p.is_file():
            return None
        data = json.loads(p.read_text())
        msg = data["message"]
        return ChatResponse(
            message=ChatMessage(
                role=msg["role"],
                content=msg.get("content") or "",
                tool_calls=[
                    ToolCall(id=t.get("id", "c"), name=t["name"], arguments=t.get("arguments") or {})
                    for t in msg.get("tool_calls") or []
                ],
            ),
            usage=ChatUsage(**(data.get("usage") or {})),
            model=data.get("model") or "",
            stop_reason=data.get("stop_reason") or "stop",
        )

    def save(self, key: str, response: ChatResponse) -> None:
        data = {
            "model": response.model,
            "stop_reason": response.stop_reason,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "cached_tokens": response.usage.cached_tokens,
            },
            "message": {
                "role": response.message.role,
                "content": response.message.content,
                "tool_calls": [
                    {
                        "id": t.id,
                        "name": t.name,
                        "arguments": t.arguments,
                    }
                    for t in response.message.tool_calls
                ],
            },
        }
        self.path_for(key).write_text(json.dumps(data, indent=2))


class CassetteModel:
    """Wrap a ChatModel with record/replay."""

    def __init__(self, inner: Any, store: CassetteStore, *, mode: str = "replay") -> None:
        self.inner = inner
        self.store = store
        self.mode = mode  # replay | record | live
        self.model_id = getattr(inner, "model_id", "cassette")
        self.provider = getattr(inner, "provider", "cassette")

    async def chat(self, messages, tools=None, **kwargs):  # noqa: ANN001
        key = self.store.key(messages, self.model_id)
        if self.mode in {"replay", "record"}:
            cached = self.store.load(key)
            if cached is not None:
                return cached
            if self.mode == "replay":
                raise FileNotFoundError(f"Missing cassette {key}")
        resp = await self.inner.chat(messages, tools, **kwargs)
        if self.mode == "record":
            self.store.save(key, resp)
        return resp

    async def smoke(self) -> str:
        return await self.inner.smoke()
