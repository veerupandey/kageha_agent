"""Canonical @tool decorator and registry."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, get_args, get_origin, get_type_hints

from kageha.models.base import ToolSpec

ToolHandler = Callable[..., Awaitable[str] | str]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    risk_class: str = "safe"

    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, parameters=self.parameters)

    async def call(self, **kwargs: Any) -> str:
        result = self.handler(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return str(result)


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self.tools.pop(name, None)

    def apply_tool_policy(self) -> list[str]:
        """Remove tools denied by tools.yaml; return removed names."""
        from kageha.harness.tool_policy import tool_denied

        removed: list[str] = []
        for name in list(self.tools.keys()):
            if tool_denied(name):
                self.unregister(name)
                removed.append(name)
        return removed

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self.tools.values()]

    def names(self) -> list[str]:
        return list(self.tools.keys())


def _json_type(anno: Any) -> dict[str, Any]:
    origin = get_origin(anno)
    if anno is str or anno == "str":
        return {"type": "string"}
    if anno is int:
        return {"type": "integer"}
    if anno is float:
        return {"type": "number"}
    if anno is bool:
        return {"type": "boolean"}
    if origin is list:
        args = get_args(anno)
        return {"type": "array", "items": _json_type(args[0]) if args else {"type": "string"}}
    if origin is dict:
        return {"type": "object"}
    # Optional[T]
    if origin is type(None):
        return {"type": "string"}
    args = get_args(anno)
    if args:
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return _json_type(non_none[0])
    return {"type": "string"}


def tool(
    name: str | None = None,
    description: str = "",
    *,
    risk_class: str = "safe",
) -> Callable[[ToolHandler], Tool]:
    def decorator(fn: ToolHandler) -> Tool:
        hints = get_type_hints(fn)
        sig = inspect.signature(fn)
        props: dict[str, Any] = {}
        required: list[str] = []
        for pname, param in sig.parameters.items():
            if pname in {"self", "ctx", "context"}:
                continue
            anno = hints.get(pname, str)
            props[pname] = _json_type(anno)
            # Use docstring first line for param if present — keep schema simple
            if param.default is inspect.Parameter.empty:
                required.append(pname)
        parameters = {
            "type": "object",
            "properties": props,
            "required": required,
        }
        return Tool(
            name=name or fn.__name__,
            description=description or (fn.__doc__ or fn.__name__).strip(),
            parameters=parameters,
            handler=fn,
            risk_class=risk_class,
        )

    return decorator
