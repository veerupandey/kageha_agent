"""Agent tools for the canonical provenance-aware memory service."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from kageha.harness.approvals import ApprovalDecision, ApprovalRequest
from kageha.harness.tools.base import ToolRegistry, tool
from kageha.memory.models import MemoryMutation, MemoryQuery
from kageha.memory.service import get_memory_service

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext


_EXPLICIT_REMEMBER = re.compile(
    r"(?i)\b(?:remember|save (?:this|that)|my preference is|from now on|always|never)\b"
)


def register_memory_tools(ctx: "HarnessContext") -> ToolRegistry:
    reg = ToolRegistry()
    service = get_memory_service(start_worker=True)
    gate = ctx.approvals
    project_root = str(Path.cwd())
    session_id = ctx.workspace.run_id
    user_id = str(ctx.meta.get("memory_user_id") or "local")
    agent_id = str(ctx.meta.get("memory_agent_id") or "main")
    channel_key = str(ctx.meta.get("memory_channel_key") or "")

    def _query(query: str, limit: int = 6) -> MemoryQuery:
        return MemoryQuery(
            query=query,
            project_root=project_root,
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            channel_key=channel_key,
            max_results=max(1, min(20, int(limit or 6))),
            trace_root=str(ctx.workspace.root),
        )

    @tool(
        description=(
            "Search durable memory when the injected digest is missing what you need. "
            "Prefer the digest first; do not call this on every turn."
        )
    )
    async def memory_recall(query: str, limit: int = 6) -> str:
        if not (query or "").strip():
            return "ERROR: query required"
        context = service.recall(_query(query, limit))
        return context.render() or "(no relevant confirmed memory)"

    @tool(
        description=(
            "Load one memory/episode by id from the digest "
            "(e.g. memory:abc… or episode:…). Use when a digest line is truncated."
        )
    )
    async def memory_fetch(target: str) -> str:
        try:
            return json.dumps(service.fetch(target), indent=2)[:12000]
        except ValueError as exc:
            return f"ERROR: {exc}"

    @tool(
        description=(
            "List memory rows with provenance. "
            "state=candidate|confirmed|superseded|retracted|quarantined; "
            "scope=global|project|session|agent."
        )
    )
    async def memory_inspect(
        state: str = "",
        scope: str = "",
        limit: int = 50,
    ) -> str:
        rows = service.inspect(
            state=(state or "").strip(),
            scope_type=(scope or "").strip(),
            project_root=project_root if scope == "project" else "",
            session_id=session_id if scope == "session" else "",
            user_id=user_id,
            agent_id=agent_id,
            channel_key=channel_key,
            limit=max(1, min(200, int(limit or 50))),
        )
        return json.dumps([row.to_dict() for row in rows], indent=2)[:12000]

    @tool(
        description=(
            "Store a durable claim only when the user explicitly asked to remember it. "
            "Assistant guesses stay candidates and are not auto-injected."
        ),
        risk_class="memory_mutation",
    )
    async def memory_remember(
        content: str,
        kind: str = "",
        scope: str = "",
    ) -> str:
        current_user = str(ctx.meta.get("current_user_text") or "")
        source_role = "user" if _EXPLICIT_REMEMBER.search(current_user) else "assistant"
        record = service.mutate(
            MemoryMutation(
                action="remember",
                content=content,
                kind=kind,
                scope_type=scope,
                project_root=project_root,
                session_id=session_id,
                user_id=user_id,
                agent_id=agent_id,
                channel_key=channel_key,
                source_role=source_role,
                verification_evidence=(
                    "explicit user request"
                    if source_role == "user"
                    else "assistant-authored candidate"
                ),
            )
        )
        return json.dumps(record.to_dict(), indent=2)

    @tool(
        description=(
            "Replace one memory by exact id. Only on an explicit user correction."
        ),
        risk_class="memory_mutation",
    )
    async def memory_correct(memory_id: str, replacement: str) -> str:
        ok = await gate.require(
            ApprovalRequest(
                action="memory_correct",
                detail=f"Supersede memory {memory_id}",
                risk_class="memory_mutation",
                default=ApprovalDecision.ASK,
            )
        )
        if not ok:
            return gate.denial_message("memory correction")
        record = service.mutate(
            MemoryMutation(
                action="correct",
                target=memory_id,
                content=replacement,
                project_root=project_root,
                session_id=session_id,
                user_id=user_id,
                agent_id=agent_id,
                channel_key=channel_key,
                source_role="user",
                verification_evidence="explicit user correction",
            )
        )
        return json.dumps(record.to_dict(), indent=2)

    @tool(
        description=(
            "Retract one memory by exact id or unique text. "
            "Only on an explicit user forget request."
        ),
        risk_class="memory_mutation",
    )
    async def memory_forget(target: str) -> str:
        ok = await gate.require(
            ApprovalRequest(
                action="memory_forget",
                detail=f"Retract memory matching {target!r}",
                risk_class="memory_mutation",
                default=ApprovalDecision.ASK,
            )
        )
        if not ok:
            return gate.denial_message("memory retraction")
        record = service.mutate(
            MemoryMutation(
                action="forget",
                target=target,
                project_root=project_root,
                session_id=session_id,
                user_id=user_id,
                agent_id=agent_id,
                channel_key=channel_key,
                source_role="user",
            )
        )
        return json.dumps(record.to_dict(), indent=2)

    @tool(
        description=(
            "Audit the latest recall ranking (or a trace id). "
            "Use when asked why something was remembered/ignored."
        )
    )
    async def memory_explain(trace_id: str = "") -> str:
        try:
            trace = (
                service.explain(trace_id)
                if trace_id
                else service.latest_trace(session_id=session_id)
            )
        except ValueError:
            return f"(no recall trace {trace_id})"
        return (
            json.dumps(trace.to_dict(), indent=2)
            if trace
            else "(no recall trace for this session)"
        )

    @tool(
        description=(
            "Show recently pruned/forgotten memories and why. "
            "Use when the user asks what was dropped."
        )
    )
    async def memory_forgotten(limit: int = 20) -> str:
        rows = service.forgotten(limit=max(1, min(100, int(limit or 20))))
        return json.dumps(rows, indent=2) if rows else "(nothing forgotten recently)"

    for memory_tool in (
        memory_recall,
        memory_fetch,
        memory_inspect,
        memory_remember,
        memory_correct,
        memory_forget,
        memory_explain,
        memory_forgotten,
    ):
        reg.register(memory_tool)
    return reg
