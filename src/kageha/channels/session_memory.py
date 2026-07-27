"""Per-sender session continuity for WhatsApp (and similar channels).

Maps phone → run_id so follow-ups resume the same workspace like ``kageha chat``.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kageha.channels.whatsapp import normalize_phone
from kageha.config import kageha_home
from kageha.harness.sandbox import SessionWorkspace
from kageha.io import atomic_write_json

log = logging.getLogger(__name__)

_RESET_RE = re.compile(
    r"^(new\s+session|/new|start\s+over|reset\s+session|forget\s+(this\s+)?chat)\s*$",
    re.I,
)


@dataclass
class ChannelTurnResult:
    ok: bool
    run_id: str = ""
    status: str = ""
    reply: str = ""
    artifacts: list[str] = field(default_factory=list)
    route: str = ""
    error: str = ""
    reset: bool = False
    quick: bool = False


class ChannelSessionStore:
    """Persist identity → session run_id under ~/.kageha/channels/<name>/sessions.json."""

    def __init__(self, channel: str = "whatsapp") -> None:
        self.channel = channel
        self.path = kageha_home() / "channels" / channel / "sessions.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def identity_key(self, identity: str) -> str:
        """WhatsApp uses phone digits; Discord/Slack keep opaque ids."""
        raw = (identity or "").strip()
        if self.channel in {"whatsapp", "sms"}:
            return normalize_phone(raw)
        return raw

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        atomic_write_json(self.path, data)

    def get(self, phone: str) -> str | None:
        key = self.identity_key(phone)
        if not key:
            return None
        entry = self._load().get(key)
        if isinstance(entry, dict):
            rid = str(entry.get("run_id") or "").strip()
            return rid or None
        return None


    def get_model_override(self, phone: str) -> str | None:
        key = self.identity_key(phone)
        entry = self._load().get(key) if key else None
        if not isinstance(entry, dict):
            return None
        value = str(entry.get("model_override") or "").strip()
        return value or None

    def set_model_override(self, phone: str, model: str | None) -> None:
        """Persist a channel model pin without creating an unjournaled session."""
        key = self.identity_key(phone)
        if not key:
            return
        data = self._load()
        entry = data.get(key) if isinstance(data.get(key), dict) else {}
        payload: dict[str, str] = {"updated_at": datetime.now(tz=timezone.utc).isoformat()}
        run_id = str(entry.get("run_id") or "").strip()
        if run_id:
            payload["run_id"] = run_id
        if model:
            payload["model_override"] = model
        data[key] = payload
        self._save(data)

    def set(self, phone: str, run_id: str) -> None:
        key = self.identity_key(phone)
        if not key or not run_id:
            return
        data = self._load()
        previous = data.get(key) if isinstance(data.get(key), dict) else {}
        payload: dict[str, str] = {
            "run_id": run_id,
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        override = str(previous.get("model_override") or "").strip()
        if override:
            payload["model_override"] = override
        data[key] = payload
        self._save(data)

    def clear(self, phone: str) -> None:
        key = self.identity_key(phone)
        if not key:
            return
        data = self._load()
        if key in data:
            data.pop(key, None)
            self._save(data)

    def open_workspace(self, phone: str) -> tuple[str | None, SessionWorkspace | None]:
        run_id = self.get(phone)
        if not run_id:
            return None, None
        from kageha.config import sessions_dir

        root = sessions_dir() / run_id
        if not root.is_dir():
            self.clear(phone)
            return None, None
        try:
            from kageha.runtime import RuntimeStore

            runtime_store = RuntimeStore()
            try:
                runtime_store.inspect_session(run_id)
            finally:
                runtime_store.close()
            return run_id, SessionWorkspace.create(run_id)
        except Exception:  # noqa: BLE001
            self.clear(phone)
            return None, None


def is_session_reset(text: str) -> bool:
    return bool(_RESET_RE.match((text or "").strip()))


def append_chat_log(workspace: SessionWorkspace, role: str, text: str) -> None:
    path = workspace.root / "chat.jsonl"
    rec = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "role": role,
        "text": text,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


async def run_channel_agent_turn(
    *,
    phone: str,
    text: str,
    store: ChannelSessionStore | None = None,
    auto_approve: bool = True,
    approver: Any = None,
    channel_note: str = (
        "You are answering via WhatsApp. Keep replies concise and chat-friendly. "
        "Remember prior turns in this session. Lead with file paths for deliverables."
    ),
) -> ChannelTurnResult:
    """Classify + resume/new-task in the sender's persistent session (chat-like)."""
    from kageha.chat.quick import (
        answer_before_workspace,
        answer_status,
        answer_where,
        is_where_question,
    )
    from kageha.chat.turn_manager import (
        build_turn_context,
        classify_turn,
        expand_user_message,
        ground_artifact_followup,
        new_task_prompt,
        persist_turn_decision,
        prefer_loop_mode,
        resolve_artifact_references,
        route_for_decision,
    )
    from kageha.knowledge.registry import attached_kbs
    from kageha.memory.models import TurnMemoryInput
    from kageha.memory.service import (
        get_memory_service,
        private_channel_key,
        turn_memory_input_from_result,
    )
    from kageha.memory.skills import SkillRegistry

    store = store or ChannelSessionStore()
    # Preserve opaque ids for discord/slack; normalize phones for WhatsApp.
    phone = store.identity_key(phone)
    if not phone:
        return ChannelTurnResult(ok=False, error="empty identity")
    memory = get_memory_service(start_worker=True)
    memory_channel_key = private_channel_key(store.channel, phone)
    msg = (text or "").strip()
    if not msg:
        return ChannelTurnResult(ok=False, error="empty")

    if is_session_reset(msg):
        store.clear(phone)
        return ChannelTurnResult(
            ok=True,
            reset=True,
            quick=True,
            reply="Fresh session started. Send your next task.",
        )

    run_id, workspace = store.open_workspace(phone)

    low = msg.lower()
    if low == "/model" or low.startswith("/model ") or low in {"/models"} or low.startswith(
        "/models "
    ):
        from kageha.chat.model_commands import handle_model_command

        result = handle_model_command(
            msg,
            override=(
                workspace.get_model_override()
                if workspace is not None
                else store.get_model_override(phone)
            ),
            once=workspace.get_model_once() if workspace is not None else None,
            role_overrides=(
                workspace.get_model_role_overrides()
                if workspace is not None
                else {}
            ),
            workspace=workspace,
        )
        if result.handled:
            if result.changed:
                store.set_model_override(phone, result.override)
            return ChannelTurnResult(
                ok=True,
                quick=True,
                run_id=run_id or "",
                reply=result.message,
            )

    try:
        explicit_memory = memory.apply_explicit_user_action(
            msg,
            session_id=run_id or f"channel-{memory_channel_key}",
            project_root=str(Path.cwd()),
            channel_key=memory_channel_key,
        )
    except (RuntimeError, ValueError, KeyError) as exc:
        return ChannelTurnResult(ok=False, error=f"Memory error: {exc}")
    if explicit_memory is not None:
        return ChannelTurnResult(
            ok=True,
            run_id=run_id or "",
            status="success",
            reply=(
                f"Memory updated ({explicit_memory.id}, {explicit_memory.state}): "
                f"{explicit_memory.content}"
            ),
            route="memory_action",
            quick=True,
        )
    turn_ctx = build_turn_context(workspace)
    decision = await classify_turn(msg, turn_ctx)
    route = route_for_decision(
        decision,
        has_session=bool(run_id and workspace),
        message=msg,
        turn_ctx=turn_ctx,
    )
    agent_line = (
        expand_user_message(msg, turn_ctx)
        if route in {"resume", "new_run", "first_run"}
        else msg
    )
    if route == "resume":
        referenced = resolve_artifact_references(
            agent_line,
            turn_ctx,
            preferred=decision.reuse_artifacts,
        )
        if referenced:
            agent_line = ground_artifact_followup(agent_line, referenced)

    if workspace:
        persist_turn_decision(workspace, decision, message=msg, route=route)

    correction = memory.apply_natural_correction(
        msg,
        session_id=run_id or "",
        project_root=str(Path.cwd()),
        channel_key=memory_channel_key,
    )
    if isinstance(correction, list) and route in {
        "quick_where",
        "quick_status",
    }:
        return ChannelTurnResult(
            ok=True,
            run_id=run_id or "",
            status="needs_clarification",
            reply=(
                "That could refer to more than one recalled memory. I suppressed "
                "them for now. Tell me which ID to correct: "
                + ", ".join(correction)
            ),
            route=route,
            quick=True,
        )
    if correction is not None and not isinstance(correction, list) and route in {
        "quick_where",
        "quick_status",
    }:
        return ChannelTurnResult(
            ok=True,
            run_id=run_id or "",
            status="corrected",
            reply=(
                f"Got it — I retracted that memory ({correction.id}). "
                "Tell me the correct version if you want it replaced."
            ),
            route=route,
            quick=True,
        )

    if route == "cancel":
        reply = "Okay — stopped. Send a new task whenever you're ready."
        if workspace:
            append_chat_log(workspace, "user", msg)
            append_chat_log(workspace, "assistant", reply)
        memory.capture_turn(
            TurnMemoryInput(
                session_id=run_id or f"channel-{memory_channel_key}",
                turn_id=uuid.uuid4().hex,
                task=msg,
                user_text=msg,
                assistant_text=reply,
                status="cancelled",
                verified=False,
                project_root=str(Path.cwd()),
                channel_key=memory_channel_key,
            )
        )
        return ChannelTurnResult(
            ok=True,
            run_id=run_id or "",
            status="cancelled",
            reply=reply,
            route=route,
            quick=True,
        )

    if route == "quick_remote":
        from kageha.chat.quick_remote import execute_quick_remote, should_quick_remote

        if workspace:
            append_chat_log(workspace, "user", msg)
        action = should_quick_remote(msg, turn_ctx)
        reply = await execute_quick_remote(
            msg,
            action=action,
            auto_approve=bool(auto_approve),
        )
        if workspace:
            append_chat_log(workspace, "assistant", reply)
        memory.capture_turn(
            TurnMemoryInput(
                session_id=run_id or f"channel-{memory_channel_key}",
                turn_id=uuid.uuid4().hex,
                task=msg,
                user_text=msg,
                assistant_text=reply,
                status="success",
                verified=True,
                project_root=str(Path.cwd()),
                channel_key=memory_channel_key,
            )
        )
        return ChannelTurnResult(
            ok=True,
            run_id=run_id or "",
            status="success",
            reply=reply,
            route=route,
            quick=True,
        )

    if route in {"quick_where", "quick_status"}:
        if not (run_id and workspace):
            if route == "quick_where" or is_where_question(msg):
                reply = "There aren't any files yet because no task has started."
            else:
                reply = answer_before_workspace("/status")
            memory.capture_turn(
                TurnMemoryInput(
                    session_id=f"channel-{memory_channel_key}",
                    turn_id=uuid.uuid4().hex,
                    task=msg,
                    user_text=msg,
                    assistant_text=reply,
                    status="success",
                    verified=True,
                    project_root=str(Path.cwd()),
                    channel_key=memory_channel_key,
                )
            )
            return ChannelTurnResult(
                ok=True, reply=reply, route=route, quick=True
            )
        append_chat_log(workspace, "user", msg)
        if route == "quick_where" or is_where_question(msg):
            reply = answer_where(workspace)
        else:
            reply = answer_status(workspace)
        quick_status = "success"
        quick_verified = True
        append_chat_log(workspace, "assistant", reply)
        memory.capture_turn(
            TurnMemoryInput(
                session_id=run_id,
                turn_id=uuid.uuid4().hex,
                task=msg,
                user_text=msg,
                assistant_text=reply,
                status=quick_status,
                verified=quick_verified,
                project_root=str(Path.cwd()),
                channel_key=memory_channel_key,
            )
        )
        return ChannelTurnResult(
            ok=True,
            run_id=run_id or "",
            status=quick_status,
            reply=reply,
            route=route,
            quick=True,
        )

    # Full agent turn
    from kageha.chat.history import session_continuity_extra

    kbs = attached_kbs()
    skills = SkillRegistry()
    from kageha.memory.bootstrap import prepare_turn_memory

    memory_extra = prepare_turn_memory(
        memory,
        query=agent_line,
        project_root=str(Path.cwd()),
        session_id=run_id or "",
        channel_key=memory_channel_key,
        trace_root=str(workspace.root) if workspace else "",
    )
    if isinstance(correction, list):
        memory_extra = (
            memory_extra
            + "\n\nThe user's correction matched multiple recalled memories, "
            f"which were quarantined: {', '.join(correction)}. Ask one concise "
            "clarifying question to identify the intended claim."
        ).strip()
    memory_extra = (memory_extra + "\n\n" + channel_note).strip()
    if workspace:
        cont = session_continuity_extra(workspace, current_user=msg)
        if cont:
            memory_extra = (memory_extra + "\n\n" + cont).strip()
    _tool_cap = (
        "You HAVE browser_* (if pack enabled), skill_run for network_scan / "
        "sony_bravia / android_tv, "
        "bash/shell, and skill_load/skill_run. Never claim you cannot scan "
        "the LAN, open a browser, or act when a matching tool/skill exists — "
        "use them (or write/run a script) instead of refusing."
    )
    if run_id and workspace and route == "resume":
        memory_extra += (
            "\nThis is a follow-up in an ongoing channel session — "
            "use prior session files and chat context; do not restart from scratch. "
            + _tool_cap
        )
    elif run_id and workspace and route in {"new_run", "first_run"}:
        # Topic switch / new task: new_task_prompt already discards the old plan.
        memory_extra += "\n" + _tool_cap

    durable: Any = None
    try:
        from kageha.config import security_profile
        from kageha.runtime import (
            AgentRuntime,
            SecurityProfile,
            TurnRequest,
        )

        durable = AgentRuntime()
        catalog = skills.catalog(limit=40)
        model_override = (
            workspace.get_model_override()
            if workspace is not None
            else store.get_model_override(phone)
        )
        loop_mode = prefer_loop_mode(
            msg, decision, route=route, workspace=workspace
        )
        request_args = {
            "user_id": "local",
            "agent_id": "main",
            "channel_key": memory_channel_key,
            "project_root": str(Path.cwd()),
            "auto_approve": auto_approve,
            "approver": approver,
            "security_profile": SecurityProfile(security_profile()),
            "knowledge_bases": tuple(kbs),
            "skill_catalog": catalog,
            "kb_pins": ", ".join(kbs) if kbs else "",
            "system_extra": memory_extra,
            "model_override": model_override or "",
            "live": False,
            "defer_human_input": True,
            "platform": store.channel or "whatsapp",
            "loop_mode": loop_mode,
        }
        if route == "resume" and run_id and workspace:
            append_chat_log(workspace, "user", msg)
            result = await durable.execute_resume(
                run_id,
                agent_line,
                **request_args,
            )
        else:
            prior_id = run_id if route == "new_run" else None
            task_text = new_task_prompt(
                agent_line,
                prior_run_id=prior_id,
                reuse_artifacts=decision.reuse_artifacts,
            )
            if prior_id and workspace:
                append_chat_log(
                    workspace,
                    "system",
                    f"new task turn in session {prior_id}",
                )
                append_chat_log(workspace, "user", msg)
                result = await durable.execute_resume(
                    prior_id,
                    task_text,
                    **request_args,
                )
            else:
                result = await durable.execute(
                    TurnRequest(objective=task_text, **request_args)
                )
                run_id = result.run_id
                workspace = SessionWorkspace.create(run_id)
                if model_override:
                    workspace.set_model_override(model_override)
                append_chat_log(workspace, "user", msg)
                persist_turn_decision(
                    workspace, decision, message=msg, route=route
                )

        store.set(phone, result.run_id)
        memory.capture_turn(
            turn_memory_input_from_result(
                result,
                task=msg,
                user_text=msg,
                project_root=str(Path.cwd()),
                channel_key=memory_channel_key,
            )
        )
        reply = (result.message or result.status or "(no reply)").strip()
        if workspace:
            append_chat_log(workspace, "assistant", reply)
        return ChannelTurnResult(
            ok=True,
            run_id=result.run_id,
            status=result.status,
            reply=reply,
            artifacts=list(result.artifacts or []),
            route=route,
        )
    except Exception as e:  # noqa: BLE001
        memory.capture_turn(
            TurnMemoryInput(
                session_id=run_id or f"channel-{memory_channel_key}",
                turn_id=uuid.uuid4().hex,
                task=msg,
                user_text=msg,
                assistant_text=str(e),
                status="error",
                verified=False,
                project_root=str(Path.cwd()),
                channel_key=memory_channel_key,
            )
        )
        return ChannelTurnResult(ok=False, error=str(e), run_id=run_id or "")
    finally:
        if durable is not None:
            durable.close()
