"""Anthropic Messages API compatible adapter."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from kageha.models.base import ChatMessage, ChatResponse, ChatUsage, ToolCall, ToolSpec


class AnthropicCompatModel:
    def __init__(
        self,
        *,
        model_id: str,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        timeout: float = 120.0,
        api_version: str = "2023-06-01",
    ) -> None:
        self.model_id = model_id
        self.provider = provider
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.api_version = api_version

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
            "Content-Type": "application/json",
        }

    def _split_system(self, messages: list[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        body: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
                continue
            if m.role == "tool":
                body.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id or "",
                                "content": m.content,
                            }
                        ],
                    }
                )
                continue
            if m.role == "assistant" and m.tool_calls:
                content: list[dict[str, Any]] = []
                if m.content:
                    content.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                body.append({"role": "assistant", "content": content})
                continue
            body.append({"role": m.role, "content": m.content or ""})
        return "\n\n".join(system_parts), body

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> ChatResponse:
        system, body = self._split_system(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": body,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            # Stable cacheable system prefix
            payload["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        if tools:
            # Manus/Claude pattern: cache breakpoint on the last tool so the
            # tools catalog sits in the cached prefix with system.
            schemas = [t.anthropic_schema() for t in tools]
            schemas[-1] = {**schemas[-1], "cache_control": {"type": "ephemeral"}}
            payload["tools"] = schemas

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/v1/messages",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in data.get("content") or []:
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text") or "")
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                        name=block.get("name") or "",
                        arguments=block.get("input") or {},
                    )
                )
        usage_raw = data.get("usage") or {}
        # Anthropic reports input_tokens separately from cache_read_input_tokens.
        # Normalize to inclusive prompt_tokens (OpenAI/Gemini style) so budget
        # accounting can treat cached_tokens as a subset.
        input_tokens = int(usage_raw.get("input_tokens") or 0)
        cache_read = int(usage_raw.get("cache_read_input_tokens") or 0)
        usage = ChatUsage(
            prompt_tokens=input_tokens + cache_read,
            completion_tokens=int(usage_raw.get("output_tokens") or 0),
            cached_tokens=cache_read,
        )
        return ChatResponse(
            message=ChatMessage(
                role="assistant",
                content="".join(text_parts),
                tool_calls=tool_calls,
            ),
            usage=usage,
            model=data.get("model") or self.model,
            raw=data,
            stop_reason=data.get("stop_reason") or "stop",
        )

    async def smoke(self) -> str:
        r = await self.chat(
            [ChatMessage(role="user", content="Reply with exactly: ok")],
            max_tokens=16,
        )
        return (r.message.content or "").strip()
