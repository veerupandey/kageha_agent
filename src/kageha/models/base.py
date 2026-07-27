"""Chat model protocol and shared message types."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
                or {"type": "object", "properties": {}, "additionalProperties": True},
            },
        }

    def anthropic_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters
            or {"type": "object", "properties": {}, "additionalProperties": True},
        }

    def gemini_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": _gemini_params(self.parameters),
        }


def _gemini_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """Gemini rejects JSON Schema fields like additionalProperties."""
    if not params:
        return {"type": "object", "properties": {}}

    def scrub(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: scrub(v)
                for k, v in obj.items()
                if k
                not in {
                    "additionalProperties",
                    "$schema",
                    "unevaluatedProperties",
                }
            }
        if isinstance(obj, list):
            return [scrub(x) for x in obj]
        return obj

    cleaned = scrub(params)
    if "type" not in cleaned:
        cleaned["type"] = "object"
    cleaned.setdefault("properties", {})
    return cleaned


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    # Provider-specific extras (e.g. Gemini thought_signature)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatMessage:
    role: str  # system | user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    # Model "thinking" / chain-of-thought when the provider exposes it separately.
    reasoning: str = ""


@dataclass
class ChatUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class ChatResponse:
    message: ChatMessage
    usage: ChatUsage = field(default_factory=ChatUsage)
    model: str = ""
    raw: Any = None
    stop_reason: str = "stop"


@dataclass
class StreamDelta:
    """Incremental assistant output from a streaming chat completion."""

    text: str
    role: str | None = None


@runtime_checkable
class ChatModel(Protocol):
    model_id: str
    provider: str

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> ChatResponse: ...

    async def smoke(self) -> str: ...


@runtime_checkable
class StreamingChatModel(Protocol):
    """Optional streaming extension; implement ``stream`` without changing ``ChatModel``."""

    model_id: str
    provider: str

    async def stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> AsyncIterator[StreamDelta]: ...
