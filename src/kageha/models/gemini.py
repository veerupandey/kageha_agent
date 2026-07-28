"""Google Gemini REST adapter (no google-genai SDK — keep core light)."""

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

_TOOL_HISTORY_400 = (
    "function call turn",
    "functionresponse",
    "function_call",
    "thoughtsignature",
    "thought signature",
    "please ensure that function call",
)


def _is_tool_history_400(detail: str) -> bool:
    text = (detail or "").lower()
    return any(token in text for token in _TOOL_HISTORY_400)


def flatten_messages_for_gemini_retry(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Collapse structured tool turns to plain text (Gemini 400 recovery).

    Assistant tool-call turns become user breadcrumbs (not assistant text) so
    the model cannot echo ``[called tools: …]`` as the final answer.
    """
    out: list[ChatMessage] = []
    for m in messages:
        if m.role == "system":
            out.append(m)
            continue
        if m.role == "tool":
            out.append(
                ChatMessage(
                    role="user",
                    content=f"[tool:{m.name or 'tool'} result]\n{(m.content or '')[:4000]}",
                )
            )
            continue
        if m.role == "assistant" and m.tool_calls:
            names = ", ".join(tc.name for tc in m.tool_calls[:8])
            text = (m.content or "").strip()
            crumb = f"[prior step called {names}]"
            if text:
                crumb = f"{text[:500]}\n{crumb}"
            out.append(ChatMessage(role="user", content=crumb[:2000]))
            continue
        out.append(m)
    return out


class GeminiModel:
    def __init__(
        self,
        *,
        model_id: str,
        provider: str,
        model: str,
        api_key: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout: float = 120.0,
    ) -> None:
        self.model_id = model_id
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _is_gemini3(self) -> bool:
        m = (self.model or "").lower()
        return m.startswith("gemini-3") or "gemini-3" in m

    def _to_contents(self, messages: list[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
                continue
            if m.role == "tool":
                fr: dict[str, Any] = {
                    "name": m.name or "tool",
                    "response": {"result": m.content},
                }
                if m.tool_call_id:
                    fr["id"] = m.tool_call_id
                contents.append(
                    {
                        "role": "user",
                        "parts": [{"functionResponse": fr}],
                    }
                )
                continue
            if m.role == "assistant":
                parts: list[dict[str, Any]] = []
                if m.content:
                    parts.append({"text": m.content})
                for tc in m.tool_calls:
                    fc: dict[str, Any] = {
                        "name": tc.name,
                        "args": tc.arguments,
                    }
                    if tc.id:
                        fc["id"] = tc.id
                    part: dict[str, Any] = {"functionCall": fc}
                    sig = (tc.meta or {}).get("thought_signature")
                    if sig:
                        part["thoughtSignature"] = sig
                    parts.append(part)
                contents.append({"role": "model", "parts": parts or [{"text": ""}]})
                continue
            contents.append({"role": "user", "parts": [{"text": m.content or ""}]})
        contents = self._repair_contents(contents)
        # Gemini generateContent rejects histories that end on a model turn
        # (common when prior chat history is replayed without a fresh user msg).
        if contents and contents[-1].get("role") == "model":
            contents.append(
                {
                    "role": "user",
                    "parts": [{"text": "Continue from the previous turn."}],
                }
            )
        return "\n\n".join(system_parts), contents

    @staticmethod
    def _repair_contents(contents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop/convert orphaned functionResponse turns (history truncation)."""
        out: list[dict[str, Any]] = []
        expect_response = False
        for item in contents:
            parts = list(item.get("parts") or [])
            has_fc = any(isinstance(p, dict) and "functionCall" in p for p in parts)
            has_fr = any(isinstance(p, dict) and "functionResponse" in p for p in parts)
            role = item.get("role")
            if has_fr and not expect_response:
                # Orphan tool result — keep facts, drop invalid schema.
                bits: list[str] = []
                for p in parts:
                    fr = p.get("functionResponse") if isinstance(p, dict) else None
                    if not isinstance(fr, dict):
                        continue
                    name = fr.get("name") or "tool"
                    body = fr.get("response") or {}
                    if isinstance(body, dict):
                        body = body.get("result", body)
                    bits.append(f"[tool:{name} result]\n{body}")
                out.append(
                    {
                        "role": "user",
                        "parts": [{"text": "\n".join(bits)[:4000] or "[tool result]"}],
                    }
                )
                expect_response = False
                continue
            if has_fc:
                expect_response = True
                out.append(item)
                continue
            if has_fr:
                out.append(item)
                continue
            # Plain user/model text resets the tool-response expectation.
            if role == "user" or (role == "model" and not has_fc):
                expect_response = False
            out.append(item)
        return out

    def _thinking_config(
        self,
        *,
        tools: list[ToolSpec] | None,
        max_tokens: int,
        effort: str | None = None,
    ) -> dict[str, Any]:
        """Limit thinking so it cannot consume the whole output budget.

        Gemini 3.x uses thinkingLevel (minimal/low/medium/high).
        Gemini 2.5 uses thinkingBudget (0 disables on Flash).
        """
        from kageha.models.effort import gemini_thinking_level

        if self._is_gemini3():
            if effort:
                level = gemini_thinking_level(effort, has_tools=bool(tools))
            else:
                # Legacy heuristic when caller omits effort.
                level = "low" if tools else "minimal"
                if max_tokens >= 4096 and tools:
                    level = "medium"
            return {"thinkingLevel": level}
        # 2.5 Flash family — map effort to budgets.
        if not tools and (effort or "medium") == "low":
            return {"thinkingBudget": 0}
        budgets = {"low": 256, "medium": 1024, "high": 2048}
        budget = budgets.get((effort or "medium").lower(), 1024)
        if not tools:
            return {"thinkingBudget": min(budget, 512)}
        return {"thinkingBudget": min(budget, max(128, max_tokens // 4))}

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> ChatResponse:
        try:
            return await self._chat_once(
                messages,
                tools,
                temperature=temperature,
                max_tokens=max_tokens,
                effort=effort,
            )
        except httpx.HTTPStatusError as exc:
            detail = str(exc)
            if exc.response is None or exc.response.status_code != 400:
                raise
            if not _is_tool_history_400(detail):
                raise
            # Multi-step computer-use / tool loops often trip Gemini when history
            # truncation or model switches leave invalid functionCall pairs.
            flat = flatten_messages_for_gemini_retry(messages)
            return await self._chat_once(
                flat,
                tools,
                temperature=temperature,
                max_tokens=max_tokens,
                effort=effort,
            )

    async def _chat_once(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> ChatResponse:
        system, contents = self._to_contents(messages)
        gen_cfg: dict[str, Any] = {
            "maxOutputTokens": max_tokens,
        }
        # Gemini 3.6+ deprecates sampling params; keep temperature only on 2.x.
        if not self._is_gemini3():
            gen_cfg["temperature"] = temperature
        gen_cfg["thinkingConfig"] = self._thinking_config(
            tools=tools, max_tokens=max_tokens, effort=effort
        )

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": gen_cfg,
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [t.gemini_schema() for t in tools],
                }
            ]

        url = f"{self.base_url}/models/{self.model}:generateContent"
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 400:
                detail_raw = resp.text or ""
                # Tool-history 400s are recovered by flattening in chat(); don't
                # burn thinkingConfig retries that won't fix functionCall order.
                if _is_tool_history_400(detail_raw):
                    detail = detail_raw.replace("\n", " ")[:700]
                    raise httpx.HTTPStatusError(
                        f"Client error '400' for url '{resp.request.url}' — {detail}",
                        request=resp.request,
                        response=resp,
                    )
                if "thinkingConfig" in gen_cfg:
                    for alt in self._thinking_fallbacks(
                        tools=tools, max_tokens=max_tokens, effort=effort
                    ):
                        if alt is None:
                            gen_cfg.pop("thinkingConfig", None)
                        else:
                            gen_cfg["thinkingConfig"] = alt
                        payload["generationConfig"] = gen_cfg
                        resp = await client.post(url, headers=headers, json=payload)
                        if resp.status_code != 400:
                            break
            if resp.status_code >= 400:
                detail = (resp.text or "").replace("\n", " ")[:700]
                raise httpx.HTTPStatusError(
                    f"Client error '{resp.status_code}' for url '{resp.request.url}' "
                    f"— {detail}",
                    request=resp.request,
                    response=resp,
                )
            data = resp.json()

        candidates = data.get("candidates") or [{}]
        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        text_parts: list[str] = []
        thought_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for part in parts:
            if "text" in part:
                chunk = part.get("text") or ""
                if part.get("thought"):
                    thought_parts.append(chunk)
                else:
                    text_parts.append(chunk)
            fc = part.get("functionCall")
            if fc:
                args = fc.get("args") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                meta: dict[str, Any] = {}
                if part.get("thoughtSignature"):
                    meta["thought_signature"] = part["thoughtSignature"]
                tool_calls.append(
                    ToolCall(
                        id=fc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                        name=fc.get("name") or "",
                        arguments=args if isinstance(args, dict) else {"value": args},
                        meta=meta,
                    )
                )
        meta = data.get("usageMetadata") or {}
        usage = ChatUsage(
            prompt_tokens=int(meta.get("promptTokenCount") or 0),
            completion_tokens=int(meta.get("candidatesTokenCount") or 0),
            cached_tokens=int(meta.get("cachedContentTokenCount") or 0),
        )
        return ChatResponse(
            message=ChatMessage(
                role="assistant",
                content="".join(text_parts),
                tool_calls=tool_calls,
                reasoning="".join(thought_parts).strip(),
            ),
            usage=usage,
            model=self.model,
            raw=data,
            stop_reason=(candidates[0].get("finishReason") or "stop"),
        )

    def _thinking_fallbacks(
        self,
        *,
        tools: list[ToolSpec] | None,
        max_tokens: int,
        effort: str | None = None,
    ) -> list[dict[str, Any] | None]:
        """Alternate thinking configs to try after a 400."""
        if self._is_gemini3():
            return [
                {"thinkingLevel": "medium"},
                {"thinkingLevel": "low"},
                {"thinkingLevel": "minimal"},
                None,
            ]
        return [
            {"thinkingBudget": 0} if not tools else {"thinkingBudget": 256},
            None,
        ]

    def _build_generate_payload(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> dict[str, Any]:
        system, contents = self._to_contents(messages)
        gen_cfg: dict[str, Any] = {
            "maxOutputTokens": max_tokens,
        }
        if not self._is_gemini3():
            gen_cfg["temperature"] = temperature
        gen_cfg["thinkingConfig"] = self._thinking_config(
            tools=tools, max_tokens=max_tokens, effort=effort
        )
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": gen_cfg,
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [
                {"functionDeclarations": [t.gemini_schema() for t in tools]}
            ]
        return payload

    async def stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> AsyncIterator[StreamDelta]:
        """Yield text / functionCall deltas via ``streamGenerateContent`` (SSE)."""
        payload = self._build_generate_payload(
            messages,
            tools,
            temperature=temperature,
            max_tokens=max_tokens,
            effort=effort,
        )
        url = (
            f"{self.base_url}/models/{self.model}:streamGenerateContent"
            f"?alt=sse"
        )
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST", url, headers=headers, json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[len("data:") :].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    candidates = chunk.get("candidates") or [{}]
                    cand0 = candidates[0] if candidates else {}
                    finish = cand0.get("finishReason")
                    parts = ((cand0.get("content") or {}).get("parts")) or []
                    meta = chunk.get("usageMetadata") or {}
                    usage = None
                    if meta:
                        usage = ChatUsage(
                            prompt_tokens=int(meta.get("promptTokenCount") or 0),
                            completion_tokens=int(
                                meta.get("candidatesTokenCount") or 0
                            ),
                            cached_tokens=int(
                                meta.get("cachedContentTokenCount") or 0
                            ),
                        )
                    for part in parts:
                        if "text" in part and not part.get("thought"):
                            text = part.get("text") or ""
                            if text:
                                yield StreamDelta(
                                    text=text,
                                    finish_reason=str(finish) if finish else None,
                                    usage=usage,
                                    model=self.model,
                                )
                        fc = part.get("functionCall")
                        if fc:
                            args = fc.get("args") or {}
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except json.JSONDecodeError:
                                    args = {"_raw": args}
                            meta_tc: dict[str, Any] = {}
                            if part.get("thoughtSignature"):
                                meta_tc["thought_signature"] = part[
                                    "thoughtSignature"
                                ]
                            yield StreamDelta(
                                text="",
                                tool_call=ToolCall(
                                    id=fc.get("id")
                                    or f"call_{uuid.uuid4().hex[:8]}",
                                    name=fc.get("name") or "",
                                    arguments=(
                                        args
                                        if isinstance(args, dict)
                                        else {"value": args}
                                    ),
                                    meta=meta_tc,
                                ),
                                finish_reason=str(finish) if finish else None,
                                usage=usage,
                                model=self.model,
                            )
                    if finish and not parts:
                        yield StreamDelta(
                            text="",
                            finish_reason=str(finish),
                            usage=usage,
                            model=self.model,
                        )

    async def smoke(self) -> str:
        r = await self.chat(
            [ChatMessage(role="user", content="Reply with exactly: ok")],
            max_tokens=128,
        )
        return (r.message.content or "").strip()
