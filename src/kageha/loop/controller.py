"""Async plan → act → verify → stop loop controller."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from kageha.config import (
    checkpoint_enabled,
    checkpoint_history_tokens,
    max_steps,
    max_tool_parallel,
    max_usd,
    monitor_enabled,
    monitor_every,
    post_checkpoint_guard_enabled,
)
from kageha.context.assembler import ContextAssembler
from kageha.harness.approvals import ApprovalGate, cli_approver
from kageha.harness.router import execute_tool_calls
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.builtin import load_entry_point_tools
from kageha.loop.adaptive import (
    apply_decision,
    decide_control,
    huddle_steering_message,
    repair_steering_message,
    replan_steering_message,
    retry_steering_message,
    switch_tool_steering_message,
)
from kageha.loop.checkpoint import create_checkpoint, history_token_estimate
from kageha.loop.goal_card import GoalCard
from kageha.loop.monitor import monitor_plan_alignment
from kageha.loop.planner import make_followup_plan, make_plan
from kageha.loop.resume_text import (
    is_resume_wrapper,
    unwrap_objective,
)
from kageha.loop.stop_rules import StopDecision, StopReason, StopRules
from kageha.loop.task_state import (
    ControlDecision,
    Defect,
    TaskState,
    ValidationSnapshot,
)
from kageha.loop.tool_guardrails import (
    PostCheckpointGuard,
    ToolCallGuardrailConfig,
    ToolCallGuardrailController,
    append_guidance,
    synthetic_block_result,
)
from kageha.loop.verifier import (
    VerifyResult,
    build_workspace_evidence,
    verify_with_defects,
)
from kageha.models.base import ChatMessage
from kageha.models.effort import classify_effort
from kageha.models.registry import ModelRegistry
from kageha.models.router import ModelRouter
from kageha.obs.events import EventLog


_PROJECT_SNAPSHOT_SKIP = frozenset({
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".kageha-tmp",
    "dist",
    "build",
    ".turbo",
    ".next",
    "target",
})


def _workspace_file_snapshot(
    root: Path,
    *,
    skip_dir_names: frozenset[str] | None = None,
) -> dict[str, tuple[int, int]]:
    """Stable file fingerprint used to isolate evidence to one chat turn."""
    out: dict[str, tuple[int, int]] = {}
    for path in root.rglob("*"):
        if skip_dir_names and any(p in skip_dir_names for p in path.parts):
            continue
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            out[str(path.relative_to(root))] = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            continue
    return out


def _path_relative_to_root(path: str | Path, root: Path) -> str | None:
    try:
        p = Path(path).expanduser().resolve()
        rel = p.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    text = rel.as_posix()
    if not text or text.startswith(".."):
        return None
    return text


def _changed_workspace_paths(
    root: Path,
    before: dict[str, tuple[int, int]],
    *,
    skip_dir_names: frozenset[str] | None = None,
) -> set[str]:
    # Must use the same skip set as the before-snapshot. Otherwise skipped
    # trees (node_modules, .venv, …) look brand-new every turn and get
    # mirrored into session artifacts as junk.
    after = _workspace_file_snapshot(root, skip_dir_names=skip_dir_names)
    return {rel for rel, fingerprint in after.items() if before.get(rel) != fingerprint}


_INTERNAL_REPLY_RE = re.compile(
    r"^(goals? (validated|met)|init|loop exhausted|hit max steps|"
    r"produced the requested deliverable|verified the new deliverable)",
    re.I,
)
# Failover/sanitize stubs must never be shown (or treated) as the final answer.
_TOOL_STUB_REPLY_RE = re.compile(
    r"^\s*\[(?:called tools:|prior step called)\s",
    re.I,
)


def _is_user_facing_reply(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return False
    if _TOOL_STUB_REPLY_RE.search(value):
        return False
    return not _INTERNAL_REPLY_RE.search(value)


def _latest_turn_assistant_text(
    history: list[ChatMessage],
    *,
    turn_start: int = 0,
    require_no_tool_calls: bool = False,
) -> str:
    """Return the latest current-turn assistant text (never prior chat continuity)."""
    start = max(0, min(int(turn_start), len(history)))
    for message in reversed(history[start:]):
        if message.role != "assistant":
            continue
        if require_no_tool_calls and message.tool_calls:
            continue
        text = (message.content or "").strip()
        if text:
            return message.content or ""
    return ""


def _defect_signature(task_state: TaskState) -> str:
    """Stable fingerprint of open defects — used to detect stuck repair loops."""
    defects = list(task_state.validation.defects or [])
    if not defects:
        return f"status:{task_state.validation.status}|next:{task_state.validation.next_action}"
    parts = [
        f"{d.severity}|{d.artifact}|{d.problem}|{d.repair}"
        for d in defects[:8]
    ]
    return "||".join(parts)


_SENSITIVE_ARG_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|token|password|secret|"
    r"authorization|cookie)"
)


def _sanitized_tool_action(tool_calls: list[Any]) -> str:
    """Compact action trace with credential-like values redacted."""

    def scrub(value: Any, *, key: str = "") -> Any:
        if _SENSITIVE_ARG_RE.search(key):
            return "[redacted]"
        if isinstance(value, dict):
            return {str(k): scrub(v, key=str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(item, key=key) for item in value[:8]]
        if isinstance(value, str):
            text = re.sub(
                r"(?i)(bearer\s+)[^\s\"']+",
                r"\1[redacted]",
                value,
            )
            text = re.sub(
                r"(?i)((?:api[_-]?key|token|password|secret)=)[^&\s\"']+",
                r"\1[redacted]",
                text,
            )
            return text[:500]
        return value

    actions: list[str] = []
    for call in tool_calls[:8]:
        args = scrub(call.arguments or {})
        rendered = json.dumps(args, ensure_ascii=False, default=str)
        actions.append(f"{call.name} {rendered[:600]}")
    return " | ".join(actions)


def _pending_question_from_results(
    results: list[ChatMessage],
) -> tuple[str, str, str] | None:
    for result in results:
        if result.name != "ask_human":
            continue
        try:
            payload = json.loads(result.content or "{}")
        except json.JSONDecodeError:
            continue
        if payload.get("status") != "needs_user_input":
            continue
        question = str(payload.get("question") or "").strip()
        if question:
            return (
                question,
                str(payload.get("yes_label") or "").strip(),
                str(payload.get("no_label") or "").strip(),
            )
    return None


def _format_pending_question(
    question: str,
    yes_label: str = "",
    no_label: str = "",
) -> str:
    lines = [question.strip()]
    if yes_label or no_label:
        lines.extend(
            [
                "",
                f"[Y] {yes_label or 'Yes'}",
                f"[N] {no_label or 'No'}",
            ]
        )
    return "\n".join(lines).strip()


async def _compose_turn_answer(
    *,
    router: ModelRouter,
    objective: str,
    status: str,
    goal: GoalCard,
    history: list[ChatMessage],
    turn_artifacts: list[str],
) -> str:
    """Compose the one durable chat answer from current-turn evidence only."""
    tool_evidence: list[str] = []
    for message in history:
        if message.role != "tool" or not (message.content or "").strip():
            continue
        compact = re.sub(r"\s+", " ", message.content.strip())
        tool_evidence.append(
            f"{message.name or 'tool'}: {compact[:900]}"
        )
    evidence = "\n".join(f"- {item}" for item in tool_evidence[-8:])
    goal_evidence = "\n".join(
        f"- {item.description}: {item.evidence}"
        for item in goal.items
        if item.evidence
    )
    incomplete = (status or "").lower() not in {"success", ""}
    status_note = ""
    if incomplete:
        status_note = (
            "The run ended before full completion "
            f"(status={status}). Summarize what was accomplished, what is blocked, "
            "and any useful partial artifacts. Do not pretend the task finished.\n"
        )
    prompt = (
        "Write the final answer to the user for this single chat turn.\n"
        "Be direct, natural, and concise, like a strong coding agent.\n"
        "Lead with the outcome. Mention paths only for files created or changed "
        "during this turn. Never mention goals, verifier, loop, steps, TaskState, "
        "internal status, or prior-turn results. Do not claim anything not shown "
        "in the evidence. If the request was simply to open a browser, confirm "
        "what page was opened and give the current-turn screenshot path.\n"
        f"{status_note}\n"
        f"User request: {objective}\n"
        f"Run status: {status}\n"
        f"Current-turn artifacts: {turn_artifacts or '(none)'}\n"
        f"Verified goal evidence:\n{goal_evidence or '(none)'}\n"
        f"Current-turn tool evidence:\n{evidence or '(none)'}"
    )
    try:
        # Hermes-hard grace: no tools; scrub any leaked tool_calls.
        _, response = await router.chat(
            [
                ChatMessage(
                    role="user",
                    content=(
                        prompt
                        + "\n\nReply in plain text only. Do not call tools."
                    ),
                )
            ],
            [],
            role="fast_worker",
            max_tokens=700,
        )
        message = response.message
        if message.tool_calls:
            # Provider ignored tools=[] — never resume the tool loop from grace.
            answer = (message.content or "").strip()
            if not answer:
                answer = (
                    "I reached a stop condition and could not produce a clean "
                    "summary without further tool use."
                )
        else:
            answer = (message.content or "").strip()
        if _is_user_facing_reply(answer):
            return answer
    except Exception:  # noqa: BLE001
        pass

    if status == StopReason.SUCCESS.value:
        if tool_evidence:
            return re.sub(r"^[^:]+:\s*", "", tool_evidence[-1])[:1200]
        if turn_artifacts:
            return "Completed. Saved:\n" + "\n".join(
                f"- {path}" for path in turn_artifacts
            )
        return "Completed successfully."
    if turn_artifacts:
        return (
            "I couldn't fully finish that request, but saved partial files:\n"
            + "\n".join(f"- {path}" for path in turn_artifacts[:8])
        )
    if tool_evidence:
        return (
            "I couldn't fully finish that request. Latest tool evidence:\n"
            + tool_evidence[-1][:1200]
        )
    return ""


@dataclass
class RunResult:
    run_id: str
    status: str
    message: str
    goal: GoalCard
    steps: int
    spent_usd: float
    artifacts: list[str] = field(default_factory=list)
    turn_id: str = ""
    turn_artifacts: list[str] = field(default_factory=list)
    validated: bool = False
    verified_facts: list[str] = field(default_factory=list)
    verification_evidence: str = ""
    recovered_failures: list[str] = field(default_factory=list)
    active_skills: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)


class LoopController:
    def __init__(
        self,
        *,
        auto_approve: bool = False,
        auto_build: bool = False,
        approver: Any = None,
        attached_kbs: list[str] | None = None,
        skill_catalog: str = "",
        kb_pins: str = "",
        system_extra: str = "",
        planning_role: str = "planning",
        execution_role: str = "tool_calling",
        live: bool = True,
        max_steps_limit: int | None = None,
        export_dir: Path | None = None,
        log_handler: Callable[[str], None] | None = None,
        cancel_event: asyncio.Event | None = None,
        inject_queue: asyncio.Queue[str] | None = None,
        defer_human_input: bool = False,
        memory_user_id: str = "local",
        memory_agent_id: str = "main",
        memory_channel_key: str = "",
        platform: str = "cli",
        model_override: str | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
        runtime_journal: Any = None,
        provider_control: Any = None,
        security_profile: Any = "approval_fallback",
        approval_audit: Callable[[Any, str], None] | None = None,
        project_root: str = "",
    ) -> None:
        self.auto_approve = auto_approve
        # Plan Build gate — independent of tool auto_approve.
        self.auto_build = auto_build
        self.approver = approver or (None if auto_approve else cli_approver)
        self.attached_kbs = attached_kbs or []
        self.skill_catalog = skill_catalog
        self.kb_pins = kb_pins
        self.system_extra = system_extra
        self.planning_role = planning_role
        self.execution_role = execution_role
        self.live = live
        self.max_steps_limit = max_steps_limit
        self.export_dir = export_dir
        self.log_handler = log_handler
        self.cancel_event = cancel_event or asyncio.Event()
        self.inject_queue = inject_queue or asyncio.Queue()
        self.defer_human_input = defer_human_input
        self.memory_user_id = memory_user_id
        self.memory_agent_id = memory_agent_id
        self.memory_channel_key = memory_channel_key
        self.platform = platform or "cli"
        self.model_override = model_override
        self.event_sink = event_sink
        self.runtime_journal = runtime_journal
        self.provider_control = provider_control
        self.security_profile = security_profile
        self.project_root = (project_root or "").strip()
        from kageha.runtime.security import ExecutionSecurityPolicy
        from kageha.runtime.types import SecurityProfile

        selected_security = (
            security_profile
            if isinstance(security_profile, SecurityProfile)
            else SecurityProfile(str(security_profile))
        )
        self.security_policy = ExecutionSecurityPolicy(selected_security)
        self.approval_audit = approval_audit
        self.pending_user_messages: list[str] = []
        from kageha.project.hooks import load_hook_runner

        self.hooks = load_hook_runner(self.project_root or None)

    def _log(self, msg: str) -> None:
        if self.live:
            if self.log_handler is not None:
                self.log_handler(msg)
            else:
                print(msg, flush=True)

    def _log_checklist(
        self,
        markdown: str,
        *,
        label: str = "todos",
        events: Any | None = None,
    ) -> None:
        """Emit a compact checklist snapshot for chat progress UIs."""
        from kageha.loop.todo_board import board_log_lines, parse_todo_markdown

        board = parse_todo_markdown(markdown, label=label)
        if not board.get("total"):
            return
        self._log(board_log_lines(board))
        # Structured board for WebUI / runtime journal (todos only).
        if events is not None and (label or "todos") == "todos":
            events.emit("todo_board", board)

    def _log_todo_board(
        self,
        workspace: SessionWorkspace,
        *,
        label: str = "todos",
        events: Any | None = None,
    ) -> None:
        from kageha.loop.todo_board import board_log_lines, parse_todo_file

        board = parse_todo_file(workspace.path("todo.md"), label=label)
        if board is None:
            return
        self._log(board_log_lines(board))
        if events is not None:
            events.emit("todo_board", board)
        self._todo_board_fp = self._todo_board_fingerprint(board)

    @staticmethod
    def _todo_board_fingerprint(board: dict[str, Any] | None) -> tuple[Any, ...]:
        if not board:
            return ()
        items = tuple(
            (str(it.get("id") or ""), bool(it.get("done")), str(it.get("text") or ""))
            for it in (board.get("items") or [])
        )
        return (board.get("done"), board.get("total"), items)

    def _refresh_todo_board_if_changed(
        self,
        workspace: SessionWorkspace,
        *,
        label: str = "todos",
        events: Any | None = None,
    ) -> bool:
        """Re-emit the live board when todo.md progress changed (any write path)."""
        from kageha.loop.todo_board import parse_todo_file

        board = parse_todo_file(workspace.path("todo.md"), label=label)
        fp = self._todo_board_fingerprint(board)
        if fp == getattr(self, "_todo_board_fp", None):
            return False
        if board is None:
            self._todo_board_fp = fp
            return False
        self._log_todo_board(workspace, label=label, events=events)
        return True

    def _sync_todo_board_from_progress(
        self,
        workspace: SessionWorkspace,
        *,
        goal: Any,
        task_state: Any,
        events: Any | None = None,
        success: bool = False,
    ) -> None:
        """Mirror goal_card / plan stage progress into todo.md for the WebUI board."""
        from kageha.loop.todo_board import sync_todo_file_from_progress

        todo_path = workspace.path("todo.md")
        if sync_todo_file_from_progress(
            todo_path,
            goal=goal,
            stages=getattr(task_state, "stages", None),
            success=success,
        ):
            self._log_todo_board(workspace, label="todos", events=events)

    def _tool_result_touches_todo(self, result: Any) -> bool:
        name = str(getattr(result, "name", None) or "")
        if name in {"todo_write", "todo_read"}:
            return True
        if name in {"write_file", "edit_file"}:
            content = str(getattr(result, "content", None) or "")
            return "todo.md" in content
        return False

    def _reload_plan_from_disk(
        self,
        workspace: SessionWorkspace,
        plan: Any,
        goal: Any,
    ) -> Any:
        """Re-read session plan.md so Build uses saved edits, not stale memory."""
        from kageha.loop.mode_policy import apply_saved_plan_markdown

        plan_path = workspace.path("plan.md")
        if not plan_path.is_file():
            return plan
        try:
            text = plan_path.read_text(encoding="utf-8")
        except OSError:
            return plan
        updated = apply_saved_plan_markdown(plan, text)
        if updated is plan:
            return plan
        # Keep plan.json / todo.md aligned with the disk checklist for execute.
        try:
            workspace.write_text(
                "plan.json",
                json.dumps(
                    {
                        "summary": updated.summary,
                        "source": updated.source,
                        "steps": [
                            {
                                "id": s.id,
                                "description": s.description,
                                "tools": s.tools,
                            }
                            for s in updated.steps
                        ],
                    },
                    indent=2,
                ),
            )
            goal_md = ""
            if goal is not None and hasattr(goal, "to_markdown"):
                try:
                    goal_md = goal.to_markdown()
                except Exception:  # noqa: BLE001
                    goal_md = ""
            workspace.write_text(
                "todo.md",
                "# Plan\n\n"
                + "\n".join(f"- [ ] {s.id}: {s.description}" for s in updated.steps)
                + ("\n\n" + goal_md if goal_md else "\n"),
            )
        except OSError as exc:
            self._log(f"[kageha] plan disk sync skipped: {exc}")
        self._log(
            f"[kageha] reloaded plan.md from disk ({len(updated.steps)} steps)"
        )
        return updated

    def inject(self, message: str) -> None:
        self.pending_user_messages.append(message)

    def cancel(self) -> None:
        self.cancel_event.set()

    async def run(
        self,
        task: str,
        *,
        run_id: str | None = None,
        workspace: SessionWorkspace | None = None,
        fresh_turn: bool = False,
        turn_task: str | None = None,
        loop_mode: str = "full",
        agent_mode: str = "normal",
    ) -> RunResult:
        if workspace is None:
            raise RuntimeError(
                "LoopController is an internal execution component; "
                "submit work through AgentRuntime"
            )
        if run_id is not None and run_id != workspace.run_id:
            raise ValueError("run_id does not match the supplied workspace")
        turn_id = uuid.uuid4().hex[:12]
        turn_objective = (turn_task or task).strip()
        from kageha.chat.turn_manager import ESCALATE_PLAN_FLAG
        from kageha.loop.mode_policy import (
            GOAL_QA_MISFIT_MESSAGE,
            clear_agent_mode_flag,
            clear_clarify_pending,
            clear_plan_approved,
            fold_clarify_answer,
            goal_qa_misfit,
            is_mode_only_message,
            is_plan_build_prompt,
            is_plan_revise_turn,
            loop_mode_for,
            mark_plan_approved,
            mode_only_ack,
            mode_system_extra,
            mutations_blocked_until_approve,
            normalize_agent_mode,
            parse_mode_slash,
            plan_already_approved,
            plan_clarify_question,
            plan_needs_clarify,
            read_clarify_pending,
            requires_plan_approval,
            resolve_agent_mode,
            strip_mode_slash,
            write_clarify_pending,
            write_agent_mode_flag,
            write_plan_artifact,
        )

        # Precedence: slash on raw message → explicit API/CLI → workspace flag.
        explicit_mode = str(agent_mode or "").strip() or None
        raw_turn_objective = turn_objective
        # Mode-only (/plan, bare "plan"): never invent Objective: plan + Build.
        stripped = strip_mode_slash(turn_objective)
        if is_mode_only_message(turn_objective) or not stripped:
            mode_hint = normalize_agent_mode(
                parse_mode_slash(turn_objective)
                or explicit_mode
                or turn_objective.lstrip("/")
                or "plan"
            )
            if mode_hint != "normal":
                write_agent_mode_flag(workspace.root, mode_hint)
            else:
                clear_agent_mode_flag(workspace.root)
            return RunResult(
                run_id=workspace.run_id,
                status="success",
                message=mode_only_ack(mode_hint),
                goal=GoalCard.from_task(f"(mode switch: {mode_hint})"),
                steps=0,
                spent_usd=0.0,
                artifacts=[],
                turn_id=turn_id,
            )
        turn_objective = stripped
        resolved_agent_mode = resolve_agent_mode(
            raw_turn_objective,
            explicit=explicit_mode,
            workspace_root=workspace.root,
            consume_flag=True,
        )
        # Drop leftover flag even when explicit/slash won, so it cannot stick.
        clear_agent_mode_flag(workspace.root)

        # Fresh Plan design must re-gate Build — never reuse a prior
        # plan_approved.flag from an earlier turn in this session.
        if fresh_turn and requires_plan_approval(resolved_agent_mode):
            clear_plan_approved(workspace.root)

        # Goal + informational Q&A → Normal/followup (no DAG/skill theater, no Build).
        goal_qa_soft_redirect = goal_qa_misfit(resolved_agent_mode, turn_objective)
        if goal_qa_soft_redirect:
            resolved_agent_mode = "normal"

        mode = (loop_mode or "").strip().lower()
        # act = alias for followup (one-step plan, sparse verify).
        if mode == "act":
            mode = "followup"
        if mode not in {"full", "followup"}:
            mode = loop_mode_for(resolved_agent_mode)
        # Prior escalate_plan tool call → full executive loop this turn.
        # Explicit Normal (chip / agent_mode=normal) wins — never misroute a
        # Normal send into Plan explore→Build from a stale escalate flag.
        escalate_flag = workspace.root / ESCALATE_PLAN_FLAG
        if escalate_flag.is_file():
            explicit_normal = (
                explicit_mode is not None
                and normalize_agent_mode(explicit_mode) == "normal"
                and parse_mode_slash(raw_turn_objective) is None
            )
            if explicit_normal:
                try:
                    escalate_flag.unlink()
                except OSError:
                    pass
                self._log(
                    "[kageha] escalate_plan flag ignored — explicit Normal mode"
                )
            else:
                mode = "full"
                if resolved_agent_mode == "normal":
                    resolved_agent_mode = "plan"
                try:
                    escalate_flag.unlink()
                except OSError:
                    pass
                self._log(
                    f"[kageha] escalate_plan flag — full loop "
                    f"(agent_mode={resolved_agent_mode})"
                )
                goal_qa_soft_redirect = False
        elif resolved_agent_mode != "normal":
            mode = "full"
        if goal_qa_soft_redirect:
            # Win over client loop_mode=full sent with Goal chip.
            mode = "followup"
        effort = classify_effort(turn_objective)
        if mode == "followup":
            effort = "low"
        elif resolved_agent_mode != "normal" and effort == "low":
            # Short prompts must not collapse plan/goal into followup-like
            # sparse verify — deep modes need a real plan→verify cadence.
            effort = "medium"
        turn_snapshot = _workspace_file_snapshot(workspace.root)
        project_root_path: Path | None = None
        project_turn_snapshot: dict[str, tuple[int, int]] = {}
        if self.project_root:
            try:
                pr = Path(self.project_root).expanduser().resolve()
            except OSError:
                pr = None
            if pr is not None and pr.is_dir() and pr != workspace.root.resolve():
                project_root_path = pr
                project_turn_snapshot = _workspace_file_snapshot(
                    pr, skip_dir_names=_PROJECT_SNAPSHOT_SKIP
                )
        events = EventLog(
            path=workspace.root / "events.jsonl",
            sink=self.event_sink,
        )
        events.emit(
            "run_start",
            {
                "task": task,
                "turn_task": turn_objective,
                "effort": effort,
                "run_id": workspace.run_id,
                "turn_id": turn_id,
                "fresh_turn": fresh_turn,
                "agent_mode": resolved_agent_mode,
                "loop_mode": mode,
                "goal_qa_soft_redirect": goal_qa_soft_redirect,
            },
        )
        if goal_qa_soft_redirect:
            events.emit(
                "goal_qa_misfit",
                {
                    "message": GOAL_QA_MISFIT_MESSAGE,
                    "suggested_mode": "normal",
                    "original_mode": "goal",
                    "task": turn_objective[:240],
                },
            )
            self._log(
                f"[kageha] Goal Q&A misfit — soft Normal "
                f"({GOAL_QA_MISFIT_MESSAGE})"
            )
        self._log(f"[kageha] run_id={workspace.run_id}")
        self._log(f"[kageha] workspace={workspace.root}")
        self._log(
            f"[kageha] agent_mode={resolved_agent_mode} loop_mode={mode} "
            f"effort={effort}"
        )
        self._log(f"[kageha] task={task[:200]}{'…' if len(task) > 200 else ''}")

        # Copy absolute paths mentioned in the task into inputs/ (sandbox-safe)
        seeded_note = ""
        try:
            from kageha.harness.inputs import seed_task_inputs

            seeded = seed_task_inputs(task, workspace)
            if seeded:
                events.emit("inputs_seeded", {"files": seeded})
                listing = ", ".join(s["dest"] for s in seeded)
                self._log(f"[kageha] seeded inputs: {listing}")
                seeded_note = (
                    "\n\n## Seeded inputs\n"
                    "These files were copied from absolute paths in the task "
                    "into the session workspace — prefer these relative paths:\n"
                    + "\n".join(f"- `{s['source']}` → `{s['dest']}`" for s in seeded)
                )
        except Exception as e:  # noqa: BLE001
            events.emit("inputs_seed_error", {"error": str(e)})

        # Progressive disclosure: inject top skill bodies matching the task
        system_extra = (
            self.system_extra
            + seeded_note
            + "\n\n"
            + mode_system_extra(resolved_agent_mode)
        )
        # Native browser / research prefs (set via /browser or ~/.kageha/browser.json)
        try:
            from kageha.harness.browser.prefs import apply_browser_prefs

            prefs = apply_browser_prefs()
            system_extra = (
                (system_extra or "")
                + "\n\n## Browser / research backend\n"
                + f"- backend: `{prefs.backend}`\n"
                + f"- cdp: `{prefs.cdp}`\n"
                + f"- research_depth: `{prefs.research_depth}`\n"
                + f"- browser_pack: `{'on' if prefs.enable_browser_pack else 'off'}`\n"
                + "- For research questions call `research_run` first.\n"
                + "- User can change backend with `/browser use <name>`.\n"
            )
        except Exception:  # noqa: BLE001
            pass
        # Project brain (AGENTS.md / KAGEHA.md / CLAUDE.md / .kageha/rules)
        try:
            from kageha.project.brain import load_project_brain, render_project_brain

            brain = load_project_brain(self.project_root or None)
            brain_text = render_project_brain(brain)
            if brain_text:
                system_extra = (system_extra or "") + "\n\n" + brain_text
                events.emit(
                    "project_brain",
                    {
                        "root_file": brain.root_file if brain else "",
                        "rules": len(brain.rules) if brain else 0,
                        "commands": list(brain.command_names) if brain else [],
                        "chars": len(brain_text),
                    },
                )
                self._log(
                    "[kageha] project brain: "
                    + (
                        brain.root_file
                        if brain and brain.root_file
                        else f"{len(brain.rules) if brain else 0} rules"
                    )
                )
        except Exception as e:  # noqa: BLE001
            events.emit("project_brain_error", {"error": str(e)})
        auto_skill_names: list[str] = []
        try:
            from kageha.loop.mode_policy import plan_skill_match_text
            from kageha.memory.skills import (
                SkillRegistry,
                extract_path_hints,
                parse_skill_invocations,
            )

            reg = SkillRegistry()
            forced = parse_skill_invocations(task, reg)
            skill_query = plan_skill_match_text(workspace.root, task) or task
            path_hints = extract_path_hints(
                skill_query, project_root=self.project_root or None
            )
            auto_load = reg.auto_load_for_task(
                skill_query,
                limit=4,
                max_chars=8000,
                force_names=forced or None,
                path_hints=path_hints or None,
            )
            auto_skill_names = list(auto_load.names)
            if auto_load.text:
                system_extra = (system_extra or "") + "\n\n" + auto_load.text
                events.emit(
                    "skill_autoload",
                    {
                        "chars": len(auto_load.text),
                        "skills": auto_skill_names,
                        "scores": dict(auto_load.scores),
                        "forced": list(forced),
                        "path_hints": path_hints[:12],
                        "query_preview": skill_query[:240],
                        "preview": auto_load.text[:200],
                    },
                )
                self._log(
                    "[kageha] auto-loaded matching skills: "
                    + ", ".join(auto_skill_names)
                )
        except Exception as e:  # noqa: BLE001
            events.emit("skill_autoload_error", {"error": str(e)})

        registry = ModelRegistry.load()
        router = ModelRouter(registry, provider_control=self.provider_control)
        override = self.model_override or workspace.get_model_override()
        once = workspace.get_model_once()
        role_slots = workspace.get_model_role_overrides()
        if once:
            router.set_once_override(once)

            def _clear_once(_mid: str) -> None:
                workspace.set_model_once(None)

            router.on_once_consumed = _clear_once
            self._log(f"[kageha] model_once={once}")
            events.emit("model_once", {"model_id": once})
        if role_slots:
            from kageha.chat.model_commands import expand_role_overrides

            expanded = expand_role_overrides(role_slots)
            router.set_role_overrides(expanded)
            self._log(f"[kageha] model_role_overrides={role_slots}")
            events.emit(
                "model_role_overrides",
                {"slots": dict(role_slots), "roles": expanded},
            )
        if override:
            # Persist so /resume and later turns keep the pin.
            if self.model_override and workspace.get_model_override() != override:
                workspace.set_model_override(override)
            router.set_session_override(override)
            self._log(f"[kageha] model_override={override}")
            events.emit("model_override", {"model_id": override})
        gate = ApprovalGate(
            approver=self.approver,
            auto_approve=self.auto_approve,
            audit=self.approval_audit,
        )
        ctx = HarnessContext(
            workspace=workspace,
            approvals=gate,
            router=router,
            attached_kbs=list(self.attached_kbs),
            cancel_event=self.cancel_event,
            project_root=self.project_root or "",
        )
        ctx.meta["defer_human_input"] = self.defer_human_input
        ctx.meta["current_user_text"] = turn_task or task
        ctx.meta["memory_user_id"] = self.memory_user_id
        ctx.meta["memory_agent_id"] = self.memory_agent_id
        ctx.meta["memory_channel_key"] = self.memory_channel_key
        ctx.meta["agent_mode"] = resolved_agent_mode
        ctx.meta["project_root"] = self.project_root or ""
        ctx.meta["security_profile"] = (
            self.security_profile.value
            if hasattr(self.security_profile, "value")
            else str(self.security_profile)
        )
        # Closest Hermes: soft observe/refine on local TTY (chat REPL + CLI).
        # Channel adapters (WhatsApp/etc.) set a channel asker → soft off in
        # skill_learn_soft_enabled. Do not key off defer_human_input — chat
        # sets that True so ask_human routes to the chat UI, but chat is still
        # interactive for skill learning.
        ctx.meta["skill_learn_interactive"] = bool(sys.stdin.isatty())
        ctx.meta["events"] = events
        if auto_skill_names:
            from kageha.harness.tools.skills_tools import activate_skills

            activate_skills(ctx, auto_skill_names)
        ctx.tools = load_entry_point_tools(ctx)
        removed_tools = ctx.tools.apply_tool_policy()
        if removed_tools:
            events.emit("tool_policy", {"removed": removed_tools})
            self._log(
                f"[kageha] tool_policy removed: {', '.join(removed_tools[:12])}"
                + ("…" if len(removed_tools) > 12 else "")
            )
        # Connect MCP servers and register remote tools.
        # Skip on desktop computer-use turns — MCP adds latency and isn't needed.
        # Also skip for short Normal/followup chat (Q&A) — big win for WebUI snappy replies.
        _skip_mcp = False
        _skip_mcp_reason = "computer_use"
        try:
            from kageha.harness.tools.computer_ready import task_wants_computer

            _skip_mcp = task_wants_computer(turn_task or task) or (
                "computer_use" in auto_skill_names
            )
        except Exception:  # noqa: BLE001
            _skip_mcp = False
        if not _skip_mcp and (
            mode == "followup" or resolved_agent_mode == "normal"
        ) and len((turn_task or task or "").strip()) < 240:
            _skip_mcp = True
            _skip_mcp_reason = "short_chat"
        if _skip_mcp:
            self._log(f"[kageha] MCP skipped ({_skip_mcp_reason})")
            events.emit("mcp_skipped", {"reason": _skip_mcp_reason})
        else:
            try:
                from kageha.harness.tools.mcp_tools import connect_mcp_into_context

                hub = await connect_mcp_into_context(ctx)
                n_ok = sum(1 for r in hub.status() if r.get("ok"))
                n_tools = sum(
                    len(c.tools) for c in hub.connected.values() if c.ok
                )
                if n_ok:
                    self._log(f"[kageha] MCP: {n_ok} server(s), {n_tools} tool(s)")
            except Exception as e:  # noqa: BLE001
                events.emit("mcp_connect_error", {"error": str(e)})
                self._log(f"[kageha] MCP connect skipped: {e}")

        warnings = list(ctx.meta.get("tool_load_warnings") or [])
        if warnings:
            events.emit("tool_load_warnings", {"warnings": warnings})
            self._log(
                "[kageha] tool pack warnings:\n- " + "\n- ".join(warnings),
            )

        # Foolproof computer-use: exclusive skill + fail-closed readiness.
        wants_computer = False
        try:
            from kageha.harness.tools.computer_ready import (
                ensure_computer_ready,
                task_wants_computer,
            )
            from kageha.harness.tools.skills_tools import activate_skills

            packs_enabled = list(ctx.meta.get("tool_packs_enabled") or [])
            wants_computer = task_wants_computer(turn_task or task) or (
                "computer_use" in auto_skill_names
            )
            if wants_computer:
                if "computer" not in packs_enabled:
                    msg = (
                        "Computer-use pack is not enabled — continuing without "
                        "desktop tools. On macOS with cua-driver installed it "
                        "auto-enables; or set tools.yaml packs: [computer] / "
                        "KAGEHA_TOOL_PACKS=computer."
                    )
                    events.emit(
                        "computer_ready",
                        {
                            "ok": False,
                            "pack_enabled": False,
                            "skipped": True,
                            "message": msg,
                        },
                    )
                    self._log(f"[kageha] computer_ready skip: {msg}")
                    # Soft-skip: drop computer_use so web_research/browser can run.
                    auto_skill_names = [
                        n for n in auto_skill_names if n != "computer_use"
                    ]
                    active = list(ctx.meta.get("active_skills") or [])
                    if "computer_use" in active:
                        ctx.meta["active_skills"] = [
                            n for n in active if n != "computer_use"
                        ]
                    # Drop already-injected computer_use skill body from context.
                    if system_extra and "### skill:computer_use" in system_extra:
                        parts = system_extra.split("### skill:")
                        kept = [parts[0]]
                        for chunk in parts[1:]:
                            if chunk.startswith("computer_use"):
                                continue
                            kept.append("### skill:" + chunk)
                        system_extra = "".join(kept)
                else:
                    # Exclusive: drop web_browse allowlist so computer_* never vanish.
                    ctx.meta["active_skills"] = []
                    ctx.meta["skill_allowed_tools"] = None
                    activate_skills(ctx, ["computer_use"])
                    auto_skill_names = ["computer_use"]
                    ready = await ensure_computer_ready(pack_enabled=True)
                    events.emit("computer_ready", ready.as_dict())
                    self._log(f"[kageha] computer_ready: {ready.message}")
                    if not ready.ok:
                        return RunResult(
                            run_id=workspace.run_id,
                            status="error",
                            message=ready.message,
                            goal=GoalCard.from_task(turn_task or task),
                            steps=0,
                            spent_usd=0.0,
                            turn_id=turn_id,
                            active_skills=["computer_use"],
                        )
                    # Prefer a native tool model for this run when session pin is CLI-only.
                    pin = router.session_override
                    if (
                        ready.tool_model_id
                        and pin
                        and not router._model_supports_tool_calling(pin)
                    ):
                        router.set_once_override(ready.tool_model_id)
                        self._log(
                            f"[kageha] computer_use model once={ready.tool_model_id} "
                            f"(session pin {pin} cannot call tools)"
                        )
        except Exception as e:  # noqa: BLE001
            events.emit("computer_ready_error", {"error": str(e)})
            self._log(f"[kageha] computer_ready skipped: {e}")

        # Plan. Chat follow-ups share the workspace but use a one-step plan
        # (no LLM planner) so "pause"/"louder" don't rebuild a 5-stage mission.
        goal_path = workspace.path("goal_card.json")
        plan_path = workspace.path("plan.json")
        explore_notes = ""
        # Plan + new objective on a resumed session → full explore/plan.md,
        # never a followup stub written as plan.md before the real plan exists.
        if (
            mode != "followup"
            and not fresh_turn
            and requires_plan_approval(resolved_agent_mode)
            and goal_path.is_file()
            and plan_path.is_file()
        ):
            try:
                _prior_goal = GoalCard.load(goal_path)
                _prior_task = unwrap_objective(
                    _prior_goal.task or "", fallback=(_prior_goal.task or "")
                ).strip()
                _new_task = unwrap_objective(
                    turn_objective or "", fallback=(turn_objective or "")
                ).strip()
                if _new_task and _prior_task and _prior_task != _new_task:
                    self._log(
                        "[kageha] different ask on Plan — fresh design turn "
                        f"(prior={_prior_task[:80]!r})"
                    )
                    try:
                        workspace.write_text(
                            "goal_card_prior.json",
                            goal_path.read_text(encoding="utf-8"),
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    fresh_turn = True
            except Exception:  # noqa: BLE001
                pass
        if mode == "followup":
            self._log("[kageha] followup mode — one-step plan (skip LLM planner)")
            plan = make_followup_plan(turn_objective)
            # Always a fresh act goal for THIS turn. Reusing a prior completed
            # goal_card made stop_rules fire SUCCESS after the first tool call
            # (e.g. browser_connect only — never browser_open).
            if goal_path.is_file():
                try:
                    prior = GoalCard.load(goal_path)
                    if prior.task and prior.task.strip() != turn_objective.strip():
                        workspace.write_text(
                            "goal_card_prior.json",
                            goal_path.read_text(encoding="utf-8"),
                        )
                except Exception:  # noqa: BLE001
                    pass
            goal = GoalCard.from_task(turn_objective, milestones=plan.milestones)
            goal.save(goal_path)
            workspace.write_text(
                "plan.json",
                json.dumps(
                    {
                        "summary": plan.summary,
                        "source": plan.source,
                        "steps": [
                            {
                                "id": s.id,
                                "description": s.description,
                                "tools": s.tools,
                            }
                            for s in plan.steps
                        ],
                    },
                    indent=2,
                ),
            )
        elif not fresh_turn and goal_path.is_file() and plan_path.is_file():
            self._log("[kageha] resuming — loading plan + goals from workspace")
            goal = GoalCard.load(goal_path)
            # Repair nested chat-resume wrappers that bloated goal.task
            if is_resume_wrapper(goal.task) or len(goal.task or "") > 4000:
                goal.task = unwrap_objective(goal.task, fallback=task)
                goal.save(goal_path)
                self._log(f"[kageha] repaired goal.task → {goal.task[:120]}…")
            # New chat follow-up with a different ask must not inherit a prior
            # (often completed / validation-pass) goal — that made stop_rules fire
            # SUCCESS after the first tool and re-emit the previous turn's answer.
            # Engine may set fresh_turn=False for resumed full mode; this reset wins.
            prior_task = unwrap_objective(
                goal.task or "", fallback=(goal.task or "")
            ).strip()
            new_task = unwrap_objective(
                turn_objective or "", fallback=(turn_objective or "")
            ).strip()
            validation_pass = False
            _state_peek = workspace.path("task_state.json")
            if _state_peek.is_file():
                try:
                    _peek = TaskState.load(_state_peek)
                    validation_pass = (
                        str(_peek.validation.status or "").lower() == "pass"
                        or _peek.validated_ok()
                    )
                except Exception:  # noqa: BLE001
                    validation_pass = False
            different_ask = bool(new_task and prior_task and prior_task != new_task)
            # Any divergent ask on full-mode resume isolates the turn. Completed
            # goals / validation-pass are the common sticky case that made
            # stop_rules SUCCESS immediately; also reset for in-progress priors.
            # (Plan different-ask already forced fresh_turn above.)
            if different_ask:
                self._log(
                    "[kageha] different ask on resumed full mode — "
                    "starting a fresh follow-up plan "
                    f"(goal_passed={goal.all_passed()} validation_pass={validation_pass})"
                )
                if goal_path.is_file():
                    try:
                        workspace.write_text(
                            "goal_card_prior.json",
                            goal_path.read_text(encoding="utf-8"),
                        )
                    except Exception:  # noqa: BLE001
                        pass
                plan = make_followup_plan(turn_objective)
                goal = GoalCard.from_task(turn_objective, milestones=plan.milestones)
                goal.save(goal_path)
                workspace.write_text(
                    "plan.json",
                    json.dumps(
                        {
                            "summary": plan.summary,
                            "source": plan.source,
                            "steps": [
                                {
                                    "id": s.id,
                                    "description": s.description,
                                    "tools": s.tools,
                                }
                                for s in plan.steps
                            ],
                        },
                        indent=2,
                    ),
                )
                # Ensure TaskState validation/goals reset for this new ask so
                # stop_rules cannot SUCCESS immediately from prior passes.
                fresh_turn = True
            else:
                try:
                    pdata = json.loads(plan_path.read_text())
                    from kageha.loop.planner import PlanStep, TaskPlan

                    plan = TaskPlan(
                        summary=str(pdata.get("summary") or "resumed"),
                        source=str(pdata.get("source") or "resume"),
                        steps=[
                            PlanStep(
                                id=str(s.get("id") or f"s{i}"),
                                description=str(s.get("description") or ""),
                                tools=list(s.get("tools") or []),
                            )
                            for i, s in enumerate(pdata.get("steps") or [])
                        ],
                        milestones=[i.description for i in goal.items],
                    )
                except Exception:  # noqa: BLE001
                    plan = await make_plan(
                        task,
                        router,
                        role=self.planning_role,
                        available_tools={spec.name for spec in ctx.tools.specs()},
                        effort=effort,
                    )
        else:
            self._log(f"[kageha] planning… (effort={effort})")
            # Plan: fold clarify answer, ask if needed, or revise awaiting plan.
            revising = is_plan_revise_turn(
                workspace.root,
                resolved_agent_mode,
                turn_objective,
                auto_build=self.auto_build,
            )
            pending = (
                read_clarify_pending(workspace.root)
                if requires_plan_approval(resolved_agent_mode)
                else None
            )
            if pending:
                answer = turn_objective.strip()
                turn_objective = fold_clarify_answer(
                    str(pending.get("objective") or turn_objective), answer
                )
                clear_clarify_pending(workspace.root)
                events.emit("clarify", {"status": "answered", "chars": len(answer)})
                self._log("[kageha] plan clarify answered — continuing design")
            elif (
                requires_plan_approval(resolved_agent_mode)
                and not plan_already_approved(workspace.root)
                and not revising
                and not is_plan_build_prompt(turn_objective)
                and plan_needs_clarify(turn_objective)
            ):
                question = plan_clarify_question(turn_objective)
                events.emit(
                    "clarify", {"status": "asking", "question": question[:500]}
                )
                if self.defer_human_input:
                    try:
                        write_clarify_pending(
                            workspace.root,
                            objective=turn_objective,
                            question=question,
                        )
                    except OSError:
                        pass
                    self._log("[kageha] plan clarify — awaiting answer")
                    return RunResult(
                        run_id=workspace.run_id,
                        status="awaiting_clarify",
                        message=question,
                        goal=GoalCard.from_task(turn_objective),
                        steps=0,
                        spent_usd=0.0,
                        artifacts=[],
                        turn_id=turn_id,
                        active_skills=list(auto_skill_names),
                    )
                from kageha.harness.approvals import cli_ask_human

                answer = (await cli_ask_human(question)).strip() or (
                    "(no preference — pick a sensible default)"
                )
                turn_objective = fold_clarify_answer(turn_objective, answer)
                events.emit("clarify", {"status": "answered", "chars": len(answer)})
            if revising:
                ctx.meta["plan_approved"] = False
                ctx.meta["design_phase"] = True
                events.emit(
                    "plan_revise",
                    {
                        "agent_mode": resolved_agent_mode,
                        "feedback_preview": turn_objective[:240],
                    },
                )
                self._log("[kageha] revising plan.md (still awaiting Build)")
                try:
                    en = workspace.root / "explore_notes.md"
                    if en.is_file():
                        explore_notes = en.read_text(encoding="utf-8")
                except OSError:
                    pass
            # Explore-first: read-only tools before plan.md / Build.
            if (
                requires_plan_approval(resolved_agent_mode)
                and not plan_already_approved(workspace.root)
                and not revising
            ):
                from kageha.loop.design_explore import explore_before_plan

                ctx.meta["plan_approved"] = False
                ctx.meta["design_phase"] = True
                _design_parallel = max(1, min(2, max_tool_parallel()))

                async def _design_exec(calls: list) -> list[ChatMessage]:
                    return await execute_tool_calls(
                        ctx.tools,
                        calls,
                        max_parallel=_design_parallel,
                        events=events,
                        approvals=gate,
                        journal=self.runtime_journal,
                        security_policy=self.security_policy,
                        cancel_event=self.cancel_event,
                        hooks=self.hooks,
                        design_readonly=True,
                    )

                explore_status: dict[str, Any] = {
                    "status": "ok",
                    "message": "",
                }
                try:
                    explore_notes = await explore_before_plan(
                        task=turn_objective,
                        router=router,
                        tool_specs=list(ctx.tools.specs()),
                        execute_tools=_design_exec,
                        events=events,
                        log=self._log,
                        agent_mode=resolved_agent_mode,
                        role=self.planning_role,
                    )
                    if explore_notes:
                        workspace.write_text(
                            "explore_notes.md",
                            "# Explore notes\n\n" + explore_notes + "\n",
                        )
                        explore_status = {
                            "status": "ok",
                            "message": "Explore complete",
                            "chars": len(explore_notes),
                        }
                    else:
                        explore_status = {
                            "status": "empty",
                            "message": "Explore finished without notes",
                        }
                except Exception as exc:  # noqa: BLE001
                    skip_msg = f"Explore skipped: {exc}"
                    explore_status = {
                        "status": "skipped",
                        "message": skip_msg[:400],
                        "error": str(exc)[:400],
                        "degraded": True,
                    }
                    events.emit(
                        "design_explore_error",
                        {
                            "error": str(exc)[:400],
                            "message": skip_msg[:400],
                            "degraded": True,
                        },
                    )
                    self._log(f"[kageha] {skip_msg[:240]}")
                # Persist for Design panel degraded banner (plan still continues).
                try:
                    # Prefer last failover notice if explore recovered mid-run.
                    for evt in reversed(getattr(events, "events", []) or []):
                        if (
                            isinstance(evt, dict)
                            and evt.get("kind") == "design_explore_failover"
                        ):
                            data = evt.get("data") or {}
                            if explore_status.get("status") != "skipped":
                                explore_status = {
                                    "status": "failover",
                                    "message": str(
                                        data.get("message")
                                        or explore_status.get("message")
                                        or ""
                                    ),
                                    "from": data.get("from"),
                                    "to": data.get("to"),
                                    "degraded": False,
                                }
                            break
                    workspace.write_text(
                        "explore_status.json",
                        json.dumps(explore_status, indent=2),
                    )
                except Exception:  # noqa: BLE001
                    pass

            plan = await make_plan(
                turn_objective,
                router,
                role=self.planning_role,
                available_tools={spec.name for spec in ctx.tools.specs()},
                effort=effort,
                explore_notes=explore_notes,
            )
            goal = GoalCard.from_task(turn_objective, plan.milestones or None)
            workspace.write_text("goal_card.json", json.dumps({
                "task": goal.task,
                "items": [
                    {
                        "id": i.id,
                        "description": i.description,
                        "passes": i.passes,
                        "evidence": i.evidence,
                    }
                    for i in goal.items
                ],
            }, indent=2))
            workspace.write_text(
                "todo.md",
                "# Plan\n\n"
                + "\n".join(f"- [ ] {s.id}: {s.description}" for s in plan.steps)
                + "\n\n"
                + goal.to_markdown(),
            )
            workspace.write_text("plan.json", json.dumps({
                "summary": plan.summary,
                "source": plan.source,
                "steps": [
                    {"id": s.id, "description": s.description, "tools": s.tools}
                    for s in plan.steps
                ],
            }, indent=2))
        events.emit(
            "plan",
            {
                "source": plan.source,
                "steps": len(plan.steps),
                "agent_mode": resolved_agent_mode,
            },
        )

        # Plan mode materializes a visible artifact, then hard-gates Build.
        # auto_approve (tool HITL) must NOT skip this — Plan ≠ Normal.
        plan_md_text = ""
        design_artifacts: list[str] = []
        if mode == "full" and requires_plan_approval(resolved_agent_mode):
            # Never publish a one-step followup stub as the Build plan.md.
            if getattr(plan, "source", "") == "followup":
                self._log(
                    "[kageha] rejecting followup stub for Plan design — re-planning"
                )
                plan = await make_plan(
                    turn_objective,
                    router,
                    role=self.planning_role,
                    available_tools={spec.name for spec in ctx.tools.specs()},
                    effort=effort,
                    explore_notes=explore_notes,
                )
            plan_md_text = write_plan_artifact(
                workspace.root,
                resolved_agent_mode,
                summary=plan.summary,
                steps=plan.steps,
                task=turn_objective,
                tldr=plan.summary,
                explore_notes=explore_notes,
            )
            design_artifacts = ["plan.md"]
            if (workspace.root / "explore_notes.md").is_file():
                design_artifacts.append("explore_notes.md")
            explore_banner = ""
            explore_degraded = False
            try:
                status_path = workspace.root / "explore_status.json"
                if status_path.is_file():
                    status = json.loads(status_path.read_text(encoding="utf-8"))
                    if isinstance(status, dict):
                        explore_banner = str(status.get("message") or "")
                        explore_degraded = bool(
                            status.get("degraded")
                            or status.get("status") == "skipped"
                        )
            except Exception:  # noqa: BLE001
                pass
            design_phases = ["explore", "plan", "build"]
            events.emit(
                "design_artifacts",
                {
                    "agent_mode": resolved_agent_mode,
                    "artifacts": design_artifacts,
                    "phases": design_phases,
                    "explore_status": explore_banner,
                    "explore_degraded": explore_degraded,
                    "message": explore_banner if explore_degraded else "",
                },
            )
            self._log(
                f"[kageha] {resolved_agent_mode} artifacts: "
                + ", ".join(design_artifacts)
            )

        if (
            mode == "full"
            and requires_plan_approval(resolved_agent_mode)
            and not plan_already_approved(workspace.root)
        ):
            approved = False
            if self.auto_build:
                approved = True
                self._log("[kageha] auto_build — approving plan without HITL")
            else:
                from kageha.harness.approvals import ApprovalRequest

                for suggest_round in range(4):
                    events.emit(
                        "plan_approval_required",
                        {
                            "agent_mode": resolved_agent_mode,
                            "artifacts": design_artifacts,
                        },
                    )
                    self._log("[kageha] waiting for plan approval (Build)…")
                    outcome = await ctx.approvals.require_explicit(
                        ApprovalRequest(
                            action="approve_plan",
                            detail=(plan_md_text or plan.summary)[:4000],
                            risk_class="plan",
                        )
                    )
                    if outcome.approved:
                        approved = True
                        break
                    feedback = (outcome.feedback or "").strip()
                    if not feedback or suggest_round >= 3:
                        break
                    events.emit(
                        "plan_suggest",
                        {
                            "round": suggest_round + 1,
                            "feedback_preview": feedback[:240],
                        },
                    )
                    self._log(
                        f"[kageha] plan Suggest — revising (round {suggest_round + 1})"
                    )
                    plan = await make_plan(
                        f"{turn_objective}\n\nUser suggestion for the plan: {feedback}",
                        router,
                        role=self.planning_role,
                        available_tools={spec.name for spec in ctx.tools.specs()},
                        effort=effort,
                        explore_notes=explore_notes,
                    )
                    plan_md_text = write_plan_artifact(
                        workspace.root,
                        resolved_agent_mode,
                        summary=plan.summary,
                        steps=plan.steps,
                        task=turn_objective,
                        tldr=plan.summary,
                        explore_notes=explore_notes,
                    )

            if not approved:
                clear_plan_approved(workspace.root)
                events.emit(
                    "plan_rejected",
                    {"agent_mode": resolved_agent_mode},
                )
                preview = (plan_md_text or "").strip()
                if len(preview) > 3500:
                    preview = preview[:3500] + "\n…"
                message = (
                    "Plan ready — awaiting Build/Approve. "
                    "No project mutations were executed.\n\n"
                    f"{preview}\n\n"
                    "Edit plan.md, reply with changes, Approve in the UI, "
                    f"or `/build` (session `{workspace.run_id}`)."
                )
                return RunResult(
                    run_id=workspace.run_id,
                    status="awaiting_plan_approval",
                    message=message,
                    goal=goal,
                    steps=0,
                    spent_usd=0.0,
                    artifacts=design_artifacts,
                    turn_id=turn_id,
                )
            # Prefer disk plan.md (WebUI edits) over stale in-memory planner output.
            plan = self._reload_plan_from_disk(workspace, plan, goal)
            # Only after an actual Build/Approve (or auto_build) — never while awaiting.
            mark_plan_approved(workspace.root)
            events.emit("plan_approved", {"agent_mode": resolved_agent_mode})
            self._log("[kageha] plan approved — starting execution")
            ctx.meta["plan_approved"] = True
        elif mode == "full" and requires_plan_approval(resolved_agent_mode):
            ctx.meta["plan_approved"] = True
            plan = self._reload_plan_from_disk(workspace, plan, goal)

        step_cap = self.max_steps_limit if self.max_steps_limit is not None else max_steps()
        step_cap = max(1, int(step_cap))
        # Plan length is a checklist, NOT the loop budget. Budget is a hard ceiling.
        self._log(
            f"[kageha] plan ready ({plan.source}, {len(plan.steps)} plan items) — "
            f"loop_budget={step_cap} steps (ceiling; stops earlier on success) "
            f"max_usd={max_usd()}"
        )
        self._log(
            "[kageha] plan: "
            + " → ".join(step.description for step in plan.steps[:8])
        )
        self._todo_board_fp = None
        self._log_todo_board(workspace, label="todos", events=events)

        # Persistent executive state (survives transcript compaction / resume)
        state_path = workspace.path("task_state.json")
        resumed_state = False
        clean_objective = (
            unwrap_objective(goal.task, fallback=task)
            if not is_resume_wrapper(task)
            else unwrap_objective(task, fallback=goal.task)
        )
        if is_resume_wrapper(task):
            # Follow-up resume prompt must not become the objective.
            clean_objective = unwrap_objective(
                goal.task or task, fallback=clean_objective
            )
        if state_path.is_file():
            try:
                task_state = TaskState.load(state_path)
                resumed_state = bool(task_state.stages)
                if is_resume_wrapper(task_state.objective) or len(task_state.objective) > 4000:
                    task_state.objective = clean_objective or unwrap_objective(
                        task_state.objective
                    )
                elif not task_state.objective:
                    task_state.objective = clean_objective
            except Exception:  # noqa: BLE001
                task_state = TaskState(objective=clean_objective or task[:2000])
        else:
            task_state = TaskState(objective=clean_objective or task[:2000])
        if fresh_turn or not resumed_state:
            task_state.begin_turn(
                turn_id=turn_id,
                objective=turn_objective,
                goals=[
                    {
                        "id": item.id,
                        "description": item.description,
                        "passes": item.passes,
                        "evidence": item.evidence,
                    }
                    for item in goal.items
                ],
                plan_steps=plan.steps,
                max_steps=step_cap,
                max_usd=float(max_usd()),
            )
            if fresh_turn:
                task_state.add_fact(
                    f"Current turn request: {turn_objective[:400]}",
                    source="user",
                    certainty="verified",
                )
            resumed_state = False
        # Merge TaskState goal progress into GoalCard (and vice versa)
        if not fresh_turn and resumed_state and task_state.goals:
            for g in task_state.goals:
                if g.get("passes"):
                    goal.mark(
                        str(g["id"]),
                        passes=True,
                        evidence=str(g.get("evidence") or ""),
                    )
        # Keep GoalCard.task aligned with clean objective (not the follow-up wrapper)
        if task_state.objective and (
            is_resume_wrapper(goal.task) or len(goal.task or "") > 4000
        ):
            goal.task = task_state.objective
            goal.save(goal_path)
        task_state.sync_goals_from_card(goal)
        task_state.budget.max_steps = step_cap
        task_state.budget.max_usd = float(max_usd())
        task_state.save(state_path)
        events.emit("task_state", {"stages": len(task_state.stages), "goals": len(task_state.goals)})

        # Bring prior chat turns into context — without this, every fresh_turn
        # starts amnesiac even though chat.jsonl exists on disk.
        chat_extra = ""
        prior_msgs: list[ChatMessage] = []
        try:
            from kageha.chat.history import (
                prior_history_messages,
                session_continuity_extra,
            )

            chat_user = turn_objective if fresh_turn else ""
            chat_extra = session_continuity_extra(
                workspace, current_user=chat_user or turn_objective
            )
            prior_msgs = prior_history_messages(
                workspace, current_user=chat_user or turn_objective, limit=16
            )
            if chat_extra:
                system_extra = (system_extra or "") + "\n\n" + chat_extra
                events.emit(
                    "chat_continuity",
                    {"chars": len(chat_extra), "prior_messages": len(prior_msgs)},
                )
                self._log(
                    f"[kageha] session memory: {len(prior_msgs)} prior chat msgs"
                )
        except Exception as e:  # noqa: BLE001
            events.emit("chat_continuity_error", {"error": str(e)})

        assembler = ContextAssembler(
            skill_catalog=self.skill_catalog,
            kb_pins=self.kb_pins,
            system_extra=system_extra
            + f"\n\nPlan summary: {plan.summary}\nPlan source: {plan.source}",
            working_notes=task_state.projection() + "\n" + goal.to_markdown(),
        )

        history: list[ChatMessage] = list(prior_msgs)
        # Prior continuity messages are context only — never the durable answer.
        turn_history_start = len(history)
        chat_first = mode == "followup" or resolved_agent_mode == "normal"
        if chat_first:
            task_guidance = (
                "Answer in the chat. Do not create .md (or other) deliverable files "
                "unless the user explicitly asked to save, write, export, or produce "
                "a document/brief/report. Skip todo.md for simple Q&A. "
                "Stop when the question is answered."
            )
        else:
            task_guidance = (
                "Update todo.md and write deliverables as files when the task asks "
                "for them. Do not claim done until the verifier marks goals pass "
                "with evidence."
            )
        deliverable_bind = (
            "DEFAULT: create every user-facing file under `artifacts/` "
            f"(session: {workspace.root}/artifacts/). "
            "Examples: `artifacts/deck.pptx`, `artifacts/brief.pdf`, "
            "`artifacts/index.html`. "
            "Do not write final deliverables to the project root or cwd. "
            "Use the project root only for source/code helpers. "
            "In bash, prefer `$KAGEHA_ARTIFACTS/...` or `artifacts/...`."
        )
        history.append(
            ChatMessage(
                role="user",
                content=(
                    f"Task: {task}\n\n"
                    f"Work in session workspace: {workspace.root}\n"
                    f"{deliverable_bind}\n"
                    "Use the recent conversation above and TaskState in working notes. "
                    "Do not restart from scratch or invent that prior turns never happened. "
                    f"{task_guidance}"
                ),
            )
        )

        stop_rules = StopRules(max_steps=step_cap, max_usd=max_usd())
        stagnant = 0
        last_progress = goal.progress()
        final = StopDecision(StopReason.SUCCESS, "init")
        tool_parallel = max_tool_parallel()
        ask_user = False
        same_repair_streak = 0
        total_repair_cycles = 0
        last_repair_sig = ""
        last_repair_progress = -1.0
        no_progress_continues = 0
        tool_guard = ToolCallGuardrailController(
            ToolCallGuardrailConfig.from_env(self.platform)
        )
        tool_guard.reset_for_turn()
        post_ckpt_guard = PostCheckpointGuard(
            enabled=post_checkpoint_guard_enabled()
        )
        guardrail_halt = False

        for step in range(1, stop_rules.max_steps + 1):
            ctx.step = step
            task_state.budget.steps_used = step
            task_state.budget.usd_spent = ctx.spent_usd
            if self.cancel_event.is_set():
                final = StopDecision(StopReason.CANCELLED, "Cancelled")
                break

            # Mid-run user injections — steer without discarding completed work
            while self.pending_user_messages:
                msg = self.pending_user_messages.pop(0)
                history.append(ChatMessage(role="user", content=f"[user steering] {msg}"))
                task_state.constraints.append(f"user: {msg[:300]}")
                if len(task_state.constraints) > 24:
                    task_state.constraints = task_state.constraints[-24:]
                task_state.add_fact(f"User correction: {msg[:400]}", source="user", certainty="verified")
                events.emit("user_inject", {"message": msg})
                self._log(f"[kageha] user inject: {msg[:120]}")

            assembler.working_notes = (
                task_state.projection()
                + "\n"
                + goal.to_markdown()
            )
            tool_specs = _filter_tools_for_skills(ctx)
            assembled = assembler.build(history=history, tools=tool_specs)
            events.emit("context", assembled.stats)
            self._log(f"[kageha] step {step}/{stop_rules.max_steps} — thinking…")
            computer_early_stopped = False
            browser_early_stopped = False

            import time as _time

            llm_t0 = _time.perf_counter()
            try:
                model, resp = await router.chat(
                    assembled.messages,
                    tool_specs,
                    role=self.execution_role,
                    task_id=workspace.run_id,
                    max_tokens=8192,
                    effort=effort,
                )
            except Exception as e:  # noqa: BLE001
                events.emit("error", {"error": str(e)})
                self._log(f"[kageha] model error: {e}")
                task_state.record_tool(
                    step=step,
                    tool="model",
                    content=f"ERROR: provider {e}",
                )
                task_state.save(state_path)
                final = StopDecision(StopReason.ERROR, str(e))
                break
            llm_ms = (_time.perf_counter() - llm_t0) * 1000.0

            for notice in router.drain_failover_notices():
                line = ModelRouter.format_failover_line(notice)
                events.emit("model_failover", notice)
                self._log(f"[kageha] {line}")

            # Budget accounting (per-model rates from models.yaml; cache reads credited)
            prompt_tok = resp.usage.prompt_tokens
            cached_tok = resp.usage.cached_tokens
            usd = _estimate_usd(
                prompt_tok,
                resp.usage.completion_tokens,
                model_id=model.model_id,
                cached_tokens=cached_tok,
            )
            ctx.spent_usd += usd
            task_state.budget.usd_spent = ctx.spent_usd
            cache_hit_rate = (cached_tok / prompt_tok) if prompt_tok > 0 else 0.0
            events.emit(
                "model",
                {
                    "model": model.model_id,
                    "provider": model.provider,
                    "prompt_tokens": prompt_tok,
                    "completion_tokens": resp.usage.completion_tokens,
                    "cached_tokens": cached_tok,
                    "cache_hit_rate": round(cache_hit_rate, 4),
                    "usd": usd,
                    "llm_ms": round(llm_ms, 1),
                },
            )
            cache_note = ""
            if cached_tok:
                cache_note = f" cache_read={cached_tok}/{prompt_tok} ({cache_hit_rate:.0%})"
            self._log(
                f"[kageha]   model={model.model_id} "
                f"tokens={prompt_tok}+{resp.usage.completion_tokens}{cache_note} "
                f"usd~{ctx.spent_usd:.4f}"
            )

            assistant = resp.message
            history.append(assistant)

            reasoning = (getattr(assistant, "reasoning", None) or "").strip()
            if not reasoning and assistant.content and assistant.tool_calls:
                # Narration before tools — useful when providers don't expose thoughts.
                reasoning = (assistant.content or "").strip()
            if reasoning:
                one_line = " ".join(reasoning.split())
                self._log(f"[kageha]   reasoning: {one_line[:480]}")

            if (resp.stop_reason or "") in {"length", "max_tokens"}:
                self._log("[kageha]   warn: model hit max_tokens (tool args may be truncated)")
                history.append(
                    ChatMessage(
                        role="user",
                        content=(
                            "[system] Your previous response hit max_tokens. "
                            "If a tool call failed with missing arguments, retry with a "
                            "SMALLER write (outline first, then append sections). "
                            "Prefer reading inputs/* seeded files over absolute paths."
                        ),
                    )
                )

            if assistant.tool_calls:
                names = ", ".join(tc.name for tc in assistant.tool_calls)
                self._log(f"[kageha]   tools: {names} (parallel≤{tool_parallel})")
                self._log(
                    f"[kageha]   action: "
                    f"{_sanitized_tool_action(assistant.tool_calls)}"
                )
                allowed_calls = []
                blocked_by_id: dict[str, ChatMessage] = {}
                # Hard gate: Plan must not mutate sources until Build.
                # Primary stop is awaiting_plan_approval before the act loop;
                # this blocks tools if that gate is ever bypassed.
                from kageha.loop.mode_policy import (
                    mutations_blocked_until_approve,
                    tool_blocked_in_plan_design,
                )

                design_locked = mutations_blocked_until_approve(
                    resolved_agent_mode
                ) and not bool(ctx.meta.get("plan_approved"))
                for tc in assistant.tool_calls:
                    if design_locked and tool_blocked_in_plan_design(
                        tc.name, approved=False
                    ):
                        blocked = ChatMessage(
                            role="tool",
                            name=tc.name,
                            tool_call_id=tc.id,
                            content=(
                                "DENIED: Plan design is read-only until "
                                "Build/Approve. Mutating tools are blocked."
                            ),
                        )
                        blocked_by_id[tc.id] = blocked
                        events.emit(
                            "plan_design_blocked",
                            {
                                "tool": tc.name,
                                "agent_mode": resolved_agent_mode,
                            },
                        )
                        self._log(
                            f"[kageha]   plan_design BLOCK {tc.name} "
                            "(awaiting Build)"
                        )
                        continue
                    before = tool_guard.before_call(tc.name, tc.arguments or {})
                    if before.should_halt:
                        blocked = ChatMessage(
                            role="tool",
                            name=tc.name,
                            tool_call_id=tc.id,
                            content=synthetic_block_result(before),
                        )
                        blocked_by_id[tc.id] = blocked
                        guardrail_halt = True
                        events.emit(
                            "tool_guardrail",
                            {
                                **before.to_metadata(),
                                "phase": "before_call",
                            },
                        )
                        self._log(
                            f"[kageha]   tool_guardrail BLOCK {tc.name}: "
                            f"{before.code}"
                        )
                    else:
                        allowed_calls.append(tc)

                executed: list[ChatMessage] = []
                if allowed_calls:
                    design_readonly = (
                        mutations_blocked_until_approve(resolved_agent_mode)
                        and not bool(ctx.meta.get("plan_approved"))
                    )
                    executed = await execute_tool_calls(
                        ctx.tools,
                        allowed_calls,
                        max_parallel=tool_parallel,
                        events=events,
                        approvals=gate,
                        journal=self.runtime_journal,
                        security_policy=self.security_policy,
                        cancel_event=self.cancel_event,
                        hooks=self.hooks,
                        design_readonly=design_readonly,
                    )

                results: list[ChatMessage] = []
                executed_by_id = {
                    (r.tool_call_id or ""): r for r in executed if r.tool_call_id
                }
                for tc in assistant.tool_calls:
                    if tc.id in blocked_by_id:
                        r = blocked_by_id[tc.id]
                    else:
                        r = executed_by_id.get(tc.id)
                        if r is None:
                            # Fallback if provider omitted tool_call_id match.
                            for cand in executed:
                                if cand.name == tc.name and cand not in results:
                                    r = cand
                                    break
                        if r is None:
                            continue
                        after = tool_guard.after_call(
                            tc.name,
                            tc.arguments or {},
                            r.content or "",
                        )
                        if after.action == "warn":
                            r = ChatMessage(
                                role=r.role,
                                content=append_guidance(r.content or "", after),
                                tool_call_id=r.tool_call_id,
                                name=r.name,
                            )
                            events.emit(
                                "tool_guardrail",
                                {**after.to_metadata(), "phase": "after_call"},
                            )
                            self._log(
                                f"[kageha]   tool_guardrail WARN {tc.name}: "
                                f"{after.code}"
                            )
                        elif after.should_halt:
                            r = ChatMessage(
                                role=r.role,
                                content=append_guidance(r.content or "", after),
                                tool_call_id=r.tool_call_id,
                                name=r.name,
                            )
                            guardrail_halt = True
                            events.emit(
                                "tool_guardrail",
                                {**after.to_metadata(), "phase": "after_call"},
                            )
                            self._log(
                                f"[kageha]   tool_guardrail HALT {tc.name}: "
                                f"{after.code}"
                            )
                        if post_ckpt_guard.armed:
                            verdict = post_ckpt_guard.observe(
                                tc.name,
                                tc.arguments or {},
                                r.content or "",
                            )
                            if verdict.should_abort:
                                guardrail_halt = True
                                events.emit(
                                    "tool_guardrail",
                                    {
                                        "action": "halt",
                                        "code": verdict.detector,
                                        "message": verdict.message,
                                        "tool_name": verdict.tool_name,
                                        "count": verdict.count,
                                        "phase": "post_checkpoint",
                                    },
                                )
                                self._log(
                                    f"[kageha]   post_checkpoint guard: "
                                    f"{verdict.detector}"
                                )
                                r = ChatMessage(
                                    role=r.role,
                                    content=(r.content or "")
                                    + f"\n\n[Tool loop hard stop: "
                                    f"{verdict.detector}; {verdict.message}]",
                                    tool_call_id=r.tool_call_id,
                                    name=r.name,
                                )
                    results.append(r)

                todo_touched = False
                for r in results:
                    preview = (r.content or "").replace("\n", " ")[:140]
                    self._log(f"[kageha]   ← {r.name}: {preview}")
                    if self._tool_result_touches_todo(r):
                        todo_touched = True
                    task_state.record_tool(
                        step=step,
                        tool=r.name or "tool",
                        content=r.content or "",
                    )
                # Always re-parse when todo tools/files were touched; also pick up
                # indirect edits (shell, skills) by comparing the board fingerprint.
                if todo_touched or results:
                    self._refresh_todo_board_if_changed(
                        workspace, label="todos", events=events
                    )
                history.extend(results)
                # Closest Hermes: nudge observe/refine after tool pitfalls
                try:
                    from kageha.memory.skill_learn import (
                        collect_tool_pitfalls,
                        learning_nudge,
                        skill_learn_nudges_enabled,
                    )

                    if (
                        skill_learn_nudges_enabled()
                        and not ctx.meta.get("skill_learn_nudged")
                        and ctx.meta.get("active_skills")
                    ):
                        pitfalls = collect_tool_pitfalls(results)
                        if pitfalls:
                            nudge = learning_nudge(
                                list(ctx.meta.get("active_skills") or []),
                                pitfalls=pitfalls,
                            )
                            if nudge:
                                history.append(
                                    ChatMessage(role="user", content=nudge)
                                )
                                ctx.meta["skill_learn_nudged"] = True
                                events.emit(
                                    "skill_learn_nudge",
                                    {
                                        "skills": list(
                                            ctx.meta.get("active_skills") or []
                                        ),
                                        "pitfalls": pitfalls,
                                    },
                                )
                                self._log(
                                    "[kageha] skill learn nudge → observe/refine"
                                )
                except Exception as e:  # noqa: BLE001
                    events.emit("skill_learn_nudge_error", {"error": str(e)})
                # Hermes-style SWITCH_TOOL / RETRY steer from guardrail warnings.
                steer = tool_guard.consume_steer()
                if steer and not guardrail_halt:
                    if steer == "switch_tool":
                        apply_decision(
                            task_state,
                            ControlDecision.SWITCH_TOOL,
                            "tool_guardrail:switch_tool",
                        )
                        history.append(
                            ChatMessage(
                                role="user",
                                content=switch_tool_steering_message(
                                    task_state,
                                    detail="tool loop guardrail requested SWITCH_TOOL",
                                ),
                            )
                        )
                    elif steer == "retry":
                        apply_decision(
                            task_state,
                            ControlDecision.RETRY,
                            "tool_guardrail:retry",
                        )
                        history.append(
                            ChatMessage(
                                role="user",
                                content=retry_steering_message(
                                    task_state,
                                    detail="tool loop guardrail requested RETRY",
                                ),
                            )
                        )
                    events.emit(
                        "control",
                        {"decision": steer, "reason": "tool_guardrail_steer"},
                    )
                    self._log(f"[kageha]   control steer={steer}")
                task_state.sync_artifacts(
                    workspace.list_files(),
                    current_paths=_changed_workspace_paths(
                        workspace.root, turn_snapshot
                    ),
                )
                pending = _pending_question_from_results(results)
                if pending is not None:
                    question, yes_label, no_label = pending
                    task_state.pending_question = question
                    task_state.pending_yes_label = yes_label
                    task_state.pending_no_label = no_label
                    task_state.pending_request = turn_objective
                    final = StopDecision(
                        StopReason.ASK_USER,
                        _format_pending_question(
                            question,
                            yes_label,
                            no_label,
                        ),
                    )
                    events.emit(
                        "user_input_requested",
                        {
                            "turn_id": turn_id,
                            "question": question,
                            "yes_label": yes_label,
                            "no_label": no_label,
                        },
                    )
                    task_state.save(state_path)
                    break
                if guardrail_halt:
                    halt = tool_guard.halt_decision
                    msg = (
                        (halt.message if halt else "")
                        or "Tool loop detected — need a different approach"
                    )
                    final = StopDecision(StopReason.ASK_USER, msg)
                    events.emit(
                        "tool_guardrail",
                        {
                            "action": "halt",
                            "code": (halt.code if halt else "guardrail_halt"),
                            "message": msg,
                            "phase": "turn_exit",
                        },
                    )
                    task_state.save(state_path)
                    break
                model_said_done = False
                computer_early_stopped = False
                browser_early_stopped = False
                # OSWorld-Human: skip an extra LLM turn after a successful
                # grouped computer action (readings already prove the result).
                if wants_computer and not guardrail_halt:
                    try:
                        from kageha.harness.tools.computer_early_stop import (
                            select_computer_early_stop,
                        )

                        early = select_computer_early_stop(
                            [(r.name or "", r.content or "") for r in results]
                        )
                    except Exception:  # noqa: BLE001
                        early = None
                    if early is not None:
                        history.append(
                            ChatMessage(role="assistant", content=early.answer)
                        )
                        for item in goal.items:
                            if not item.passes:
                                goal.mark(
                                    item.id,
                                    passes=True,
                                    evidence=early.evidence,
                                )
                        goal.save(workspace.path("goal_card.json"))
                        task_state.sync_goals_from_card(goal)
                        model_said_done = True
                        computer_early_stopped = True
                        events.emit(
                            "computer_early_stop",
                            {
                                "tool": early.tool,
                                "mode": early.mode,
                                "evidence": early.evidence,
                                "answer": early.answer[:200],
                                "research": "osworld-human-grouped-action",
                            },
                        )
                        self._log(
                            "[kageha]   computer_early_stop — "
                            f"{early.mode} readings (skip extra LLM turn)"
                        )
                # Browse/screenshot goals: stop once an image deliverable exists
                # (avoids max_steps thrash via bash/list_dir after success).
                if (
                    not model_said_done
                    and not guardrail_halt
                    and results
                ):
                    try:
                        from kageha.harness.tools.browser_early_stop import (
                            select_browser_early_stop,
                        )

                        browser_early = select_browser_early_stop(
                            [(r.name or "", r.content or "") for r in results],
                            objective=getattr(goal, "task", None) or turn_objective,
                        )
                    except Exception:  # noqa: BLE001
                        browser_early = None
                    if browser_early is not None:
                        history.append(
                            ChatMessage(
                                role="assistant", content=browser_early.answer
                            )
                        )
                        for item in goal.items:
                            if not item.passes:
                                goal.mark(
                                    item.id,
                                    passes=True,
                                    evidence=browser_early.evidence,
                                )
                        goal.save(workspace.path("goal_card.json"))
                        task_state.sync_goals_from_card(goal)
                        model_said_done = True
                        browser_early_stopped = True
                        events.emit(
                            "browser_early_stop",
                            {
                                "tool": browser_early.tool,
                                "path": browser_early.path,
                                "evidence": browser_early.evidence,
                                "answer": browser_early.answer[:200],
                            },
                        )
                        self._log(
                            "[kageha]   browser_early_stop — "
                            f"{browser_early.tool} → {browser_early.path}"
                        )
            else:
                computer_early_stopped = False
                browser_early_stopped = False
                # Sanitized tool-history stubs are not real answers — keep looping.
                if not _is_user_facing_reply(assistant.content or ""):
                    model_said_done = False
                    if (assistant.content or "").strip():
                        self._log(
                            "[kageha]   ignore non-answer reply: "
                            f"{(assistant.content or '')[:120]}"
                        )
                        history.append(
                            ChatMessage(
                                role="user",
                                content=(
                                    "Continue the task. Call the next required tool. "
                                    "Do not echo internal tool-history markers."
                                ),
                            )
                        )
                else:
                    model_said_done = True
                    if assistant.content:
                        self._log(
                            f"[kageha]   reply: {(assistant.content or '')[:160]}"
                        )

            # Periodic verify + adaptive control + stage-gate monitor + checkpoint.
            # Follow-up / low-effort turns only gate when the model claims done —
            # otherwise short remotes burn tokens on empty verify+checkpoint loops.
            if mode == "followup" or effort == "low":
                do_gate = model_said_done
            else:
                do_gate = step % monitor_every() == 0 or model_said_done
            stage_complete = False
            force_escalate = False
            ask_user = False
            if do_gate:
                files = build_workspace_evidence(
                    workspace.root,
                    include_paths=_changed_workspace_paths(
                        workspace.root, turn_snapshot
                    ),
                )
                tail = "\n".join(
                    f"{m.role}: {(m.content or '')[:200]}" for m in history[-8:]
                )
                turn_results = task_state.tool_results[
                    task_state.turn_tool_result_start :
                ]
                tool_fail = any(not r.ok for r in turn_results)
                turn_paths = _changed_workspace_paths(workspace.root, turn_snapshot)
                from kageha.loop.artifacts import classify_artifacts

                turn_deliverables = classify_artifacts(list(turn_paths))
                successful_tools = [
                    r.tool
                    for r in turn_results
                    if r.ok
                    and r.tool not in {"todo_write", "todo_read", "read_file", "ask_human"}
                ]
                answer_text = ""
                if model_said_done:
                    answer_text = _latest_turn_assistant_text(
                        history, turn_start=turn_history_start
                    )
                # act: skip verifier LLM unless a tool failed.
                # Never false-succeed action turns that claimed done with no tools
                # and no new deliverables (Antigravity/text-only regressions).
                # Computer grouped-action early-stop also skips verifier LLM
                # (OSWorld-Human: plan/reflect dominate latency).
                if (
                    mode == "followup"
                    or computer_early_stopped
                    or browser_early_stopped
                ) and not tool_fail:
                    from kageha.loop.verifier import is_lookup_status_goal

                    informational = is_lookup_status_goal(goal)
                    computer_evidence = False
                    if wants_computer or computer_early_stopped:
                        from kageha.harness.tools.computer_early_stop import (
                            has_verified_computer_evidence,
                        )

                        computer_evidence = bool(computer_early_stopped) or (
                            has_verified_computer_evidence(
                                [
                                    (r.tool, r.summary)
                                    for r in turn_results
                                    if r.ok
                                ]
                            )
                        )
                    if wants_computer and not informational:
                        # Never treat write_file / todo as proof of desktop interaction.
                        has_evidence = computer_evidence
                    else:
                        has_evidence = bool(
                            successful_tools
                            or turn_deliverables
                            or computer_early_stopped
                            or browser_early_stopped
                        )
                    # Normal/followup chat: a user-facing answer with no tools is
                    # success (Q&A / check-ins). Requiring tools caused TypeError
                    # (Defect missing artifact) and multi-step repair thrash.
                    chat_answer_ok = bool(
                        chat_first
                        and not wants_computer
                        and answer_text
                        and _is_user_facing_reply(answer_text)
                    )
                    if has_evidence or informational or chat_answer_ok:
                        evidence = (
                            (
                                "computer_early_stop"
                                if computer_early_stopped
                                else ""
                            )
                            or (
                                "browser_early_stop"
                                if browser_early_stopped
                                else ""
                            )
                            or ", ".join(
                                t
                                for t in successful_tools[:4]
                                if str(t).startswith("computer_")
                            )
                            or ", ".join(successful_tools[:4])
                            or ", ".join(turn_deliverables[:4])
                            or ("chat_answer" if chat_answer_ok else "followup_answer")
                        )
                        for item in goal.items:
                            if not item.passes:
                                goal.mark(item.id, passes=True, evidence=evidence)
                        verify = VerifyResult(
                            goal=goal,
                            snapshot=ValidationSnapshot(
                                status="pass",
                                notes=(
                                    "computer grouped-action early-stop (no LLM)"
                                    if computer_early_stopped
                                    else (
                                        "browser screenshot early-stop (no LLM)"
                                        if browser_early_stopped
                                        else "followup deterministic verify (no LLM)"
                                    )
                                ),
                            ),
                        )
                        self._log(
                            "[kageha]   computer early-stop verify — deterministic pass"
                            if computer_early_stopped
                            else (
                                "[kageha]   browser early-stop verify — "
                                "deterministic pass"
                                if browser_early_stopped
                                else "[kageha]   followup verify — deterministic pass"
                            )
                        )
                    else:
                        if wants_computer:
                            defect = Defect(
                                artifact="computer",
                                severity="major",
                                problem=(
                                    "Claimed done without verified computer "
                                    "interaction (write_file alone is not enough)"
                                ),
                                evidence=(answer_text or "")[:240],
                                repair=(
                                    "Use computer_* until an action is verified "
                                    "(not effect=unverifiable). Click the input, "
                                    "retry computer_type, or report AX insert blocked."
                                ),
                            )
                            notes = (
                                "followup rejected: no verified computer evidence"
                            )
                        else:
                            defect = Defect(
                                artifact="deliverable",
                                severity="major",
                                problem=(
                                    "Claimed done without tool use or new deliverables"
                                ),
                                evidence=(answer_text or "")[:240],
                                repair=(
                                    "Call the required tools (e.g. write_file / bash) "
                                    "and only then confirm success."
                                ),
                            )
                            notes = "followup rejected: no side-effect evidence"
                        verify = VerifyResult(
                            goal=goal,
                            snapshot=ValidationSnapshot(
                                status="repair",
                                defects=[defect],
                                next_action="repair",
                                notes=notes,
                            ),
                        )
                        self._log(
                            "[kageha]   followup verify — repair "
                            + (
                                "(no verified computer evidence)"
                                if wants_computer
                                else "(claimed done without tools/deliverables)"
                            )
                        )
                else:
                    verify = await verify_with_defects(
                        goal,
                        router=router,
                        workspace_summary=files,
                        transcript_tail=tail,
                        task_state_projection=task_state.projection(),
                        execution_provider=router.last_provider.get(
                            workspace.run_id,
                            "",
                        ),
                        task_id=f"{workspace.run_id}:verifier",
                        model_said_done=model_said_done,
                        successful_tools=successful_tools,
                        turn_artifacts=turn_deliverables,
                        answer_text=answer_text,
                    )
                    if (
                        verify.snapshot.notes
                        and "deterministic lookup/status verify"
                        in verify.snapshot.notes
                    ):
                        self._log("[kageha]   lookup verify — deterministic pass")
                goal = verify.goal
                # When the LLM verifier returns unknown (common empty/JSON blip)
                # but the agent already checked todo.md and wrote turn
                # deliverables, promote goals so we don't spin until no_progress.
                todo_path = workspace.path("todo.md")
                if todo_path.is_file() and (turn_deliverables or successful_tools):
                    evidence = ", ".join(turn_deliverables[:4]) or "tool_success"
                    if goal.apply_todo_checkboxes(
                        todo_path.read_text(errors="replace"),
                        evidence=evidence,
                    ):
                        events.emit(
                            "goal_todo_sync",
                            {
                                "evidence": evidence,
                                "progress": goal.progress(),
                            },
                        )
                        self._log_checklist(goal.to_markdown(), label="goals")
                goal.save(workspace.path("goal_card.json"))
                task_state.sync_goals_from_card(goal)
                self._sync_todo_board_from_progress(
                    workspace,
                    goal=goal,
                    task_state=task_state,
                    events=events,
                )
                task_state.apply_validation(verify.snapshot)
                if (
                    goal.all_passed()
                    and task_state.validation.status in {"", "unknown"}
                    and (
                        turn_deliverables
                        or (successful_tools and is_lookup_status_goal(goal))
                    )
                ):
                    evidence_note = ", ".join(
                        (turn_deliverables or successful_tools)[:4]
                    )
                    task_state.apply_validation(
                        ValidationSnapshot(
                            status="pass",
                            notes=(
                                "Goals completed with turn evidence "
                                f"({evidence_note})"
                            ),
                        )
                    )
                task_state.sync_artifacts(
                    workspace.list_files(),
                    current_paths=turn_paths,
                )

                decision_ctrl, reason = decide_control(task_state)
                apply_decision(task_state, decision_ctrl, reason)
                events.emit(
                    "control",
                    {
                        "decision": decision_ctrl.value,
                        "reason": reason,
                        "validation": verify.snapshot.status,
                        "defects": len(verify.snapshot.defects),
                    },
                )
                self._log(
                    f"[kageha]   verify={verify.snapshot.status} "
                    f"defects={len(verify.snapshot.defects)} "
                    f"control={decision_ctrl.value}"
                )

                if decision_ctrl == ControlDecision.REPAIR:
                    total_repair_cycles += 1
                    repair_sig = _defect_signature(task_state)
                    progress_now = goal.progress()
                    if (
                        repair_sig == last_repair_sig
                        and progress_now <= last_repair_progress + 1e-9
                    ):
                        same_repair_streak += 1
                    else:
                        same_repair_streak = 1
                        last_repair_sig = repair_sig
                        last_repair_progress = progress_now
                    repair_capped = (
                        same_repair_streak >= stop_rules.max_same_repair
                        or total_repair_cycles >= stop_rules.max_total_repair
                    )
                    if repair_capped:
                        ask_user = True
                        self._log(
                            f"[kageha]   repair cap hit "
                            f"(same={same_repair_streak}/{stop_rules.max_same_repair}, "
                            f"total={total_repair_cycles}/{stop_rules.max_total_repair})"
                        )
                        events.emit(
                            "control",
                            {
                                "decision": ControlDecision.ASK_USER.value,
                                "reason": "repair_cycle_cap",
                                "same_repair_streak": same_repair_streak,
                                "total_repair_cycles": total_repair_cycles,
                            },
                        )
                    else:
                        history.append(
                            ChatMessage(
                                role="user",
                                content=repair_steering_message(task_state),
                            )
                        )
                        model_said_done = False
                elif decision_ctrl == ControlDecision.REPLAN_STAGE:
                    same_repair_streak = 0
                    history.append(
                        ChatMessage(
                            role="user",
                            content=replan_steering_message(task_state, whole_task=False),
                        )
                    )
                    model_said_done = False
                elif decision_ctrl == ControlDecision.REPLAN_TASK:
                    same_repair_streak = 0
                    history.append(
                        ChatMessage(
                            role="user",
                            content=replan_steering_message(task_state, whole_task=True),
                        )
                    )
                    model_said_done = False
                elif decision_ctrl == ControlDecision.ASK_USER:
                    ask_user = True
                elif decision_ctrl == ControlDecision.SWITCH_TOOL:
                    same_repair_streak = 0
                    history.append(
                        ChatMessage(
                            role="user",
                            content=switch_tool_steering_message(
                                task_state, detail=reason
                            ),
                        )
                    )
                    model_said_done = False
                elif decision_ctrl == ControlDecision.HUDDLE:
                    same_repair_streak = 0
                    workspace.write_text(
                        "huddle.md",
                        huddle_steering_message(task_state, detail=reason) + "\n",
                    )
                    history.append(
                        ChatMessage(
                            role="user",
                            content=huddle_steering_message(
                                task_state, detail=reason
                            ),
                        )
                    )
                    model_said_done = False
                    self._log("[kageha]   huddle — diagnose and invent under HITL")
                elif decision_ctrl == ControlDecision.RETRY:
                    history.append(
                        ChatMessage(
                            role="user",
                            content=retry_steering_message(task_state, detail=reason),
                        )
                    )
                    model_said_done = False
                elif decision_ctrl == ControlDecision.ADVANCE:
                    same_repair_streak = 0
                    total_repair_cycles = 0
                    stage_complete = True
                elif decision_ctrl == ControlDecision.STOP_SUCCESS:
                    same_repair_streak = 0
                    total_repair_cycles = 0
                    # Let stop_rules confirm calibrated success
                    pass

                # Followup/act skips plan-alignment monitor (no separate monitor turn).
                if monitor_enabled() and mode != "followup":
                    todo_md = ""
                    todo_path = workspace.path("todo.md")
                    if todo_path.is_file():
                        todo_md = todo_path.read_text(errors="replace")
                    verdict = await monitor_plan_alignment(
                        router=router,
                        plan_summary=plan.summary,
                        plan_steps=[
                            f"{s.id}: {s.description}" for s in plan.steps
                        ],
                        goal_md=goal.to_markdown(),
                        todo_md=todo_md,
                        workspace_summary=files,
                        transcript_tail=tail,
                    )
                    events.emit(
                        "monitor",
                        {
                            "on_plan": verdict.on_plan,
                            "stage_complete": verdict.stage_complete,
                            "current_stage": verdict.current_stage,
                            "escalate": verdict.escalate,
                        },
                    )
                    if verdict.on_plan:
                        self._log(
                            "[kageha]   monitor: ON PLAN"
                            + (f" ({verdict.current_stage})" if verdict.current_stage else "")
                        )
                    else:
                        self._log(
                            f"[kageha]   monitor: DRIFT — {(verdict.drift or verdict.redirect)[:120]}"
                        )
                    # Only steer on real drift/escalation — not every on-plan
                    # redirect/stage_complete tick (those re-injected the loop).
                    if verdict.escalate or not verdict.on_plan:
                        history.append(
                            ChatMessage(role="user", content=verdict.steering_message())
                        )
                    stage_complete = stage_complete or verdict.stage_complete
                    force_escalate = verdict.escalate

                task_state.save(state_path)
                assembler.working_notes = (
                    task_state.projection() + "\n" + goal.to_markdown()
                )

            progress = goal.progress()
            progressed = progress > last_progress
            if force_escalate:
                stagnant = stop_rules.no_progress_limit
            elif progress <= last_progress and not assistant.tool_calls:
                stagnant += 1
            else:
                stagnant = 0
                last_progress = progress

            hist_tokens = history_token_estimate(history)
            sparse_loop = mode == "followup" or effort == "low"
            need_ckpt = checkpoint_enabled() and (
                hist_tokens >= checkpoint_history_tokens()
                or (
                    not sparse_loop
                    and (stage_complete or progressed)
                )
            )
            # Avoid checkpoint every single progress tick if history is small
            if need_ckpt and (
                hist_tokens >= checkpoint_history_tokens()
                or (
                    not sparse_loop
                    and (stage_complete or (progressed and do_gate))
                )
            ):
                reason = (
                    "context_window"
                    if hist_tokens >= checkpoint_history_tokens()
                    else (
                        "stage_complete"
                        if stage_complete
                        else "progress"
                    )
                )
                self._log(
                    f"[kageha]   checkpoint ({reason}) hist_tokens~{hist_tokens}…"
                )
                try:
                    self.hooks.run(
                        "preCompact",
                        payload={
                            "reason": reason,
                            "hist_tokens": hist_tokens,
                            "step": step,
                        },
                    )
                except Exception:  # noqa: BLE001
                    pass
                ckpt = await create_checkpoint(
                    workspace=workspace,
                    step=step,
                    history=history,
                    goal=goal,
                    plan_summary=plan.summary,
                    router=router,
                    reason=reason,
                )
                history = ckpt.history
                # After compaction, arm OpenClaw-style short-window loop guard.
                post_ckpt_guard.arm()
                # After compaction, TaskState is the memory — re-project it
                task_state.save(state_path)
                assembler.working_notes = (
                    task_state.projection()
                    + "\n"
                    + goal.to_markdown()
                    + f"\n\n## Latest checkpoint\n{ckpt.path}\n\n{ckpt.summary[:1500]}"
                )
                events.emit(
                    "checkpoint",
                    {
                        "path": ckpt.path,
                        "reason": reason,
                        "tokens_before": ckpt.history_tokens_before,
                        "tokens_after": ckpt.history_tokens_after,
                        "post_checkpoint_guard_armed": post_ckpt_guard.armed,
                    },
                )
                self._log(
                    f"[kageha]   checkpoint saved {ckpt.path} "
                    f"tokens {ckpt.history_tokens_before}→{ckpt.history_tokens_after}"
                    + (
                        " (post-checkpoint guard armed)"
                        if post_ckpt_guard.armed
                        else ""
                    )
                )

            decision = stop_rules.evaluate(
                step=step,
                spent_usd=ctx.spent_usd,
                goal=goal,
                stagnant_steps=stagnant,
                cancelled=self.cancel_event.is_set(),
                model_said_done=model_said_done and not assistant.tool_calls,
                validated=task_state.validated_ok(),
                validation_status=task_state.validation.status,
                ask_user=ask_user,
                same_repair_streak=same_repair_streak,
                total_repair_cycles=total_repair_cycles,
            )
            events.emit(
                "stop_check",
                {
                    "reason": decision.reason.value,
                    "progress": progress,
                    "validation": task_state.validation.status,
                    "control": task_state.control,
                    "same_repair_streak": same_repair_streak,
                    "total_repair_cycles": total_repair_cycles,
                },
            )
            self._log(
                f"[kageha]   progress={progress:.0%} "
                f"stop={decision.reason.value} control={task_state.control}"
            )
            task_state.save(state_path)
            if decision.should_stop:
                if decision.reason == StopReason.NO_PROGRESS:
                    # Auto-approve must not reset forever — one continue max.
                    if no_progress_continues < 1:
                        ok = await _hitl_continue(gate, decision.message)
                        if ok:
                            no_progress_continues += 1
                            stagnant = 0
                            continue
                    final = decision
                    break
                final = decision
                break
        else:
            final = StopDecision(StopReason.MAX_STEPS, "Loop exhausted")
        task_state.save(state_path)

        # Final summary file — current turn only (prior chat continuity is excluded).
        summary = _latest_turn_assistant_text(
            history,
            turn_start=turn_history_start,
            require_no_tool_calls=True,
        )
        from kageha.research.citations import (
            WEB_CITE_TOOLS,
            collect_citations_from_messages,
            ensure_cited_answer,
            strip_sources_marker,
        )

        turn_citations = collect_citations_from_messages(
            history, start=turn_history_start
        )
        used_web = any(
            (getattr(m, "name", None) or "") in WEB_CITE_TOOLS
            or str(getattr(m, "name", "") or "").startswith("browser_")
            for m in history[turn_history_start:]
            if getattr(m, "role", None) == "tool"
        )
        if used_web and turn_citations and summary:
            summary = strip_sources_marker(
                ensure_cited_answer(summary, turn_citations)
            )
        from kageha.loop.artifacts import (
            classify_artifacts,
            format_artifacts_report,
            mirror_deliverables_into_session,
        )

        if project_root_path is not None:
            project_rel_changed = _changed_workspace_paths(
                project_root_path,
                project_turn_snapshot,
                skip_dir_names=_PROJECT_SNAPSHOT_SKIP,
            )
            for tp in ctx.meta.get("touched_paths") or []:
                rel = _path_relative_to_root(tp, project_root_path)
                if rel:
                    project_rel_changed.add(rel)
            if project_rel_changed:
                mirrored = mirror_deliverables_into_session(
                    workspace,
                    source_root=project_root_path,
                    relative_paths=project_rel_changed,
                )
                if mirrored:
                    self._log(
                        "[kageha] mirrored project deliverables: "
                        + ", ".join(mirrored[:6])
                        + ("…" if len(mirrored) > 6 else "")
                    )
                    events.emit("project_artifacts_mirrored", {"paths": mirrored[:40]})

        all_files = workspace.list_files()
        user_artifacts = classify_artifacts(all_files)
        changed_paths = _changed_workspace_paths(workspace.root, turn_snapshot)
        turn_user_artifacts = classify_artifacts(list(changed_paths))
        # Grace final answer: SUCCESS when summary is jargon; hard stops when
        # there is no user-facing assistant text yet (tools-disabled compose).
        ask_has_question = (
            final.reason == StopReason.ASK_USER
            and _is_user_facing_reply(final.message)
            and "?" in (final.message or "")
        )
        need_grace = (
            not ask_has_question
            and (
                (
                    final.reason == StopReason.SUCCESS
                    and not _is_user_facing_reply(summary)
                )
                or (
                    final.reason
                    in {
                        StopReason.MAX_STEPS,
                        StopReason.BUDGET,
                        StopReason.NO_PROGRESS,
                        StopReason.ASK_USER,
                    }
                    and not _is_user_facing_reply(summary)
                )
            )
        )
        if need_grace:
            composed = await _compose_turn_answer(
                router=router,
                objective=turn_objective,
                status=final.reason.value,
                goal=goal,
                history=history,
                turn_artifacts=turn_user_artifacts,
            )
            if composed:
                summary = composed
                if used_web and turn_citations and summary:
                    summary = strip_sources_marker(
                        ensure_cited_answer(summary, turn_citations)
                    )
                if final.reason != StopReason.SUCCESS:
                    events.emit(
                        "grace_summary",
                        {
                            "status": final.reason.value,
                            "chars": len(summary),
                        },
                    )
        exported: list[str] = []
        if self.export_dir is not None:
            try:
                exported = workspace.export_to(self.export_dir)
                self._log(
                    f"[kageha] exported {len(exported)} files to "
                    f"{self.export_dir.expanduser().resolve()}"
                )
            except Exception as e:  # noqa: BLE001
                events.emit("export_error", {"error": str(e)})
                final = StopDecision(StopReason.ERROR, f"Artifact export failed: {e}")

        art_report = format_artifacts_report(
            run_id=workspace.run_id,
            artifacts=all_files,
            workspace_root=workspace.root,
            exported=exported or None,
        )
        workspace.write_text(
            "result.md",
            f"# Result\n\nStatus: {final.reason.value}\n\n{final.message}\n\n"
            f"{summary}\n\n## Artifacts\n\n"
            + (
                "\n".join(f"- `{p}`" for p in user_artifacts)
                if user_artifacts
                else "(none)"
            )
            + "\n",
        )
        validated = final.reason == StopReason.SUCCESS and task_state.validated_ok()
        verified_facts = [
            fact.text
            for fact in task_state.facts[task_state.turn_fact_start :]
            if fact.certainty == "verified"
            and not fact.text.startswith("Current turn request:")
        ]
        verification_evidence = "; ".join(
            [
                *[
                    item.evidence
                    for item in goal.items
                    if item.passes and item.evidence
                ],
                task_state.validation.notes,
            ]
        ).strip("; ")[:4000]
        recovered_failures = (
            [
                (
                    f"{failure.action}: {failure.cause}; recovery change: "
                    f"{failure.required_change or 'adapted approach'}"
                )[:800]
                for failure in task_state.failures[
                    task_state.turn_failure_start :
                ][-5:]
            ]
            if validated
            else []
        )
        workspace.write_text(
            f"_turns/{turn_id}.json",
            json.dumps(
                {
                    "turn_id": turn_id,
                    "request": turn_objective,
                    "status": final.reason.value,
                    "answer": summary,
                    "artifacts": turn_user_artifacts,
                    "steps": ctx.step,
                    "spent_usd": ctx.spent_usd,
                    "validated": validated,
                    "verified_facts": verified_facts,
                    "verification_evidence": verification_evidence,
                    "recovered_failures": recovered_failures,
                    "sources": turn_citations[:20],
                },
                indent=2,
            ),
        )
        # Always surface deliverables in the terminal at end of run
        self._log(art_report)
        try:
            self.hooks.run(
                "stop",
                payload={
                    "turn_id": turn_id,
                    "status": final.reason.value,
                    "steps": ctx.step,
                    "artifacts": user_artifacts[:40],
                },
            )
        except Exception:  # noqa: BLE001
            pass
        self._sync_todo_board_from_progress(
            workspace,
            goal=goal,
            task_state=task_state,
            events=events,
            success=(final.reason == StopReason.SUCCESS),
        )
        events.emit(
            "run_end",
            {
                "turn_id": turn_id,
                "status": final.reason.value,
                "steps": ctx.step,
                "spent_usd": ctx.spent_usd,
                "exported": exported,
                "artifacts": user_artifacts,
                "turn_artifacts": turn_user_artifacts,
                "sources": turn_citations[:20],
            },
        )
        # Tear down MCP subprocesses
        hub = ctx.meta.get("mcp_hub")
        if hub is not None and hasattr(hub, "close"):
            try:
                await hub.close()
            except Exception:  # noqa: BLE001
                pass
        return RunResult(
            run_id=workspace.run_id,
            status=final.reason.value,
            message=summary or final.message,
            goal=goal,
            steps=ctx.step,
            spent_usd=ctx.spent_usd,
            artifacts=user_artifacts,
            turn_id=turn_id,
            turn_artifacts=turn_user_artifacts,
            validated=validated,
            verified_facts=verified_facts,
            verification_evidence=verification_evidence,
            recovered_failures=recovered_failures,
            active_skills=list(ctx.meta.get("active_skills") or []),
            sources=list(turn_citations[:20]),
        )


def _filter_tools_for_skills(ctx: HarnessContext) -> list:
    """When active skills declare allowed-tools, narrow the tool catalog.

    Always keep core skill/MCP/meta tools so the agent can load more skills.
    When the computer pack is loaded, never strip ``computer_*`` — skill
    allowlists must not delete desktop tools (industry: pack ownership wins).
    """
    from kageha.models.base import ToolSpec

    specs = ctx.tools.specs()
    allowed = ctx.meta.get("skill_allowed_tools")
    if not allowed:
        return specs
    keep_prefixes = (
        "skill_",
        "mcp_",
        "ask_human",
        "todo_",
        "read_file",
        "write_file",
        "list_dir",
        "bash",
    )
    allow = set(allowed)
    keep_computer = "computer" in set(ctx.meta.get("tool_packs_enabled") or [])
    out: list[ToolSpec] = []
    for s in specs:
        if (
            s.name in allow
            or any(s.name.startswith(p) for p in keep_prefixes)
            or (keep_computer and s.name.startswith("computer_"))
        ):
            out.append(s)
    return out or specs


def _estimate_usd(
    prompt_tokens: int,
    completion_tokens: int,
    *,
    model_id: str | None = None,
    cached_tokens: int = 0,
) -> float:
    from kageha.models.registry import ModelRegistry, estimate_model_usd

    mc = None
    if model_id:
        try:
            mc = ModelRegistry.load().models.get(model_id)
        except Exception:  # noqa: BLE001
            mc = None
    return estimate_model_usd(
        mc,
        prompt_tokens,
        completion_tokens,
        cached_tokens=cached_tokens,
    )


async def _hitl_continue(gate: ApprovalGate, detail: str) -> bool:
    from kageha.harness.approvals import ApprovalDecision, ApprovalRequest

    return await gate.require(
        ApprovalRequest(
            action="continue_after_no_progress",
            detail=detail,
            risk_class="loop",
            default=ApprovalDecision.ASK,
        )
    )
