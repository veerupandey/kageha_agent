"""Per-turn tool discovery and schema selection.

The registry remains the execution authority.  This module only limits which
schemas are shown to the model on a given step, keeping prompts small while a
``tool_search`` escape hatch can activate additional tools on demand.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from kageha.harness.tools.base import ToolRegistry, tool
from kageha.models.base import ToolSpec

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext


_ALWAYS = (
    "tool_search",
    "read_file",
    "list_dir",
    "bash",
    "write_file",
    "ask_human",
    "skill_run",
)

_ROUTES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("browser", "website", "webpage", "click", "login", "form"), ("browser_",)),
    (("search", "research", "latest", "news", "source"), ("web_", "parallel_web_", "research_")),
    (("pdf", "document", "docx", "resume"), ("pdf_", "document_", "read_file")),
    (("image", "picture", "illustration", "photo"), ("image_", "gemini_generate_image")),
    (("memory", "remember", "recall"), ("memory_",)),
    (("skill", "workflow"), ("skill_",)),
    (("mcp", "connector", "server"), ("mcp_",)),
    (("computer", "desktop", "screen", "mouse", "keyboard"), ("computer_",)),
    (("agent", "delegate", "parallel"), ("spawn_", "subagent_")),
)

_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "in",
        "of",
        "on",
        "the",
        "to",
        "use",
        "with",
    }
)


def _words(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9_]+", (text or "").lower())
        if word not in _STOP_WORDS and len(word) > 1
    }


def search_specs(
    specs: list[ToolSpec],
    query: str,
    *,
    limit: int = 8,
    min_score: int = 1,
) -> list[ToolSpec]:
    """Rank tool metadata by a small deterministic lexical score."""
    query_words = _words(query)
    if not query_words:
        return []
    ranked: list[tuple[int, str, ToolSpec]] = []
    for spec in specs:
        name = spec.name.lower()
        haystack = _words(f"{name.replace('_', ' ')} {spec.description}")
        overlap = len(query_words & haystack)
        substring = sum(2 for word in query_words if word in name)
        score = overlap + substring
        if score >= max(1, min_score):
            ranked.append((-score, name, spec))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in ranked[: max(1, min(limit, 20))]]


def register_tool_search(ctx: "HarnessContext", registry: ToolRegistry) -> None:
    """Add the discovery tool after all native, MCP, and user tools are loaded."""
    if registry.get("tool_search") is not None:
        return

    @tool(
        description=(
            "Search tools that are installed but not currently shown. Use when the "
            "current tools cannot complete the task. Matching tools become available "
            "on the next agent step. Returns names and short descriptions."
        )
    )
    async def tool_search(query: str, limit: int = 8) -> str:
        matches = search_specs(
            [spec for spec in registry.specs() if spec.name != "tool_search"],
            query,
            limit=limit,
        )
        active = set(ctx.meta.get("dynamic_tool_names") or [])
        active.update(spec.name for spec in matches)
        ctx.meta["dynamic_tool_names"] = sorted(active)
        if not matches:
            return f"No installed tools matched {query!r}. Try broader capability words."
        lines = ["Activated for subsequent steps:"]
        lines.extend(f"- {spec.name}: {spec.description[:180]}" for spec in matches)
        return "\n".join(lines)

    registry.register(tool_search)


def select_tool_specs(
    ctx: "HarnessContext",
    specs: list[ToolSpec],
    *,
    task: str = "",
    max_tools: int = 18,
) -> list[ToolSpec]:
    """Return a compact, task-relevant schema set without altering the registry."""
    if len(specs) <= max_tools:
        return specs

    names = {spec.name for spec in specs}
    selected = set(_ALWAYS) & names
    dynamic = set(ctx.meta.get("dynamic_tool_names") or []) & names
    recent = set(ctx.meta.get("recent_tool_names") or []) & names
    skill_allowed = set(ctx.meta.get("skill_allowed_tools") or []) & names
    text = (task or str(ctx.meta.get("current_user_text") or "")).lower()
    wants_computer = any(
        token in text for token in ("computer", "desktop", "screen", "mouse", "keyboard")
    )
    pack_required: set[str] = set()
    if "computer" in set(ctx.meta.get("tool_packs_enabled") or []) and (
        skill_allowed or wants_computer
    ):
        # Observation is the entry point for every desktop workflow. Pack
        # ownership must survive an unrelated skill allowlist and the cap.
        pack_required.update({"computer_get_state", "computer_click", "computer_doctor"})
    pack_required &= names
    selected.update(dynamic)
    selected.update(recent)
    selected.update(pack_required)

    for keywords, prefixes in _ROUTES:
        if any(keyword in text for keyword in keywords):
            selected.update(
                name for name in names if any(name.startswith(prefix) for prefix in prefixes)
            )

    # Skill allowlists are authoritative routing hints.
    selected.update(skill_allowed)

    # Lexical matches catch custom/MCP/user tools that do not follow our prefixes.
    for spec in search_specs(specs, text, limit=6, min_score=2):
        selected.add(spec.name)

    ordered = [spec for spec in specs if spec.name in selected]
    if len(ordered) > max_tools:
        always_priority = {name: i for i, name in enumerate(_ALWAYS)}

        def priority(spec: ToolSpec) -> tuple[int, int, str]:
            if spec.name in always_priority:
                return (0, always_priority[spec.name], spec.name)
            if spec.name in pack_required:
                return (1, 0, spec.name)
            if spec.name in skill_allowed:
                return (2, 0, spec.name)
            if spec.name in dynamic:
                return (3, 0, spec.name)
            if spec.name in recent:
                return (4, 0, spec.name)
            return (5, 0, spec.name)

        ordered.sort(key=priority)
        ordered = ordered[:max_tools]
    return ordered or specs[:max_tools]
