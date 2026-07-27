"""Stage-gate monitor — separate critic that checks plan drift mid-run."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from kageha.models.base import ChatMessage
from kageha.models.router import ModelRouter


@dataclass
class MonitorVerdict:
    on_plan: bool = True
    stage_complete: bool = False
    current_stage: str = ""
    drift: str = ""
    redirect: str = ""
    escalate: bool = False

    def steering_message(self) -> str:
        parts = ["[monitor / stage-gate]"]
        if self.current_stage:
            parts.append(f"Current stage: {self.current_stage}")
        if self.on_plan:
            parts.append("Status: ON PLAN.")
        else:
            parts.append("Status: DRIFT detected.")
            if self.drift:
                parts.append(f"Drift: {self.drift}")
        if self.stage_complete:
            parts.append("Stage marked complete — checkpoint and advance.")
        if self.redirect:
            parts.append(f"Required next action: {self.redirect}")
        parts.append(
            "Stay within the plan. Prefer tools named in the current plan step. "
            "Do not invent new goals. Update todo.md checkboxes as you complete stages."
        )
        return "\n".join(parts)


async def monitor_plan_alignment(
    *,
    router: ModelRouter,
    plan_summary: str,
    plan_steps: list[str],
    goal_md: str,
    todo_md: str,
    workspace_summary: str,
    transcript_tail: str,
) -> MonitorVerdict:
    """Ask a fast critic whether the agent is still on the planned path."""
    steps_blob = "\n".join(f"- {s}" for s in plan_steps) or "(no steps)"
    prompt = (
        "You are a strict stage-gate MONITOR for an autonomous agent.\n"
        "Decide if recent work stays on the planned path (not a new rabbit hole).\n"
        "Return ONLY JSON with this schema:\n"
        "{\n"
        '  "on_plan": bool,\n'
        '  "stage_complete": bool,\n'
        '  "current_stage": str,\n'
        '  "drift": str,\n'
        '  "redirect": str,\n'
        '  "escalate": bool\n'
        "}\n"
        "Rules:\n"
        "- on_plan=false if tools/actions ignore the plan (e.g. endless bash on local files "
        "when the plan says web research).\n"
        "- stage_complete=true only when the current plan item has clear workspace evidence.\n"
        "- redirect: one concrete next action to get back on path (empty if on_plan and not done).\n"
        "- escalate=true only if stuck looping with no path back.\n\n"
        f"Plan summary:\n{plan_summary[:1500]}\n\n"
        f"Plan steps:\n{steps_blob[:3000]}\n\n"
        f"Goals:\n{goal_md[:2500]}\n\n"
        f"todo.md:\n{todo_md[:2500]}\n\n"
        f"Workspace files:\n{workspace_summary[:3000]}\n\n"
        f"Recent transcript:\n{transcript_tail[:4000]}"
    )
    try:
        _, resp = await router.chat(
            [ChatMessage(role="user", content=prompt)],
            role="monitor",
            max_tokens=768,
        )
        text = resp.message.content or ""
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return MonitorVerdict(on_plan=True, redirect="")
        data = json.loads(match.group(0))
        return MonitorVerdict(
            on_plan=bool(data.get("on_plan", True)),
            stage_complete=bool(data.get("stage_complete", False)),
            current_stage=str(data.get("current_stage") or "")[:200],
            drift=str(data.get("drift") or "")[:500],
            redirect=str(data.get("redirect") or "")[:800],
            escalate=bool(data.get("escalate", False)),
        )
    except Exception:  # noqa: BLE001
        return MonitorVerdict(on_plan=True)
