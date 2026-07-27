"""OpenAI-compatible chat/completions (OpenAI, SiliconFlow, Groq, vLLM, …)."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from kageha.models.base import (
    ChatMessage,
    ChatResponse,
    ChatUsage,
    StreamDelta,
    ToolCall,
    ToolSpec,
)


class OpenAICompatModel:
    def __init__(
        self,
        *,
        model_id: str,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        timeout: float = 120.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.model_id = model_id
        self.provider = provider
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.extra_headers = dict(extra_headers or {})

    def _headers(self) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        h.update(self.extra_headers)
        return h

    def _serialize_messages(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.tool_call_id or "",
                        "content": m.content,
                    }
                )
                continue
            item: dict[str, Any] = {"role": m.role, "content": m.content or ""}
            if m.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ]
            out.append(item)
        return out

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._serialize_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [t.openai_schema() for t in tools]
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        msg = choice.get("message") or {}
        # Some models (e.g. Kimi thinking) put text in reasoning_content
        content = msg.get("content") or msg.get("reasoning_content") or ""
        tool_calls: list[ToolCall] = []
        for raw in msg.get("tool_calls") or []:
            fn = raw.get("function") or {}
            args_raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
            except json.JSONDecodeError:
                args = {"_raw": args_raw}
            tool_calls.append(
                ToolCall(
                    id=raw.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    name=fn.get("name") or "",
                    arguments=args if isinstance(args, dict) else {"value": args},
                )
            )
        usage_raw = data.get("usage") or {}
        details = usage_raw.get("prompt_tokens_details") or {}
        usage = ChatUsage(
            prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
            completion_tokens=int(usage_raw.get("completion_tokens") or 0),
            cached_tokens=int(details.get("cached_tokens") or 0),
        )
        return ChatResponse(
            message=ChatMessage(
                role="assistant",
                content=content if isinstance(content, str) else str(content),
                tool_calls=tool_calls,
            ),
            usage=usage,
            model=data.get("model") or self.model,
            raw=data,
            stop_reason=choice.get("finish_reason") or "stop",
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> AsyncIterator[StreamDelta]:
        if tools:
            raise NotImplementedError(
                "OpenAI-compat streaming with tools is not supported yet"
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._serialize_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[len("data:") :].strip()
                    if not data_str or data_str == "[DONE]":
                        if data_str == "[DONE]":
                            break
                        continue
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        role = delta.get("role")
                        piece = delta.get("content")
                        if piece is None:
                            piece = delta.get("reasoning_content")
                        if not piece:
                            continue
                        text = piece if isinstance(piece, str) else str(piece)
                        yield StreamDelta(
                            text=text,
                            role=str(role) if role else None,
                        )

    async def smoke(self) -> str:
        r = await self.chat(
            [ChatMessage(role="user", content="Reply with exactly: ok")],
            max_tokens=128,
        )
        return (r.message.content or "").strip()

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{self.base_url}/models", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        return [m.get("id", "") for m in data.get("data") or [] if m.get("id")]
