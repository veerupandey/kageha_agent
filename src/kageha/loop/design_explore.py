"""Explore-then-plan: read-only research before Plan Build.

The model may list/read/search during design; mutating tools are denied until Build.
"""

from __future__ import annotations

from typing import Any

from kageha.loop.mode_policy import tool_blocked_in_plan_design
from kageha.models.base import ChatMessage, ToolSpec


DESIGN_EXPLORE_MAX_STEPS = 4


def filter_design_tool_specs(specs: list[ToolSpec]) -> list[ToolSpec]:
    """Keep only tools allowed during Plan design (read/search)."""
    return [
        s for s in specs if not tool_blocked_in_plan_design(s.name, approved=False)
    ]


def _role_candidates(preferred: str) -> list[str]:
    roles: list[str] = []
    for role in (preferred or "planning", "tool_calling", "default"):
        r = str(role or "").strip() or "default"
        if r not in roles:
            roles.append(r)
    return roles


def _ladder_head(router: Any, role: str) -> str:
    try:
        ladder = router.ladder(role) if hasattr(router, "ladder") else []
        if ladder:
            return str(ladder[0])
    except Exception:  # noqa: BLE001
        pass
    return role


def _emit(
    events: Any,
    kind: str,
    data: dict[str, Any],
) -> None:
    if events:
        events.emit(kind, data)


def _emit_explore_failover(
    events: Any,
    *,
    from_id: str,
    to_id: str,
    error: str = "",
    role_from: str = "",
    role_to: str = "",
    log: Any = None,
) -> None:
    frm = (from_id or role_from or "?").strip() or "?"
    to = (to_id or role_to or "?").strip() or "?"
    if frm == to:
        return
    message = f"Explore: {frm} → {to}"
    payload = {
        "message": message,
        "from": frm,
        "to": to,
        "error": (error or "")[:240],
        "role_from": role_from,
        "role_to": role_to,
    }
    _emit(events, "design_explore_failover", payload)
    if log:
        log(f"[kageha] {message}")


async def _chat_with_role_failover(
    *,
    router: Any,
    history: list[ChatMessage],
    tools: list[ToolSpec],
    preferred_role: str,
    max_tokens: int,
    events: Any = None,
    log: Any = None,
) -> tuple[Any, Any, str]:
    """Try preferred role then fallbacks; surface model/role failover to UI."""
    roles = _role_candidates(preferred_role)
    last_err: Exception | None = None
    failed_roles: list[tuple[str, str]] = []
    tokens = max(256, int(max_tokens))

    # Soft-retry once with smaller token budget if every role fails.
    for attempt in range(2):
        for try_role in roles:
            try:
                model, resp = await router.chat(
                    history,
                    tools=tools,
                    role=try_role,
                    max_tokens=tokens,
                    effort="medium",
                )
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                failed_roles.append((try_role, str(exc)[:240]))
                continue

            to_id = str(getattr(model, "model_id", "") or try_role)
            # Within-role ladder notices (429/5xx → next model on same role).
            notices: list[dict[str, Any]] = []
            drain = getattr(router, "drain_failover_notices", None)
            if callable(drain):
                try:
                    notices = list(drain() or [])
                except Exception:  # noqa: BLE001
                    notices = []
            for notice in notices:
                _emit_explore_failover(
                    events,
                    from_id=str(notice.get("from") or ""),
                    to_id=str(notice.get("to") or to_id),
                    error=str(notice.get("error") or ""),
                    role_from=str(notice.get("role") or try_role),
                    role_to=try_role,
                    log=log,
                )

            # Cross-role failover (planning wiped → tool_calling/default).
            if try_role != roles[0] and failed_roles:
                role_from, err = failed_roles[0]
                from_id = _ladder_head(router, role_from)
                _emit_explore_failover(
                    events,
                    from_id=from_id,
                    to_id=to_id,
                    error=err,
                    role_from=role_from,
                    role_to=try_role,
                    log=log,
                )
            return model, resp, try_role

        # All roles failed this pass — shrink tokens and retry once.
        tokens = min(tokens, 512)
        if log and attempt == 0:
            log("[kageha] design explore soft-retry with smaller max_tokens")

    raise last_err or RuntimeError("design explore chat failed")


async def explore_before_plan(
    *,
    task: str,
    router: Any,
    tool_specs: list[ToolSpec],
    execute_tools: Any,
    events: Any = None,
    log: Any = None,
    max_steps: int = DESIGN_EXPLORE_MAX_STEPS,
    role: str = "planning",
    agent_mode: str = "plan",
) -> str:
    """Run a short read-only tool loop; return notes for the planner.

    ``execute_tools`` must enforce ``design_readonly=True`` (router hard gate).
    On planning-role 429/5xx, falls back through tool_calling → default and
    emits ``design_explore_failover`` so the WebUI is not silent.
    """
    allowed = filter_design_tool_specs(tool_specs)
    if not allowed:
        return ""

    def _log(msg: str) -> None:
        if log:
            log(msg)

    history: list[ChatMessage] = [
        ChatMessage(
            role="system",
            content=(
                f"You are in {agent_mode} DESIGN (explore-then-plan).\n"
                "HARD RULE: read-only research only — list_dir, read_file, "
                "web_search, skill_list, memory_recall, etc. "
                "Do NOT write files, edit, bash, spawn agents, or mutate anything.\n"
                "Gather just enough context to draft a solid plan, then stop with a "
                "short bullet summary of findings (no plan JSON yet)."
            ),
        ),
        ChatMessage(
            role="user",
            content=(
                f"Explore the workspace/project for this objective, then summarize "
                f"what matters for the plan:\n\n{task}"
            ),
        ),
    ]

    notes_parts: list[str] = []
    steps = max(1, min(int(max_steps), 8))
    _log(f"[kageha] design explore — up to {steps} read-only steps")
    _emit(
        events,
        "design_explore_start",
        {"agent_mode": agent_mode, "max_steps": steps, "tools": len(allowed)},
    )

    for step in range(steps):
        _model, resp, _used_role = await _chat_with_role_failover(
            router=router,
            history=history,
            tools=allowed,
            preferred_role=role,
            max_tokens=1024,
            events=events,
            log=_log,
        )
        assistant = resp.message
        history.append(assistant)
        text = (assistant.content or "").strip()
        if text:
            notes_parts.append(text)

        calls = list(assistant.tool_calls or [])
        if not calls:
            break

        # Defense: drop mutating calls before they reach the router.
        safe_calls = [
            c
            for c in calls
            if not tool_blocked_in_plan_design(c.name, approved=False)
        ]
        blocked = [c.name for c in calls if c not in safe_calls]
        if blocked:
            _log(f"[kageha]   design explore blocked: {', '.join(blocked)}")
            for c in calls:
                if tool_blocked_in_plan_design(c.name, approved=False):
                    history.append(
                        ChatMessage(
                            role="tool",
                            name=c.name,
                            tool_call_id=c.id,
                            content=(
                                "DENIED: design phase is read-only until Build. "
                                "Use list_dir/read_file/web_search only."
                            ),
                        )
                    )
        if not safe_calls:
            break

        names = ", ".join(c.name for c in safe_calls)
        _log(f"[kageha]   design explore tools: {names}")
        results = await execute_tools(safe_calls)
        history.extend(results)
        for r in results:
            preview = (r.content or "")[:400]
            if preview:
                notes_parts.append(f"[{r.name}] {preview}")

        _emit(
            events,
            "design_explore_step",
            {"step": step + 1, "tools": [c.name for c in safe_calls]},
        )

    notes = "\n\n".join(p for p in notes_parts if p.strip())[:6000]
    _emit(
        events,
        "design_explore_done",
        {
            "chars": len(notes),
            "agent_mode": agent_mode,
            "message": (
                f"Explore done ({len(notes)} chars)"
                if notes
                else "Explore finished (no notes)"
            ),
        },
    )
    _log(f"[kageha] design explore done ({len(notes)} chars)")
    return notes
