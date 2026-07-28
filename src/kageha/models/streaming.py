"""Accumulate StreamingChatModel deltas into a ChatResponse."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from kageha.models.base import (
    ChatMessage,
    ChatResponse,
    ChatUsage,
    StreamDelta,
    ToolCall,
)


def supports_stream(model: Any) -> bool:
    """True when *model* exposes an async ``stream`` method."""
    stream = getattr(model, "stream", None)
    return callable(stream)


async def collect_stream(
    deltas: AsyncIterator[StreamDelta],
    *,
    on_text_delta: Callable[[str], None] | None = None,
    model_id: str = "",
) -> ChatResponse:
    """Fold stream deltas into one ChatResponse; optionally emit text pieces.

    ``on_text_delta`` receives only user-visible ``text`` — never ``reasoning``.
    """
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    role = "assistant"
    stop_reason = "stop"
    usage = ChatUsage()
    model_name = model_id
    # index → partial tool call assembly (OpenAI style)
    pending: dict[int, dict[str, str]] = {}
    complete_tools: list[ToolCall] = []

    async for delta in deltas:
        if delta.role:
            role = delta.role
        if delta.model:
            model_name = delta.model
        if delta.finish_reason:
            stop_reason = delta.finish_reason
        if delta.usage is not None:
            usage = delta.usage
        if delta.reasoning:
            reasoning_parts.append(delta.reasoning)
        if delta.text:
            text_parts.append(delta.text)
            if on_text_delta is not None:
                try:
                    on_text_delta(delta.text)
                except Exception:  # noqa: BLE001
                    pass
        if delta.tool_call is not None:
            complete_tools.append(delta.tool_call)
            continue
        if (
            delta.tool_call_index is not None
            or delta.tool_call_id
            or delta.tool_name
            or delta.arguments_json
        ):
            idx = int(delta.tool_call_index or 0)
            slot = pending.setdefault(
                idx, {"id": "", "name": "", "arguments": ""}
            )
            if delta.tool_call_id:
                slot["id"] = delta.tool_call_id
            if delta.tool_name:
                slot["name"] = delta.tool_name
            if delta.arguments_json:
                slot["arguments"] += delta.arguments_json

    for idx in sorted(pending):
        slot = pending[idx]
        args_raw = slot.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
        except json.JSONDecodeError:
            args = {"_raw": args_raw}
        complete_tools.append(
            ToolCall(
                id=slot.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                name=slot.get("name") or "",
                arguments=args if isinstance(args, dict) else {"value": args},
            )
        )

    return ChatResponse(
        message=ChatMessage(
            role=role or "assistant",
            content="".join(text_parts),
            tool_calls=complete_tools,
            reasoning="".join(reasoning_parts).strip(),
        ),
        usage=usage,
        model=model_name,
        stop_reason=stop_reason or "stop",
    )
