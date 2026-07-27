"""Agent-facing KB tools."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from kageha.harness.tools.base import ToolRegistry, tool
from kageha.knowledge.facade import KnowledgeFacade

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext


def register_kb_tools(ctx: "HarnessContext") -> ToolRegistry:
    reg = ToolRegistry()
    facade = KnowledgeFacade()

    @tool(description="Search an attached or named knowledge base (flat or graph).")
    async def kb_search(query: str, kb_id: str = "", top_k: int = 5) -> str:
        kids = [kb_id] if kb_id else list(ctx.attached_kbs)
        if not kids:
            return "ERROR: no kb_id and no KBs attached"
        results = []
        for kid in kids:
            try:
                hits = facade.search(kid, query, top_k=int(top_k))
                results.append({"kb": kid, "hits": hits})
            except Exception as e:  # noqa: BLE001
                results.append({"kb": kid, "error": str(e)})
        return json.dumps(results, indent=2)[:12000]

    @tool(description="Multi-hop / graph query against a VGRAG knowledge base.")
    async def kb_query(query: str, kb_id: str = "") -> str:
        kid = kb_id or (ctx.attached_kbs[0] if ctx.attached_kbs else "")
        if not kid:
            return "ERROR: kb_id required"
        result = facade.query(kid, query)
        return json.dumps(result, indent=2)[:12000]

    @tool(description="Ingest files/URLs into a knowledge base.", risk_class="network")
    async def kb_ingest(kb_id: str, sources: str) -> str:
        # sources: comma-separated
        srcs = [s.strip() for s in sources.split(",") if s.strip()]
        # HITL for URLs
        if any(s.startswith("http") for s in srcs):
            from kageha.harness.approvals import ApprovalDecision, ApprovalRequest

            ok = await ctx.approvals.require(
                ApprovalRequest(
                    action="kb_ingest_url",
                    detail=sources,
                    risk_class="network",
                    default=ApprovalDecision.ASK,
                )
            )
            if not ok:
                return "DENIED: URL ingest not approved"
        result = facade.ingest(kb_id, srcs)
        return json.dumps(result)

    @tool(description="Create a knowledge base. engine=zvec|vgrag")
    async def kb_create(kb_id: str, engine: str = "zvec") -> str:
        kb = facade.create(kb_id, engine=engine)
        return f"Created KB {kb.kb_id} engine={kb.engine} at {kb.root}"

    for t in (kb_search, kb_query, kb_ingest, kb_create):
        if hasattr(t, "name"):
            reg.register(t)  # type: ignore[arg-type]
    return reg
