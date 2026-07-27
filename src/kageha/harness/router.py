"""Tool dispatch with parallel execution and risk_class policy."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from kageha.harness.tools.base import ToolRegistry
from kageha.models.base import ChatMessage, ToolCall
from kageha.obs.events import EventLog

if TYPE_CHECKING:
    from kageha.harness.approvals import ApprovalGate

# Router asks before invoke (tools that do not self-gate).
ROUTER_REQUIRE_CLASSES = frozenset({
    "browser",
    "network",
    "messaging",
    "forged",
    "mcp",
})

# Always serialize these so HITL prompts cannot interleave (self-gated or router-gated).
SERIALIZE_CLASSES = frozenset({
    "browser",
    "network",
    "messaging",
    "forged",
    "computer_input",
    "hitl",
    "forge",
    "forge_network_or_risky",
    "skill",
    "skill_write",
    "memory_mutation",
    "loop",
    "shell_network_or_destructive",
    "mcp",
})


def _effective_output_limit(tool_name: str, output_limit: int | None) -> int:
    """Resolve per-call envelope; ``computer_*`` keep a higher cap by default."""
    from kageha.config import computer_tool_output_limit, tool_output_limit

    base = tool_output_limit() if output_limit is None else int(output_limit)
    name = tool_name or ""
    if name.startswith("computer_"):
        return max(base, computer_tool_output_limit())
    return max(1, base)


def truncate_tool_output(content: str, limit: int) -> str:
    """Head+tail truncation with a restorable marker; result length ≤ ``limit``."""
    if limit < 1 or len(content) <= limit:
        return content
    omitted = len(content) - limit
    marker = f"\n...[truncated ~{omitted} chars; head+tail]...\n"
    if len(marker) + 32 <= limit:
        budget = limit - len(marker)
        head = (budget * 2) // 3
        tail = budget - head
        return content[:head] + marker + content[-tail:]
    # Tiny envelope: head-only, still ≤ limit.
    suffix = f"\n...[truncated ~{omitted} chars]"
    keep = max(0, limit - len(suffix))
    return content[:keep] + suffix


async def execute_tool_calls(
    registry: ToolRegistry,
    calls: list[ToolCall],
    *,
    max_parallel: int = 4,
    events: EventLog | None = None,
    approvals: "ApprovalGate | None" = None,
    journal: Any = None,
    security_policy: Any = None,
    cancel_event: asyncio.Event | None = None,
    deadline_s: float = 120.0,
    output_limit: int | None = None,
    hooks: Any = None,
    design_readonly: bool = False,
) -> list[ChatMessage]:
    """Execute tool calls.

    Safe tools run concurrently up to ``max_parallel``.
    Tools with serialize/router risk classes run one-at-a-time (HITL-safe).
    When ``design_readonly`` is True (Plan/Spec before Build), mutating tools
    are denied in the router — not just by prompt.
    """
    sem = asyncio.Semaphore(max_parallel)

    def _hook_block(event: str, tool_name: str, args: dict[str, Any]) -> str | None:
        if hooks is None:
            return None
        try:
            result = hooks.run(
                event,
                tool_name=tool_name,
                payload={"tool_name": tool_name, "arguments": args},
            )
        except Exception as exc:  # noqa: BLE001
            return f"DENIED: hook {event} error: {exc}"
        if not result.allowed:
            return f"DENIED: {result.message or f'blocked by {event} hook'}"
        return None

    def _hook_after(event: str, tool_name: str, args: dict[str, Any], content: str) -> str:
        if hooks is None:
            return content
        try:
            result = hooks.run(
                event,
                tool_name=tool_name,
                payload={
                    "tool_name": tool_name,
                    "arguments": args,
                    "result_preview": (content or "")[:2000],
                },
            )
        except Exception as exc:  # noqa: BLE001
            return content + f"\n\n[hook {event} error: {exc}]"
        if result.extra_context:
            return content + f"\n\n[hook {event}]\n{result.extra_context}"
        return content

    async def invoke(call: ToolCall) -> ChatMessage:
        from kageha.harness.tool_policy import tool_denied
        from kageha.loop.mode_policy import tool_blocked_in_plan_design

        if design_readonly and tool_blocked_in_plan_design(
            call.name, approved=False
        ):
            content = (
                f"DENIED: '{call.name}' blocked — Plan/Spec design is "
                "read-only until Build/Approve."
            )
            if events:
                events.emit(
                    "plan_design_blocked",
                    {"tool": call.name, "phase": "router"},
                )
                events.emit(
                    "tool_result",
                    {"name": call.name, "id": call.id, "preview": content[:300]},
                )
            return ChatMessage(
                role="tool",
                content=content,
                tool_call_id=call.id,
                name=call.name,
            )
        if tool_denied(call.name):
            content = (
                f"DENIED: tool '{call.name}' blocked by tools.policy "
                "(allow/deny in tools.yaml)"
            )
            if events:
                events.emit(
                    "tool_result",
                    {"name": call.name, "id": call.id, "preview": content[:300]},
                )
            return ChatMessage(
                role="tool",
                content=content,
                tool_call_id=call.id,
                name=call.name,
            )
        tool = registry.get(call.name)
        if tool is None:
            content = f"ERROR: unknown tool '{call.name}'. Available: {registry.names()}"
        else:
            args = normalize_tool_arguments(call.name, call.arguments)
            # Truncated / empty tool JSON (common when completion hits max_tokens)
            if _args_look_broken(call.name, args):
                content = (
                    f"ERROR: {call.name} arguments missing or truncated "
                    f"(got keys={list(args.keys())}). "
                    "Likely hit max_tokens mid tool-call. Retry with a SMALLER payload: "
                    "write a short outline first, then append sections; "
                    "or use bash with a heredoc for large files."
                )
                if events:
                    events.emit(
                        "tool_result",
                        {"name": call.name, "id": call.id, "preview": content[:300]},
                    )
                return ChatMessage(
                    role="tool",
                    content=content,
                    tool_call_id=call.id,
                    name=call.name,
                )
            pre = _hook_block("preToolUse", call.name, args)
            if pre is None and call.name in {"bash", "shell", "run_terminal"}:
                pre = _hook_block("beforeShell", call.name, args)
            if pre is not None:
                content = pre
            else:
                import time as _time

                risk = tool.risk_class or "safe"
                security_decision = (
                    security_policy.assess(risk_class=risk)
                    if security_policy is not None
                    else None
                )
                tool_ms = 0.0
                if security_decision is not None and not security_decision.allowed:
                    content = f"DENIED: {security_decision.reason}"
                    if events:
                        events.emit(
                            "security_denial",
                            {
                                "name": tool.name,
                                "risk_class": risk,
                                "profile": security_decision.profile.value,
                                "reason": security_decision.reason,
                            },
                        )
                elif risk in ROUTER_REQUIRE_CLASSES and approvals is not None:
                    from kageha.harness.approvals import ApprovalDecision, ApprovalRequest

                    preview = json.dumps(args, default=str)[:800]
                    ok = await approvals.require(
                        ApprovalRequest(
                            action=f"tool:{tool.name}",
                            detail=f"risk_class={risk} args={preview}",
                            risk_class=risk,
                            default=ApprovalDecision.ASK,
                        )
                    )
                    if not ok:
                        content = f"DENIED: {tool.name} ({risk}) not approved"
                    else:
                        _t0 = _time.perf_counter()
                        content = await _call_tool_journaled(
                            tool,
                            args,
                            call_id=call.id,
                            journal=journal,
                            policy_grant=(
                                security_decision.grant
                                if security_decision is not None
                                else ""
                            ),
                            cancel_event=cancel_event,
                            deadline_s=deadline_s,
                            output_limit=output_limit,
                        )
                        tool_ms = (_time.perf_counter() - _t0) * 1000.0
                else:
                    _t0 = _time.perf_counter()
                    content = await _call_tool_journaled(
                        tool,
                        args,
                        call_id=call.id,
                        journal=journal,
                        policy_grant=(
                            security_decision.grant
                            if security_decision is not None
                            else ""
                        ),
                        cancel_event=cancel_event,
                        deadline_s=deadline_s,
                        output_limit=output_limit,
                    )
                    tool_ms = (_time.perf_counter() - _t0) * 1000.0
                if not str(content).startswith("DENIED:"):
                    content = _hook_after("postToolUse", call.name, args, content)
                    if call.name in {
                        "write_file",
                        "edit_file",
                        "apply_patch",
                        "str_replace",
                    }:
                        content = _hook_after(
                            "afterFileEdit", call.name, args, content
                        )
        if events:
            payload = {
                "name": call.name,
                "id": call.id,
                "preview": content[:300],
            }
            if "tool_ms" in locals() and tool_ms:
                payload["tool_ms"] = round(tool_ms, 1)
                if str(call.name).startswith("computer_"):
                    try:
                        from kageha.harness.tools import computer_driver as _cdriver

                        snap = _cdriver.timing_snapshot()
                        payload["driver_ms"] = round(
                            float(snap.get("driver_ms_total") or 0.0), 1
                        )
                        payload["driver_transport"] = snap.get("last_transport") or ""
                    except Exception:  # noqa: BLE001
                        pass
            events.emit("tool_result", payload)
        return ChatMessage(
            role="tool",
            content=content,
            tool_call_id=call.id,
            name=call.name,
        )

    async def one_free(call: ToolCall) -> ChatMessage:
        async with sem:
            return await invoke(call)

    def _serialize(call: ToolCall) -> bool:
        tool = registry.get(call.name)
        if tool is None:
            return False
        return (tool.risk_class or "safe") in SERIALIZE_CLASSES | ROUTER_REQUIRE_CLASSES

    gated = [c for c in calls if _serialize(c)]
    free = [c for c in calls if not _serialize(c)]

    out: dict[str, ChatMessage] = {}
    if free:
        for msg in await asyncio.gather(*[one_free(c) for c in free]):
            out[msg.tool_call_id or ""] = msg
    for call in gated:
        msg = await invoke(call)
        out[msg.tool_call_id or ""] = msg

    # Preserve original call order
    return [out[c.id] for c in calls]


async def _call_tool(
    tool: Any,
    args: dict[str, Any],
    *,
    cancel_event: asyncio.Event | None = None,
    deadline_s: float = 120.0,
    output_limit: int | None = None,
) -> str:
    limit = _effective_output_limit(getattr(tool, "name", "") or "", output_limit)
    task = asyncio.create_task(tool.call(**_filter_kwargs(tool.handler, args)))
    cancel_task: asyncio.Task[bool] | None = None
    try:
        if cancel_event is not None:
            cancel_task = asyncio.create_task(cancel_event.wait())
            done, _ = await asyncio.wait(
                {task, cancel_task},
                timeout=max(0.001, deadline_s),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task in done and cancel_event.is_set():
                task.cancel()
                return "ERROR: tool execution cancelled"
            if task not in done:
                task.cancel()
                return f"ERROR: tool deadline exceeded ({deadline_s:g}s)"
            content = await task
        else:
            content = await asyncio.wait_for(
                task,
                timeout=max(0.001, deadline_s),
            )
        return truncate_tool_output(str(content), limit)
    except TimeoutError:
        task.cancel()
        return f"ERROR: tool deadline exceeded ({deadline_s:g}s)"
    except TypeError as e:
        return f"ERROR: bad arguments for {tool.name}: {e}"
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {tool.name} failed: {e}"
    finally:
        if cancel_task is not None:
            cancel_task.cancel()


async def _call_tool_journaled(
    tool: Any,
    args: dict[str, Any],
    *,
    call_id: str,
    journal: Any = None,
    policy_grant: str = "",
    cancel_event: asyncio.Event | None = None,
    deadline_s: float = 120.0,
    output_limit: int | None = None,
) -> str:
    if journal is None:
        return await _call_tool(
            tool,
            args,
            cancel_event=cancel_event,
            deadline_s=deadline_s,
            output_limit=output_limit,
        )
    try:
        attempt_id, replay = journal.before(
            call_id=call_id,
            tool_name=tool.name,
            arguments=args,
            risk_class=tool.risk_class or "safe",
            policy_grant=policy_grant,
        )
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: runtime journal failed before {tool.name}: {exc}"
    if replay is not None:
        return replay
    content = await _call_tool(
        tool,
        args,
        cancel_event=cancel_event,
        deadline_s=deadline_s,
        output_limit=output_limit,
    )
    try:
        journal.after(attempt_id, content)
    except Exception as exc:  # noqa: BLE001
        return (
            f"{content}\n\nERROR: runtime journal failed after {tool.name}: {exc}"
        )
    return content


def _filter_kwargs(handler: Any, args: dict[str, Any]) -> dict[str, Any]:
    import inspect

    sig = inspect.signature(handler)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return args
    allowed = set(sig.parameters)
    return {k: v for k, v in args.items() if k in allowed}


_ARG_ALIASES: dict[str, tuple[str, ...]] = {
    "path": ("file", "filename", "filepath", "target", "dest", "destination"),
    "content": ("text", "body", "data", "contents", "markdown", "value", "code"),
    "command": ("cmd", "shell", "bash", "script"),
    "query": ("q", "search", "prompt"),
    "question": ("prompt", "q", "ask"),
    "markdown": ("content", "text", "body"),
}

_REQUIRED_HINTS: dict[str, tuple[str, ...]] = {
    "write_file": ("path", "content"),
    "read_file": ("path",),
    "edit_file": ("path", "old_string", "new_string"),
    "bash": ("command",),
    "web_search": ("query",),
    "todo_write": ("markdown",),
}


def repair_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        # Unwrap common nesting mistakes
        for nest in ("arguments", "parameters", "input", "args"):
            inner = raw.get(nest)
            if isinstance(inner, dict) and len(inner) >= len(
                {k: v for k, v in raw.items() if k != nest}
            ):
                return repair_arguments(inner)
        return raw
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
            if isinstance(val, dict):
                return val
        except json.JSONDecodeError:
            return {"_raw": raw}
    return {"value": raw}


def normalize_tool_arguments(tool_name: str, raw: Any) -> dict[str, Any]:
    """Repair JSON + map common alias keys (file→path, cmd→command, …)."""
    args = dict(repair_arguments(raw))
    for canonical, alts in _ARG_ALIASES.items():
        cur = args.get(canonical)
        if cur not in (None, ""):
            continue
        for alt in alts:
            if args.get(alt) not in (None, ""):
                args[canonical] = args[alt]
                break
    # bash sometimes gets the whole command as sole unnamed string
    if tool_name == "bash" and not args.get("command"):
        if isinstance(args.get("_raw"), str) and args["_raw"].strip():
            args["command"] = args["_raw"]
        elif isinstance(args.get("value"), str) and args["value"].strip():
            args["command"] = args["value"]
    return args


def _args_look_broken(tool_name: str, args: dict[str, Any]) -> bool:
    needed = _REQUIRED_HINTS.get(tool_name)
    if not needed:
        return False
    if args.get("_raw") and not all(args.get(k) not in (None, "") for k in needed):
        return True
    return any(args.get(k) in (None, "") for k in needed)
