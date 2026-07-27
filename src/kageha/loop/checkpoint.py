"""Mid-run checkpoints — compact history and persist stage memory to disk."""

from __future__ import annotations

import re
from dataclasses import dataclass

from kageha.context.budget import estimate_tokens
from kageha.harness.sandbox import SessionWorkspace
from kageha.loop.goal_card import GoalCard
from kageha.models.base import ChatMessage
from kageha.models.router import ModelRouter


@dataclass
class CheckpointResult:
    path: str
    summary: str
    history: list[ChatMessage]
    history_tokens_before: int
    history_tokens_after: int


def _hist_text(messages: list[ChatMessage]) -> str:
    return "\n".join(f"{m.role}:{(m.content or '')[:500]}" for m in messages)


def history_token_estimate(history: list[ChatMessage]) -> int:
    return estimate_tokens(_hist_text(history))


async def create_checkpoint(
    *,
    workspace: SessionWorkspace,
    step: int,
    history: list[ChatMessage],
    goal: GoalCard,
    plan_summary: str,
    router: ModelRouter,
    keep_recent: int = 6,
    reason: str = "stage",
) -> CheckpointResult:
    """Break the conversation: summarize older turns, keep recent, write checkpoint file.

    Mutates nothing outside the returned history list. Caller should replace history.
    """
    before = history_token_estimate(history)
    if len(history) <= keep_recent + 1:
        # Still write a lightweight checkpoint for resume/debug
        summary = _deterministic_summary(history, goal, plan_summary)
        path = _write_checkpoint(workspace, step, summary, reason=reason, compacted=False)
        return CheckpointResult(
            path=path,
            summary=summary,
            history=list(history),
            history_tokens_before=before,
            history_tokens_after=before,
        )

    anchor = history[0]
    recent = history[-keep_recent:]
    middle = history[1:-keep_recent] if len(history) > keep_recent + 1 else []
    summary = await _summarize_middle(
        router=router,
        middle=middle,
        goal=goal,
        plan_summary=plan_summary,
        step=step,
    )
    path = _write_checkpoint(workspace, step, summary, reason=reason, compacted=True)

    bridge = ChatMessage(
        role="user",
        content=(
            f"[checkpoint @ step {step} — {reason}]\n"
            f"Prior turns were compacted to keep the context window healthy.\n"
            f"Full stage memory: {path}\n\n"
            f"{summary}\n\n"
            f"Current goals:\n{goal.to_markdown()}\n"
            "Continue from this checkpoint. Prefer reading checkpoint/todo files "
            "over re-deriving old tool output. Do not restart the whole task."
        ),
    )
    # Preserve original task message + bridge + recent tail
    new_hist: list[ChatMessage] = []
    if anchor.role == "user":
        new_hist.append(anchor)
    else:
        new_hist.append(
            ChatMessage(role="user", content=f"Task (restored): {plan_summary}")
        )
    new_hist.append(bridge)
    new_hist.extend(recent)

    after = history_token_estimate(new_hist)
    return CheckpointResult(
        path=path,
        summary=summary,
        history=new_hist,
        history_tokens_before=before,
        history_tokens_after=after,
    )


def _write_checkpoint(
    workspace: SessionWorkspace,
    step: int,
    summary: str,
    *,
    reason: str,
    compacted: bool,
) -> str:
    rel = f"checkpoints/step-{step:03d}.md"
    body = (
        f"# Checkpoint step {step}\n\n"
        f"- reason: {reason}\n"
        f"- compacted: {compacted}\n\n"
        f"## Stage memory\n\n{summary}\n"
    )
    workspace.write_text(rel, body)
    workspace.write_text("checkpoints/LATEST.md", body)
    return rel


def _deterministic_summary(
    history: list[ChatMessage],
    goal: GoalCard,
    plan_summary: str,
) -> str:
    tool_bits: list[str] = []
    for m in history[-12:]:
        if m.role == "tool" and m.name:
            preview = (m.content or "").replace("\n", " ")[:160]
            tool_bits.append(f"- {m.name}: {preview}")
        elif m.role == "assistant" and m.content and not m.tool_calls:
            tool_bits.append(f"- assistant: {(m.content or '')[:160]}")
    blob = "\n".join(tool_bits[-10:]) or "(no recent tool activity)"
    return (
        f"Plan: {plan_summary}\n\n"
        f"Goals:\n{goal.to_markdown()}\n"
        f"Recent activity:\n{blob}\n"
    )


async def _summarize_middle(
    *,
    router: ModelRouter,
    middle: list[ChatMessage],
    goal: GoalCard,
    plan_summary: str,
    step: int,
) -> str:
    if not middle:
        return _deterministic_summary([], goal, plan_summary)
    transcript = _hist_text(middle)[:8000]
    prompt = (
        "Summarize this agent transcript segment for a CHECKPOINT.\n"
        "Preserve: decisions made, files written (paths), facts found, "
        "open todos, and what NOT to redo.\n"
        "Be concise (max ~400 words). No tools. Plain markdown.\n\n"
        f"Plan: {plan_summary[:800]}\n"
        f"Goals:\n{goal.to_markdown()[:1500]}\n"
        f"Step: {step}\n\n"
        f"Transcript segment:\n{transcript}"
    )
    try:
        _, resp = await router.chat(
            [ChatMessage(role="user", content=prompt)],
            role="monitor",
            max_tokens=900,
        )
        text = (resp.message.content or "").strip()
        if text:
            # Strip accidental code fences
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
            return text[:4000]
    except Exception:  # noqa: BLE001
        pass
    return _deterministic_summary(middle, goal, plan_summary)
