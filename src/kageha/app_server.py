"""Thin JSON-RPC App Server over stdio."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from kageha.config import security_profile
from kageha.memory.models import MemoryMutation, MemoryQuery, TurnMemoryInput
from kageha.memory.service import (
    get_memory_service,
    turn_memory_input_from_result,
)


def _resolve_agent_mode(
    params: dict[str, Any],
    *,
    message: str,
    workspace: Any = None,
) -> str:
    from kageha.chat.turn_manager import prefer_agent_mode

    explicit = str(params.get("agent_mode") or "").strip() or None
    return prefer_agent_mode(message, workspace=workspace, explicit=explicit)


def _resolve_loop_mode(
    params: dict[str, Any],
    *,
    message: str,
    workspace: Any = None,
    agent_mode: str | None = None,
) -> str:
    """Chat-like default: followup; plan/goal or explicit full → full loop.

    Deep agent modes always win over a stale ``loop_mode=followup`` from older
    clients (WebUI historically special-cased only ``/plan``).
    """
    from kageha.loop.mode_policy import loop_mode_for, normalize_agent_mode

    try:
        mode = normalize_agent_mode(
            agent_mode
            or _resolve_agent_mode(params, message=message, workspace=workspace)
        )
    except Exception:  # noqa: BLE001
        mode = "normal"
    if mode != "normal":
        return loop_mode_for(mode)
    raw = str(params.get("loop_mode") or "").strip().lower()
    if raw in {"full", "followup", "act"}:
        return "followup" if raw == "act" else raw
    return loop_mode_for(mode)


def prefer_loop_mode_safe(message: str, workspace: Any = None) -> str:
    from kageha.chat.turn_manager import TurnDecision, prefer_loop_mode

    decision = TurnDecision(
        intent="continue_task",
        related_to_current_task=True,
        requires_tools=True,
        discard_old_plan=False,
        reason="app_server",
    )
    return prefer_loop_mode(message, decision, route="resume", workspace=workspace)


class AppServer:
    def __init__(self) -> None:
        self.threads: dict[str, dict[str, Any]] = {}
        self._handles: dict[str, Any] = {}
        self._approval_waiters: dict[str, asyncio.Future] = {}
        self._memory = None
        self._runtime = None

    def _make_web_approver(self, thread_id: str):
        """Pause until ``thread/approve`` resolves (tools + Plan Build + request_approval).

        Prefer ``approval_id`` stamped by runtime ``approval_audit`` (via
        ``ApprovalGate.require`` / ``require_explicit``) so SSE
        ``approval_required`` and this waiter share one id.
        """

        async def approver(req: Any) -> Any:
            import uuid

            from kageha.harness.approvals import normalize_approval_result

            aid = str(getattr(req, "approval_id", "") or "") or uuid.uuid4().hex
            try:
                setattr(req, "approval_id", aid)
            except Exception:  # noqa: BLE001
                pass
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._approval_waiters[aid] = fut
            pending = {
                "approval_id": aid,
                "action": str(getattr(req, "action", "") or ""),
                "detail": str(getattr(req, "detail", "") or "")[:800],
                "risk_class": str(getattr(req, "risk_class", "") or ""),
                "thread_id": thread_id,
            }
            self.threads.setdefault(thread_id, {})["pending_approval"] = pending
            try:
                raw = await asyncio.wait_for(fut, timeout=600.0)
                return normalize_approval_result(raw)
            except asyncio.TimeoutError:
                return normalize_approval_result(False)
            finally:
                self._approval_waiters.pop(aid, None)
                state = self.threads.get(thread_id)
                if isinstance(state, dict):
                    pending = state.get("pending_approval")
                    if isinstance(pending, dict) and pending.get("approval_id") == aid:
                        state.pop("pending_approval", None)

        return approver

    def _workspace(self, run_id: str):
        if not run_id:
            return None
        from kageha.harness.sandbox import SessionWorkspace

        self.runtime.store.inspect_session(run_id)
        return SessionWorkspace.create(run_id)

    def close(self) -> None:
        if self._runtime is not None and not self._runtime._handles:  # noqa: SLF001
            self._runtime.close()
        if self._memory is not None:
            self._memory.stop_worker(timeout=2.0)

    @property
    def memory(self):
        if self._memory is None:
            self._memory = get_memory_service(start_worker=True)
        return self._memory

    @property
    def runtime(self):
        if self._runtime is None:
            from kageha.runtime import AgentRuntime

            self._runtime = AgentRuntime()
        return self._runtime

    def _thread_dict(self, thread_id: str) -> dict[str, Any]:
        st = self.threads.get(thread_id)
        if not isinstance(st, dict):
            st = {}
            self.threads[thread_id] = st
        return st

    async def handle(self, req: dict[str, Any]) -> dict[str, Any]:
        method = str(req.get("method") or "")
        params = req.get("params") or {}
        req_id = req.get("id")
        try:
            result = await self._dispatch(method, params)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(
                f"[kageha-app-server] {method or 'unknown'} failed: "
                f"{type(e).__name__}: {e}\n"
            )
            sys.stderr.flush()
            if method in {"thread/turn", "thread/resume"}:
                try:
                    thread_id = str(params.get("thread_id") or "default")
                    session_id = str(
                        params.get("run_id")
                        or self._thread_dict(thread_id).get("run_id")
                        or f"app-{thread_id}"
                    )
                    task = str(params.get("message") or params.get("task") or "")
                    self.memory.capture_turn(
                        TurnMemoryInput(
                            session_id=session_id,
                            turn_id=f"error-{time.time_ns()}",
                            task=task,
                            user_text=task,
                            assistant_text=str(e),
                            status="error",
                            verified=False,
                            project_root=str(params.get("project_root") or Path.cwd()),
                            user_id=str(params.get("user_id") or "local"),
                            agent_id=str(params.get("agent_id") or "main"),
                            channel_key=str(params.get("channel_key") or ""),
                        )
                    )
                except Exception:
                    pass
            detail = str(e).strip() or type(e).__name__
            # Keep doctor as fallback hint, but always surface the real failure —
            # opaque "Request failed" made healthy doctor output useless when the
            # session was blocked on reconciliation / InvalidTransition.
            if any(
                token in detail.lower()
                for token in ("reconcile", "reconciliation", "uncertain")
            ):
                message = (
                    f"{detail} Start a new chat, or clear the stuck approval/"
                    "uncertain tool attempt on this session."
                )
            elif "terminal phase blocked" in detail.lower():
                message = (
                    f"{detail} This session turn is stuck in a blocked phase — "
                    "start a new chat to continue."
                )
            else:
                message = (
                    f"{type(e).__name__}: {detail}. "
                    "If providers look wrong, run `kageha models list` / `kageha models test`."
                )
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32000,
                    "message": message,
                    "data": {
                        "error_type": type(e).__name__,
                        "detail": detail,
                    },
                },
            }

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "thread/start":
            thread_id = params.get("thread_id") or params.get("id") or "default"
            self.threads[thread_id] = {
                "messages": [],
                "run_id": None,
            }
            return {"thread_id": thread_id}
        if method == "thread/turn":
            from kageha.chat.model_commands import handle_model_command
            from kageha.memory.skills import SkillRegistry

            thread_id = params.get("thread_id") or "default"
            task = params.get("message") or params.get("task") or ""
            auto = bool(params.get("auto_approve", False))
            kbs = list(params.get("knowledge_bases") or [])
            project_root = str(params.get("project_root") or Path.cwd())
            user_id = str(params.get("user_id") or "local")
            agent_id = str(params.get("agent_id") or "main")
            channel_key = str(params.get("channel_key") or "")
            prior_run = str(
                params.get("run_id")
                or self._thread_dict(thread_id).get("run_id")
                or ""
            )
            # The Web UI reserves its session id before the first real turn so
            # uploads and chat history have a stable workspace.  That shell is
            # not in the runtime store yet and therefore cannot be resumed.
            # Preserve the reserved id when submitting its first runtime turn.
            runtime_session_exists = False
            if prior_run:
                try:
                    self.runtime.store.inspect_session(prior_run)
                    runtime_session_exists = True
                except KeyError:
                    pass
            model_param = params.get("model") or params.get("model_override")
            task_text = str(task)
            # Mode-only (/plan, "plan", …): switch machine, do NOT invent a
            # junk plan.md with Objective: plan and demand Build.
            from kageha.loop.mode_policy import (
                is_mode_only_message,
                mode_only_ack,
                normalize_agent_mode,
                parse_mode_slash,
                write_agent_mode_flag,
            )

            if is_mode_only_message(task_text):
                mode = parse_mode_slash(task_text) or normalize_agent_mode(
                    task_text.lstrip("/")
                )
                if prior_run:
                    try:
                        ws = self._workspace(prior_run)
                        if ws is not None and mode != "normal":
                            write_agent_mode_flag(ws.root, mode)
                        from kageha.chat.history import append_chat_log

                        if ws is not None:
                            ack = mode_only_ack(mode)
                            append_chat_log(ws, "user", task_text)
                            append_chat_log(ws, "assistant", ack)
                    except Exception:  # noqa: BLE001
                        pass
                self.threads.setdefault(thread_id, {})
                if prior_run:
                    self.threads[thread_id]["run_id"] = prior_run
                return {
                    "run_id": prior_run or "",
                    "status": "success",
                    "message": mode_only_ack(mode),
                    "artifacts": [],
                    "turn_id": "",
                    "agent_mode": mode,
                    "quick": True,
                }

            # Micro greetings/acks: skip memory recall + AgentRuntime entirely.
            from kageha.chat.quick import quick_chat_reply

            channel_hint = str(
                params.get("channel_key") or params.get("platform") or ""
            )
            quick = quick_chat_reply(task_text, channel=channel_hint)
            if quick:
                if prior_run:
                    try:
                        from kageha.chat.history import append_chat_log

                        ws = self._workspace(prior_run)
                        if ws is not None:
                            append_chat_log(ws, "user", task_text)
                            append_chat_log(ws, "assistant", quick)
                    except Exception:  # noqa: BLE001
                        pass
                self.threads.setdefault(thread_id, {})
                if prior_run:
                    self.threads[thread_id]["run_id"] = prior_run
                return {
                    "run_id": prior_run or "",
                    "status": "success",
                    "message": quick,
                    "artifacts": [],
                    "quick": True,
                }
            low = task_text.lower().strip()
            if low == "/model" or low.startswith("/model ") or low in {"/models"} or low.startswith(
                "/models "
            ):
                ws = self._workspace(prior_run)
                result = handle_model_command(
                    task_text,
                    override=(
                        ws.get_model_override()
                        if ws is not None
                        else self.threads.get(thread_id, {}).get("model_override")
                    ),
                    once=ws.get_model_once() if ws is not None else None,
                    workspace=ws,
                )
                if result.handled:
                    self.threads.setdefault(thread_id, {})[
                        "model_override"
                    ] = result.override
                    return {
                        "run_id": prior_run,
                        "status": "success",
                        "message": result.message,
                        "artifacts": [],
                    }
            if model_param and prior_run:
                try:
                    ws = self._workspace(prior_run)
                    if ws is not None:
                        ws.set_model_override(str(model_param))
                except FileNotFoundError:
                    pass
            explicit_memory = self.memory.apply_explicit_user_action(
                task_text,
                session_id=prior_run or f"app-{thread_id}",
                project_root=project_root,
                user_id=user_id,
                agent_id=agent_id,
                channel_key=channel_key,
            )
            if explicit_memory is not None:
                return {
                    "run_id": prior_run,
                    "status": "success",
                    "message": (
                        f"Memory updated ({explicit_memory.id}, "
                        f"{explicit_memory.state}): {explicit_memory.content}"
                    ),
                    "memory": explicit_memory.to_dict(),
                    "artifacts": [],
                }
            correction = self.memory.apply_natural_correction(
                str(task),
                session_id=prior_run,
                project_root=project_root,
                user_id=user_id,
                agent_id=agent_id,
                channel_key=channel_key,
            )
            from kageha.memory.bootstrap import prepare_turn_memory

            memory_extra = prepare_turn_memory(
                self.memory,
                query=str(task),
                project_root=project_root,
                session_id=prior_run,
                user_id=user_id,
                agent_id=agent_id,
                channel_key=channel_key,
            )
            if isinstance(correction, list):
                memory_extra = (
                    memory_extra
                    + "\n\nThe user's correction matched multiple recalled memories, "
                    f"which were quarantined: {', '.join(correction)}. Ask one concise "
                    "clarifying question to identify the intended claim."
                ).strip()
            model_override = None
            if prior_run:
                try:
                    ws = self._workspace(prior_run)
                    model_override = ws.get_model_override() if ws is not None else None
                except (FileNotFoundError, KeyError):
                    model_override = None
            model_override = (
                model_override
                or self.threads.get(thread_id, {}).get("model_override")
            )
            if model_param:
                model_override = str(model_param)
            catalog = SkillRegistry().catalog(limit=40, query=task_text)
            from kageha.runtime import SecurityProfile, TurnRequest

            # Match chat/channels: followup (act) by default; full only for /plan.
            prior_ws = None
            if prior_run:
                try:
                    prior_ws = self._workspace(prior_run)
                except Exception:  # noqa: BLE001
                    prior_ws = None
            agent_mode = _resolve_agent_mode(
                params, message=task_text, workspace=prior_ws
            )
            loop_mode = _resolve_loop_mode(
                params,
                message=task_text,
                workspace=prior_ws,
                agent_mode=agent_mode,
            )
            max_steps = params.get("max_steps")
            platform = str(params.get("platform") or "gateway")
            request_args = {
                "user_id": user_id,
                "agent_id": agent_id,
                "channel_key": channel_key,
                "project_root": project_root,
                "auto_approve": auto,
                "security_profile": SecurityProfile(
                    security_profile(
                        str(params.get("security_profile") or "") or None
                    )
                ),
                "knowledge_bases": tuple(kbs),
                "skill_catalog": catalog,
                "kb_pins": ", ".join(kbs) if kbs else "",
                "system_extra": memory_extra,
                "model_override": model_override or "",
                "live": False,
                "platform": platform,
                "loop_mode": loop_mode,
                "agent_mode": agent_mode,
                # Build gate for Plan/Spec — never implied by tool auto_approve.
                "auto_build": bool(params.get("auto_build", False)),
                "defer_human_input": bool(
                    params.get("defer_human_input", True)
                ),
            }
            if max_steps is not None:
                request_args["max_steps"] = int(max_steps)
            # WebUI: always attach approver so Plan/Spec Build can pause even
            # when tool auto_approve is on. Tool risks still use ApprovalGate.
            if platform == "webui":
                request_args["approver"] = self._make_web_approver(thread_id)
            handle = (
                self.runtime.resume(prior_run, task_text, **request_args)
                if runtime_session_exists
                else self.runtime.submit(
                    TurnRequest(
                        objective=task_text,
                        session_id=prior_run,
                        **request_args,
                    )
                )
            )
            self._handles[thread_id] = handle
            # Publish turn/session ids immediately so clients can poll
            # thread/events (and Web UI SSE) while the turn is still running.
            self.threads.setdefault(thread_id, {})
            self.threads[thread_id]["run_id"] = handle.session_id
            self.threads[thread_id]["turn_id"] = handle.turn_id
            try:
                result = await handle.result()
            finally:
                self._handles.pop(thread_id, None)
            self.memory.capture_turn(
                turn_memory_input_from_result(
                    result,
                    task=str(task),
                    user_text=str(task),
                    project_root=project_root,
                    user_id=user_id,
                    agent_id=agent_id,
                    channel_key=channel_key,
                )
            )
            self.threads.setdefault(thread_id, {})["run_id"] = result.run_id
            self.threads.setdefault(thread_id, {})["turn_id"] = handle.turn_id
            # Persist chat.jsonl so Web UI reopen + model continuity see history
            # (runtime also writes `_turns/`; chat log was only written for quick).
            try:
                from kageha.chat.history import append_chat_log

                ws = self._workspace(str(result.run_id or ""))
                if ws is not None and task_text:
                    append_chat_log(ws, "user", task_text)
                    reply = str(result.message or "").strip()
                    if reply:
                        append_chat_log(ws, "assistant", reply)
            except Exception:  # noqa: BLE001
                pass
            return {
                "run_id": result.run_id,
                "status": result.status,
                "message": result.message,
                "artifacts": result.artifacts,
                "turn_id": handle.turn_id,
                "sources": list(getattr(result, "sources", None) or [])[:20],
            }
        if method == "thread/inject":
            thread_id = params.get("thread_id") or "default"
            handle = self._handles.get(thread_id)
            if handle is not None:
                handle.inject(str(params.get("message") or ""))
                return {"ok": True}
            raise RuntimeError("No active run for thread")
        if method == "thread/cancel":
            thread_id = params.get("thread_id") or "default"
            handle = self._handles.get(thread_id)
            if handle is not None:
                handle.cancel()
            # Unblock any waiting approvals so the turn can finish cancelling.
            pending = (self.threads.get(thread_id) or {}).get("pending_approval")
            if isinstance(pending, dict):
                aid = str(pending.get("approval_id") or "")
                fut = self._approval_waiters.get(aid)
                if fut is not None and not fut.done():
                    fut.set_result(False)
            return {"ok": True}
        if method == "thread/approve":
            approval_id = str(params.get("approval_id") or "").strip()
            if not approval_id:
                raise ValueError("approval_id required")
            approved = bool(params.get("approved", False))
            feedback = str(params.get("feedback") or "").strip()
            scope = str(params.get("scope") or "once").strip().lower() or "once"
            if scope == "always":
                scope = "full"
            if scope not in {"once", "session", "full"}:
                scope = "once"
            fut = self._approval_waiters.get(approval_id)
            if fut is None:
                raise KeyError(f"unknown or expired approval: {approval_id}")
            if not fut.done():
                fut.set_result(
                    {
                        "approved": approved,
                        "feedback": feedback,
                        "scope": scope if approved else "once",
                    }
                )
            return {
                "ok": True,
                "approval_id": approval_id,
                "approved": approved,
                "feedback": feedback,
                "scope": scope if approved else "once",
            }
        if method == "thread/resume":
            from kageha.memory.skills import SkillRegistry

            run_id = params.get("run_id")
            if not run_id:
                raise ValueError("run_id required")
            kbs = list(params.get("knowledge_bases") or [])
            follow = params.get("message") or params.get("task")
            task_hint = str(follow or f"resume:{run_id}")
            project_root = str(params.get("project_root") or Path.cwd())
            user_id = str(params.get("user_id") or "local")
            agent_id = str(params.get("agent_id") or "main")
            channel_key = str(params.get("channel_key") or "")
            thread_id = str(params.get("thread_id") or "default")
            explicit_memory = self.memory.apply_explicit_user_action(
                task_hint,
                session_id=str(run_id),
                project_root=project_root,
                user_id=user_id,
                agent_id=agent_id,
                channel_key=channel_key,
            )
            if explicit_memory is not None:
                return {
                    "run_id": str(run_id),
                    "status": "success",
                    "message": (
                        f"Memory updated ({explicit_memory.id}, "
                        f"{explicit_memory.state}): {explicit_memory.content}"
                    ),
                    "memory": explicit_memory.to_dict(),
                }
            correction = self.memory.apply_natural_correction(
                task_hint,
                session_id=str(run_id),
                project_root=project_root,
                user_id=user_id,
                agent_id=agent_id,
                channel_key=channel_key,
            )
            from kageha.memory.bootstrap import prepare_turn_memory

            resume_memory = prepare_turn_memory(
                self.memory,
                query=task_hint,
                project_root=project_root,
                session_id=str(run_id),
                user_id=user_id,
                agent_id=agent_id,
                channel_key=channel_key,
            )
            if isinstance(correction, list):
                resume_memory = (
                    resume_memory
                    + "\n\nThe user's correction matched multiple recalled memories, "
                    f"which were quarantined: {', '.join(correction)}. Ask one concise "
                    "clarifying question to identify the intended claim."
                ).strip()
            from kageha.runtime import SecurityProfile

            resume_ws = self._workspace(str(run_id))
            resume_msg = str(follow or task_hint)
            agent_mode = _resolve_agent_mode(
                params, message=resume_msg, workspace=resume_ws
            )
            loop_mode = _resolve_loop_mode(
                params,
                message=resume_msg,
                workspace=resume_ws,
                agent_mode=agent_mode,
            )
            resume_platform = str(params.get("platform") or "gateway")
            resume_kwargs: dict[str, Any] = {
                "user_id": user_id,
                "agent_id": agent_id,
                "channel_key": channel_key,
                "project_root": project_root,
                "auto_approve": bool(params.get("auto_approve", False)),
                "auto_build": bool(params.get("auto_build", False)),
                "security_profile": SecurityProfile(
                    security_profile(
                        str(params.get("security_profile") or "") or None
                    )
                ),
                "knowledge_bases": tuple(kbs),
                "skill_catalog": SkillRegistry().catalog(limit=40),
                "kb_pins": ", ".join(kbs) if kbs else "",
                "system_extra": resume_memory,
                "live": False,
                "platform": resume_platform,
                "loop_mode": loop_mode,
                "agent_mode": agent_mode,
                "defer_human_input": bool(
                    params.get("defer_human_input", True)
                ),
            }
            if params.get("max_steps") is not None:
                resume_kwargs["max_steps"] = int(params["max_steps"])
            if resume_platform == "webui":
                resume_kwargs["approver"] = self._make_web_approver(thread_id)
            handle = self.runtime.resume(
                str(run_id),
                str(follow or "Continue until the remaining goals pass."),
                **resume_kwargs,
            )
            self._handles[thread_id] = handle
            self.threads.setdefault(thread_id, {})
            self.threads[thread_id]["run_id"] = handle.session_id
            self.threads[thread_id]["turn_id"] = handle.turn_id
            try:
                result = await handle.result()
            finally:
                self._handles.pop(thread_id, None)
            self.memory.capture_turn(
                turn_memory_input_from_result(
                    result,
                    task=task_hint,
                    user_text=str(follow or ""),
                    project_root=project_root,
                    user_id=user_id,
                    agent_id=agent_id,
                    channel_key=channel_key,
                )
            )
            self.threads.setdefault(thread_id, {})["run_id"] = result.run_id
            self.threads.setdefault(thread_id, {})["turn_id"] = handle.turn_id
            return {
                "run_id": result.run_id,
                "status": result.status,
                "message": result.message,
                "turn_id": handle.turn_id,
                "sources": list(getattr(result, "sources", None) or [])[:20],
            }
        if method == "thread/events":
            thread_id = str(params.get("thread_id") or "default")
            turn_id = str(
                params.get("turn_id")
                or self.threads.get(thread_id, {}).get("turn_id")
                or ""
            )
            if not turn_id:
                return []
            after = int(params.get("after_sequence") or 0)
            return [
                event.to_dict()
                for event in self.runtime.store.events(
                    turn_id,
                    after_sequence=after,
                )
            ]
        if method == "runtime/status":
            return self.runtime.store.status()
        if method == "runtime/list":
            return self.runtime.store.list_sessions(
                int(params.get("limit") or 50)
            )
        if method == "runtime/inspect":
            return self.runtime.store.inspect_session(
                str(params.get("session_id") or "")
            )
        if method == "runtime/rebuild":
            rebuilt = self.runtime.store.rebuild(
                str(params.get("session_id") or "")
            )
            return {
                turn_id: snapshot.to_dict()
                for turn_id, snapshot in rebuilt.items()
            }
        if method == "runtime/metrics":
            return self.runtime.store.metric_summary(
                since=float(params.get("since") or 0.0)
            )
        if method == "memory/status":
            return self.memory.status()
        if method == "memory/list":
            return [
                record.to_dict()
                for record in self.memory.inspect(
                    state=str(params.get("state") or ""),
                    scope_type=str(params.get("scope_type") or params.get("scope") or ""),
                    project_root=str(params.get("project_root") or ""),
                    session_id=str(params.get("session_id") or ""),
                    user_id=str(params.get("user_id") or "local"),
                    agent_id=str(params.get("agent_id") or "main"),
                    channel_key=str(params.get("channel_key") or ""),
                    limit=int(params.get("limit") or 100),
                )
            ]
        if method == "memory/recall":
            context = self.memory.recall(
                MemoryQuery(
                    query=str(params.get("query") or ""),
                    project_root=str(params.get("project_root") or Path.cwd()),
                    session_id=str(params.get("session_id") or ""),
                    user_id=str(params.get("user_id") or "local"),
                    agent_id=str(params.get("agent_id") or "main"),
                    channel_key=str(params.get("channel_key") or ""),
                    max_results=(
                        int(params["max_results"])
                        if params.get("max_results") is not None
                        else None
                    ),
                    trace_root=str(params.get("trace_root") or ""),
                )
            )
            return {
                "context": context.render(),
                "trace_id": context.trace_id,
                "instructions": [
                    asdict(item.record) for item in context.instructions
                ],
                "project": [asdict(item.record) for item in context.project],
                "episodes": [asdict(item.record) for item in context.episodes],
            }
        if method == "memory/mutate":
            record = self.memory.mutate(
                MemoryMutation(
                    action=str(params.get("action") or ""),
                    content=str(params.get("content") or ""),
                    target=str(params.get("target") or params.get("id") or ""),
                    kind=str(params.get("kind") or ""),
                    scope_type=str(params.get("scope_type") or params.get("scope") or ""),
                    project_root=str(params.get("project_root") or Path.cwd()),
                    session_id=str(params.get("session_id") or ""),
                    user_id=str(params.get("user_id") or "local"),
                    agent_id=str(params.get("agent_id") or "main"),
                    channel_key=str(params.get("channel_key") or ""),
                    source_role="user",
                    verification_evidence="explicit app-server mutation",
                )
            )
            return record.to_dict()
        if method == "memory/explain":
            trace_id = str(params.get("trace_id") or "")
            trace = (
                self.memory.explain(trace_id)
                if trace_id
                else self.memory.latest_trace(
                    session_id=str(params.get("session_id") or "")
                )
            )
            return trace.to_dict() if trace else None
        if method == "memory/reindex":
            return asdict(self.memory.rebuild_index())
        if method == "ping":
            return {"pong": True}
        if method in {"jobs/run", "cloud/run"}:
            from kageha.project.async_jobs import enqueue_job, job_to_api_dict

            job = enqueue_job(
                objective=str(params.get("objective") or params.get("message") or ""),
                project_root=str(params.get("project_root") or Path.cwd()),
                agent_mode=str(params.get("agent_mode") or "plan"),
                loop_mode=str(params.get("loop_mode") or "full"),
                max_steps=int(params.get("max_steps") or 40),
                notify_channel=str(params.get("notify_channel") or ""),
                start=True,
            )
            return job_to_api_dict(job)
        if method in {"jobs/list", "cloud/list"}:
            from kageha.project.async_jobs import job_counts, job_to_api_dict, list_jobs

            limit = int(params.get("limit") or 40)
            status = str(params.get("status") or "")
            return {
                "jobs": [
                    job_to_api_dict(j)
                    for j in list_jobs(limit=limit, status=status or None)
                ],
                "counts": job_counts(),
            }
        if method in {"jobs/status", "cloud/status"}:
            from kageha.project.async_jobs import job_to_api_dict, load_job

            job = load_job(str(params.get("job_id") or params.get("id") or ""))
            if job is None:
                raise FileNotFoundError("job not found")
            return job_to_api_dict(job)
        if method in {"jobs/cancel", "cloud/cancel"}:
            from kageha.project.async_jobs import cancel_job, job_to_api_dict

            job = cancel_job(str(params.get("job_id") or params.get("id") or ""))
            return job_to_api_dict(job)
        if method in {"jobs/attach", "cloud/attach"}:
            from kageha.project.async_jobs import attach_info

            return attach_info(str(params.get("job_id") or params.get("id") or ""))
        raise ValueError(f"Unknown method: {method}")


async def serve_stdio() -> None:
    server = AppServer()
    loop = asyncio.get_event_loop()
    try:
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            req = json.loads(line)
            resp = await server.handle(req)
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    finally:
        server.close()


def main(listen: str = "stdio://") -> None:
    """Entry point — ``listen`` may be stdio://, unix://, or ws://127.0.0.1:PORT."""
    from kageha.app_server_listen import main_listen

    main_listen(listen)


if __name__ == "__main__":
    main()
