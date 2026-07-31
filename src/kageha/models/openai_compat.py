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
from kageha.models.retry import raise_for_status as raise_http_status


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
        # Azure OpenAI accepts api-key as well as Bearer.
        if self.provider == "azure" or "openai.azure.com" in self.base_url:
            h["api-key"] = self.api_key
        h.update(self.extra_headers)
        return h

    def _completions_url(self) -> str:
        url = f"{self.base_url}/chat/completions"
        # /openai/v1 rejects api-version; classic /deployments/... needs it.
        if "/openai/v1" in self.base_url:
            return url
        if self.provider == "azure" or "openai.azure.com" in self.base_url:
            import os

            ver = (os.environ.get("AZURE_OPENAI_API_VERSION") or "").strip()
            if ver and "api-version=" not in url:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}api-version={ver}"
        return url

    def _token_limit_fields(self, max_tokens: int) -> dict[str, int]:
        """gpt-5.* (incl. Azure) rejects max_tokens — use max_completion_tokens."""
        model = (self.model or "").lower()
        if (
            self.provider == "azure"
            or "openai.azure.com" in self.base_url
            or model.startswith("gpt-5")
            or "o1" == model
            or model.startswith("o1-")
            or model.startswith("o3")
            or model.startswith("o4")
        ):
            return {"max_completion_tokens": max_tokens}
        return {"max_tokens": max_tokens}

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
            **self._token_limit_fields(max_tokens),
        }
        if tools:
            payload["tools"] = [t.openai_schema() for t in tools]
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                self._completions_url(),
                headers=self._headers(),
                json=payload,
            )
            raise_http_status(resp)
            data = resp.json()

        choice = data["choices"][0]
        msg = choice.get("message") or {}
        # Kimi / thinking models: keep reasoning_content out of the user reply.
        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or ""
        if isinstance(content, str):
            content_s = content
        else:
            content_s = str(content) if content else ""
        if isinstance(reasoning, str):
            reasoning_s = reasoning
        else:
            reasoning_s = str(reasoning) if reasoning else ""
        # Only fall back to reasoning when the provider sent no content at all.
        if not content_s.strip() and reasoning_s.strip():
            content_s, reasoning_s = reasoning_s, ""
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
                content=content_s,
                tool_calls=tool_calls,
                reasoning=reasoning_s.strip(),
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
        del effort
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._serialize_messages(messages),
            "temperature": temperature,
            **self._token_limit_fields(max_tokens),
            "stream": True,
        }
        if tools:
            payload["tools"] = [t.openai_schema() for t in tools]
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                self._completions_url(),
                headers=self._headers(),
                json=payload,
            ) as resp:
                if resp.status_code >= 400:
                    err_body = ""
                    try:
                        err_body = (await resp.aread()).decode("utf-8", errors="replace")[
                            :400
                        ]
                    except Exception:  # noqa: BLE001
                        err_body = ""
                    raise_http_status(resp, body=err_body)
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
                    usage_raw = chunk.get("usage") or {}
                    usage = None
                    if usage_raw:
                        details = usage_raw.get("prompt_tokens_details") or {}
                        usage = ChatUsage(
                            prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
                            completion_tokens=int(
                                usage_raw.get("completion_tokens") or 0
                            ),
                            cached_tokens=int(details.get("cached_tokens") or 0),
                        )
                    model_name = chunk.get("model") or self.model
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        role = delta.get("role")
                        finish = choice.get("finish_reason")
                        content_piece = delta.get("content")
                        reasoning_piece = delta.get("reasoning_content")
                        emitted = False
                        if reasoning_piece:
                            emitted = True
                            rtext = (
                                reasoning_piece
                                if isinstance(reasoning_piece, str)
                                else str(reasoning_piece)
                            )
                            yield StreamDelta(
                                reasoning=rtext,
                                role=str(role) if role else None,
                                finish_reason=str(finish) if finish else None,
                                usage=usage,
                                model=model_name,
                            )
                        if content_piece:
                            emitted = True
                            text = (
                                content_piece
                                if isinstance(content_piece, str)
                                else str(content_piece)
                            )
                            yield StreamDelta(
                                text=text,
                                role=str(role) if role else None,
                                finish_reason=str(finish) if finish else None,
                                usage=usage,
                                model=model_name,
                            )
                        for tc in delta.get("tool_calls") or []:
                            emitted = True
                            fn = tc.get("function") or {}
                            yield StreamDelta(
                                text="",
                                role=str(role) if role else None,
                                tool_call_index=int(tc.get("index") or 0),
                                tool_call_id=tc.get("id"),
                                tool_name=fn.get("name"),
                                arguments_json=str(fn.get("arguments") or ""),
                                finish_reason=str(finish) if finish else None,
                                usage=usage,
                                model=model_name,
                            )
                        if finish and not emitted:
                            yield StreamDelta(
                                text="",
                                role=str(role) if role else None,
                                finish_reason=str(finish),
                                usage=usage,
                                model=model_name,
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
