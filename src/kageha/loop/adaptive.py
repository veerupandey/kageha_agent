"""Adaptive control — choose continue / repair / replan / ask from TaskState."""

from __future__ import annotations

from kageha.loop.task_state import (
    ControlDecision,
    FailureKind,
    StageStatus,
    TaskState,
)


def decide_control(state: TaskState) -> tuple[ControlDecision, str]:
    """Pick the next executive action from structured state."""
    val = state.validation

    if state.validated_ok() and state.goals_all_passed():
        return ControlDecision.STOP_SUCCESS, "All goals validated with evidence"

    if val.status == "repair" and val.defects:
        critical = [d for d in val.defects if d.severity == "critical"]
        if critical:
            return (
                ControlDecision.REPAIR,
                f"{len(critical)} critical defect(s): {critical[0].repair}",
            )
        return ControlDecision.REPAIR, val.next_action or "Repair open defects"

    if val.status == "fail":
        return ControlDecision.REPLAN_STAGE, val.notes or "Validation failed — replan stage"

    # Anti-loop: same failure repeated
    if state.failures:
        last = state.failures[-1]
        if state.anti_loop_hit(last.action, last.cause):
            if last.kind in {
                FailureKind.PROVIDER_ERROR.value,
                FailureKind.TIMEOUT.value,
            }:
                return ControlDecision.SWITCH_TOOL, f"Repeated {last.kind} — switch tool/model"
            if last.kind == FailureKind.REASONING.value:
                return ControlDecision.REPLAN_TASK, "Repeated reasoning failure — replan task"
            # Stuck tool/access/output loops → huddle (diagnose + invent under HITL).
            if last.kind in {
                FailureKind.TOOL_ERROR.value,
                FailureKind.ACCESS_BLOCKED.value,
                FailureKind.BAD_OUTPUT.value,
                FailureKind.MISSING_DEP.value,
                FailureKind.UNKNOWN.value,
            }:
                return (
                    ControlDecision.HUDDLE,
                    f"Huddle: {last.action}/{last.cause} failed twice — "
                    f"{last.required_change}",
                )
            return (
                ControlDecision.REPLAN_STAGE,
                f"Anti-loop: {last.action}/{last.cause} failed twice — {last.required_change}",
            )
        if last.kind == FailureKind.INVALID_ARGS.value:
            return ControlDecision.RETRY, last.required_change or "Retry with fixed arguments"

    cur = state.current_stage()
    if cur and cur.status == StageStatus.BLOCKED.value:
        if "need user" in (cur.blocked_reason or "").lower():
            return ControlDecision.ASK_USER, cur.blocked_reason
        return ControlDecision.REPLAN_STAGE, cur.blocked_reason or "Stage blocked"

    # Stage looks done if goals for it passed and no defects
    if cur and val.status == "pass" and not val.defects:
        return ControlDecision.ADVANCE, f"Advance past {cur.id}"

    return ControlDecision.CONTINUE, "Continue current stage"


def repair_steering_message(state: TaskState) -> str:
    snap = state.validation
    lines = [
        "[verifier / repair]",
        f"status={snap.status} next_action={snap.next_action or 'repair_artifact'}",
        "Do NOT claim done. Fix ONLY the listed defects, then stop for re-verify.",
        "",
    ]
    for i, d in enumerate(snap.defects[:8], 1):
        lines.append(
            f"{i}. [{d.severity}] {d.artifact}\n"
            f"   problem: {d.problem}\n"
            f"   evidence: {d.evidence}\n"
            f"   repair: {d.repair}"
        )
    if state.failures:
        last = state.failures[-1]
        lines.append("")
        lines.append(
            f"Anti-loop: last failure {last.action}/{last.kind} — "
            f"required change: {last.required_change}"
        )
    return "\n".join(lines)


def replan_steering_message(state: TaskState, *, whole_task: bool = False) -> str:
    scope = "entire task" if whole_task else "current stage"
    cur = state.current_stage()
    lines = [
        f"[adaptive replan — {scope}]",
        f"Objective: {state.objective}",
        f"Reason: {state.control_reason}",
    ]
    if cur:
        lines.append(f"Stage: {cur.id} — {cur.description}")
    if state.validation.defects:
        lines.append("Outstanding defects:")
        for d in state.validation.defects[:5]:
            lines.append(f"- {d.artifact}: {d.problem}")
    if state.failures:
        lines.append("Do not repeat prior failed actions without the required change:")
        for f in state.failures[-4:]:
            lines.append(f"- {f.action}: {f.required_change}")
    lines.append(
        "Revise the approach now: update todo.md, pick different tools/params, "
        "and execute the next bounded action."
    )
    return "\n".join(lines)


def switch_tool_steering_message(state: TaskState, *, detail: str = "") -> str:
    from kageha.loop.tool_guardrails import suggest_alternatives_for_tool

    last = state.failures[-1] if state.failures else None
    lines = [
        "[adaptive steer — SWITCH_TOOL]",
        f"Reason: {detail or state.control_reason or 'repeated tool failure'}",
        "Do NOT retry the same failing tool with the same arguments.",
        "Pick a different tool, different query, or a smaller diagnostic step.",
    ]
    if last:
        lines.append(
            f"Last failure: {last.action}/{last.kind} — change required: "
            f"{last.required_change or 'switch approach'}"
        )
        # Inject capability-aware alternatives when available.
        tool_name = last.action.split("(")[0].split("/")[-1].strip()
        goal_hint = state.objective[:200] if state.objective else ""
        alt = suggest_alternatives_for_tool(tool_name, goal_hint=goal_hint)
        if alt:
            lines.append("")
            lines.append(alt)
    return "\n".join(lines)


def huddle_steering_message(state: TaskState, *, detail: str = "") -> str:
    """Steer a distinct huddle turn: diagnose → invent → HITL before apply."""
    last = state.failures[-1] if state.failures else None
    lines = [
        "[huddle — unblock]",
        f"Reason: {detail or state.control_reason or 'progress stalled'}",
        "You are in a huddle turn. Do NOT repeat the same failing tool calls.",
        "1) Diagnose the blocker in 1–3 bullets (write huddle.md if helpful).",
        "2) Propose an unblock: new skill (skill_manage create), forge_tool, "
        "MCP reload, or a different approach.",
        "3) Human confirmation is required before skill writes, forge of risky "
        "code, or elevated shell — call those tools and wait for approval.",
        "4) After approval, apply the fix and resume the task DAG / todos.",
    ]
    if last:
        lines.append(
            f"Last failure: {last.action}/{last.kind} — "
            f"{last.required_change or last.cause}"
        )
    if state.validation.defects:
        lines.append("Open defects:")
        for d in state.validation.defects[:5]:
            lines.append(f"- {d.artifact}: {d.problem}")
    return "\n".join(lines)


def retry_steering_message(state: TaskState, *, detail: str = "") -> str:
    last = state.failures[-1] if state.failures else None
    lines = [
        "[adaptive steer — RETRY]",
        f"Reason: {detail or state.control_reason or 'fix arguments and retry'}",
        "Retry once with corrected arguments — do not invent a new plan yet.",
    ]
    if last:
        lines.append(
            f"Fix: {last.required_change or 'correct invalid arguments'}; "
            f"action was {last.action}"
        )
    return "\n".join(lines)


def apply_decision(state: TaskState, decision: ControlDecision, reason: str) -> TaskState:
    state.control = decision.value
    state.control_reason = reason
    if decision == ControlDecision.ADVANCE:
        state.advance_stage()
    elif decision == ControlDecision.REPLAN_STAGE:
        cur = state.current_stage()
        if cur:
            cur.attempts += 1
            cur.last_error = reason
            cur.status = StageStatus.ACTIVE.value
    elif decision == ControlDecision.REPLAN_TASK:
        for s in state.stages:
            if s.status != StageStatus.DONE.value:
                s.status = StageStatus.PENDING.value
                s.last_error = reason
        # reactivate first incomplete
        for s in state.stages:
            if s.status != StageStatus.DONE.value:
                s.status = StageStatus.ACTIVE.value
                state.current_stage_id = s.id
                break
    elif decision in {
        ControlDecision.SWITCH_TOOL,
        ControlDecision.RETRY,
        ControlDecision.HUDDLE,
    }:
        cur = state.current_stage()
        if cur:
            cur.last_error = reason
            if decision == ControlDecision.HUDDLE:
                cur.status = StageStatus.BLOCKED.value
                cur.blocked_reason = reason
    return state


def mark_plan_replacement_needed(state: TaskState, reason: str) -> TaskState:
    """Extension point: chat turn manager / adaptive replan may request a full plan swap.

    Full plan versioning (archive plan.json → plan.vN.json, rewrite stages) is not
    implemented here yet — this only flags TaskState for a future replace_plan hook.
    """
    state.control = ControlDecision.REPLAN_TASK.value
    state.control_reason = f"plan_replacement_needed: {reason}"[:500]
    state.version = int(state.version or 1) + 1
    return state
