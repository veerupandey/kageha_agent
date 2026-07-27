"""Hybrid tool forge — session-local Python tools (HITL for risky ones)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from kageha.harness.approvals import ApprovalDecision, ApprovalRequest
from kageha.harness.sandbox import run_shell
from kageha.harness.tools.base import Tool, ToolRegistry, tool

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext


def register_forge_tools(ctx: "HarnessContext") -> ToolRegistry:
    reg = ToolRegistry()

    @tool(description="Forge a session-local Python tool from source code.", risk_class="forge")
    async def forge_tool(name: str, description: str, code: str) -> str:
        decision = ctx.approvals.classify_forge(code, description)
        if decision != ApprovalDecision.AUTO:
            ok = await ctx.approvals.require(
                ApprovalRequest(
                    action="forge_tool",
                    detail=f"{name}: {description}\n\n{code[:800]}",
                    risk_class="forge_network_or_risky",
                    default=decision,
                )
            )
            if not ok:
                return ctx.approvals.denial_message("forge_tool")
        tools_dir = ctx.workspace.root / "forged"
        tools_dir.mkdir(exist_ok=True)
        path = tools_dir / f"{name}.py"
        path.write_text(code)
        # Register a wrapper that executes the script with JSON args on stdin-like env
        async def _handler(input_json: str = "{}") -> str:
            runner = tools_dir / f"_run_{name}.py"
            runner.write_text(
                "import json, sys, runpy\n"
                f"ns = runpy.run_path({str(path)!r})\n"
                "fn = ns.get('run') or ns.get('main')\n"
                "args = json.loads(sys.argv[1] if len(sys.argv)>1 else '{}')\n"
                "print(fn(**args) if callable(fn) else 'ERROR: define run(**kwargs)')\n"
            )
            result = await run_shell(
                f"python {runner.name} {json.dumps(input_json)}",
                cwd=tools_dir,
            )
            return result.stdout or result.stderr

        forged = Tool(
            name=f"forged_{name}",
            description=f"[forged] {description}",
            parameters={
                "type": "object",
                "properties": {"input_json": {"type": "string"}},
                "required": [],
            },
            handler=_handler,
            risk_class="forged",
        )
        ctx.tools.register(forged)
        return f"Forged tool forged_{name} at {path}"

    if hasattr(forge_tool, "name"):
        reg.register(forge_tool)  # type: ignore[arg-type]
    return reg
