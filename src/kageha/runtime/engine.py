"""Public asynchronous agent runtime backed by the durable journal."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from kageha.config import max_usd, sessions_dir
from kageha.harness.sandbox import SessionWorkspace
from kageha.loop.controller import LoopController, RunResult
from kageha.runtime.journal import ToolJournal
from kageha.runtime.providers import ProviderControlPlane
from kageha.runtime.security import ExecutionSecurityPolicy
from kageha.runtime.store import RuntimeStore
from kageha.runtime.telemetry import Telemetry
from kageha.runtime.types import (
    FailureClass,
    RunEvent,
    RunEventKind,
    TurnRequest,
)
from kageha.runtime.validators import validate_result


class RunHandle:
    """Live handle for one durable turn."""

    def __init__(
        self,
        *,
        runtime: "AgentRuntime",
        session_id: str,
        turn_id: str,
    ) -> None:
        self.runtime = runtime
        self.session_id = session_id
        self.turn_id = turn_id
        self._task: asyncio.Task[RunResult] | None = None
        self._controller: LoopController | None = None

    async def result(self) -> RunResult:
        if self._task is None:
            raise RuntimeError("run has not started")
        return await self._task

    async def events(
        self,
        *,
        after_sequence: int = 0,
        poll_interval: float = 0.05,
    ) -> AsyncIterator[RunEvent]:
        sequence = after_sequence
        while True:
            events = self.runtime.store.events(
                self.turn_id,
                after_sequence=sequence,
            )
            for event in events:
                sequence = event.sequence
                yield event
            snapshot = self.runtime.store.get_snapshot(self.turn_id)
            if snapshot.terminal and not events:
                return
            await asyncio.sleep(poll_interval)

    def cancel(self) -> None:
        if self._controller is not None:
            self._controller.cancel()
        elif self._task is not None:
            self._task.cancel()

    def inject(self, message: str) -> None:
        if self._controller is None:
            raise RuntimeError("controller is not active")
        self._controller.inject(message)


class AgentRuntime:
    """Canonical durable runtime.

    The loop controller is an internal execution component. Session authority,
    transitions, recovery, and event semantics are owned exclusively by the
    durable journal.
    """

    def __init__(
        self,
        store: RuntimeStore | None = None,
        *,
        controller_factory: Callable[..., LoopController] = LoopController,
    ) -> None:
        self._owns_store = store is None
        self.store = store or RuntimeStore()
        self.controller_factory = controller_factory
        self.provider_control = ProviderControlPlane(self.store)
        self.telemetry = Telemetry(self.store)
        self._handles: dict[str, RunHandle] = {}
        self._closed = False

    def close(self) -> None:
        """Release the runtime database after all submitted turns finish."""
        if self._closed:
            return
        if self._handles:
            raise RuntimeError("cannot close AgentRuntime while turns are active")
        if self._owns_store:
            self.store.close()
        self._closed = True

    def __enter__(self) -> "AgentRuntime":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("AgentRuntime is closed")

    def submit(self, request: TurnRequest) -> RunHandle:
        self._ensure_open()
        event, _ = self.store.start_turn(request, max_usd=max_usd())
        handle = RunHandle(
            runtime=self,
            session_id=event.session_id,
            turn_id=event.turn_id,
        )
        self._handles[event.turn_id] = handle
        self.telemetry.metric(
            "turn.accepted",
            1,
            session_id=event.session_id,
            turn_id=event.turn_id,
            labels={"platform": request.platform},
        )
        handle._task = asyncio.create_task(
            self._execute(handle, request, resumed=False)
        )
        return handle

    def resume(self, session_id: str, message: str, **kwargs: Any) -> RunHandle:
        self._ensure_open()
        if not (sessions_dir() / session_id).is_dir():
            raise KeyError(f"unknown session: {session_id}")
        request = TurnRequest(
            objective=message,
            session_id=session_id,
            **kwargs,
        )
        incomplete = self.store.latest_incomplete(session_id)
        if incomplete is not None:
            handle = RunHandle(
                runtime=self,
                session_id=session_id,
                turn_id=incomplete.turn_id,
            )
            self._handles[incomplete.turn_id] = handle
            handle._task = asyncio.create_task(
                self._execute(handle, request, resumed=True)
            )
            return handle
        event, _ = self.store.start_turn(
            request,
            session_id=session_id,
            max_usd=max_usd(),
        )
        handle = RunHandle(
            runtime=self,
            session_id=session_id,
            turn_id=event.turn_id,
        )
        self._handles[event.turn_id] = handle
        handle._task = asyncio.create_task(
            self._execute(handle, request, resumed=True)
        )
        return handle

    async def execute(self, request: TurnRequest) -> RunResult:
        return await self.submit(request).result()

    async def execute_resume(
        self,
        session_id: str,
        message: str,
        **kwargs: Any,
    ) -> RunResult:
        return await self.resume(session_id, message, **kwargs).result()

    def active_handle(self, turn_id: str) -> RunHandle | None:
        return self._handles.get(turn_id)

    async def _execute(
        self,
        handle: RunHandle,
        request: TurnRequest,
        *,
        resumed: bool,
    ) -> RunResult:
        started = time.perf_counter()
        span_id = self.store.start_span(
            "agent.turn",
            trace_id=handle.turn_id,
            session_id=handle.session_id,
            turn_id=handle.turn_id,
            attributes={
                "platform": request.platform,
                "security_profile": request.security_profile.value,
            },
        )
        from kageha.memory.security import inspect_memory_text

        objective_security = inspect_memory_text(request.objective)
        safe_objective = (
            objective_security.safe_text
            if objective_security.blocked
            else request.objective
        )
        system_security = inspect_memory_text(request.system_extra)
        safe_system_extra = (
            system_security.safe_text
            if system_security.blocked
            else request.system_extra
        )
        workspace = SessionWorkspace.create(handle.session_id)
        reconciled = self.store.reconcile_inflight(handle.session_id)
        uncertain = [
            attempt
            for attempt in reconciled
            if attempt.state.value == "uncertain"
        ]
        if uncertain:
            self.store.append_event(
                session_id=handle.session_id,
                turn_id=handle.turn_id,
                kind=RunEventKind.BLOCKED,
                payload={
                    "status": "reconciliation_required",
                    "reason": (
                        "Interrupted mutating tool outcome is uncertain: "
                        + ", ".join(
                            f"{attempt.tool_name}:{attempt.id}"
                            for attempt in uncertain
                        )
                    ),
                },
                idempotency_key=f"reconciliation:{handle.turn_id}",
            )
            self.store.finish_span(
                span_id,
                status="error",
                attributes={"status": "reconciliation_required"},
            )
            self._handles.pop(handle.turn_id, None)
            raise RuntimeError(
                "A prior mutating tool may have completed before the crash. "
                "Reconcile its external state before resuming."
            )
        journal = ToolJournal(
            self.store,
            session_id=handle.session_id,
            turn_id=handle.turn_id,
        )
        approval_ids: dict[int, str] = {}
        execution_security = ExecutionSecurityPolicy(request.security_profile)

        def approval_audit(approval: Any, decision: str) -> None:
            sandbox = execution_security.assess(
                risk_class=str(approval.risk_class)
            )
            approval_id = self.store.record_approval(
                session_id=handle.session_id,
                turn_id=handle.turn_id,
                action=str(approval.action),
                decision=decision,
                security_profile=request.security_profile.value,
                sandboxed=sandbox.sandboxed,
                detail={
                    "risk_class": approval.risk_class,
                    "detail": approval.detail,
                    "channel_key": request.channel_key,
                },
                approval_id=approval_ids.get(id(approval), ""),
            )
            approval_ids[id(approval)] = approval_id
            if decision == "pending":
                # Expose id on the request so web/channel approvers can wait on it.
                try:
                    setattr(approval, "approval_id", approval_id)
                except Exception:  # noqa: BLE001
                    pass
                self.store.append_event(
                    session_id=handle.session_id,
                    turn_id=handle.turn_id,
                    kind=RunEventKind.APPROVAL_REQUIRED,
                    payload={
                        "approval_id": approval_id,
                        "action": approval.action,
                        "risk_class": approval.risk_class,
                        "detail": str(getattr(approval, "detail", "") or "")[:500],
                    },
                    idempotency_key=f"approval:pending:{approval_id}",
                )
            elif id(approval) in approval_ids and decision in {"approved", "denied"}:
                self.store.append_event(
                    session_id=handle.session_id,
                    turn_id=handle.turn_id,
                    kind=RunEventKind.APPROVAL_RESOLVED,
                    payload={
                        "approval_id": approval_id,
                        "approved": decision == "approved",
                        "reason": decision,
                    },
                    idempotency_key=f"approval:resolved:{approval_id}",
                )

        def event_sink(kind: str, data: dict[str, Any]) -> None:
            self._mirror_controller_event(
                handle=handle,
                workspace=workspace,
                kind=kind,
                data=data,
            )

        controller = self.controller_factory(
            auto_approve=request.auto_approve,
            auto_build=bool(getattr(request, "auto_build", False)),
            approver=request.approver,
            max_steps_limit=request.max_steps,
            memory_user_id=request.user_id,
            memory_agent_id=request.agent_id,
            memory_channel_key=request.channel_key,
            attached_kbs=list(request.knowledge_bases),
            skill_catalog=request.skill_catalog,
            kb_pins=request.kb_pins,
            system_extra=safe_system_extra,
            model_override=request.model_override or None,
            export_dir=Path(request.export_dir) if request.export_dir else None,
            platform=request.platform or "cli",
            live=request.live,
            log_handler=request.log_handler,
            defer_human_input=request.defer_human_input,
            event_sink=event_sink,
            runtime_journal=journal,
            provider_control=self.provider_control,
            security_profile=request.security_profile,
            approval_audit=approval_audit,
            project_root=str(request.project_root or ""),
        )
        handle._controller = controller
        try:
            from kageha.loop.mode_policy import loop_mode_for, normalize_agent_mode

            agent_mode = normalize_agent_mode(
                str(getattr(request, "agent_mode", None) or "normal")
            )
            loop_mode = str(getattr(request, "loop_mode", None) or "").strip().lower()
            if not loop_mode or loop_mode == "auto":
                loop_mode = loop_mode_for(agent_mode)
            if loop_mode == "act":
                loop_mode = "followup"
            if loop_mode not in {"full", "followup"}:
                loop_mode = loop_mode_for(agent_mode)
            # Full-mode resumes reuse workspace plan/goals; followup overlays act steps.
            fresh_turn = not (resumed and loop_mode == "full")
            result = await controller.run(
                safe_objective,
                run_id=handle.session_id,
                workspace=workspace,
                fresh_turn=fresh_turn,
                turn_task=safe_objective if resumed else None,
                loop_mode=loop_mode,
                agent_mode=agent_mode,
            )
            validation_artifacts = (
                list(result.turn_artifacts) if resumed else list(result.artifacts)
            )
            self.store.add_artifacts(
                session_id=handle.session_id,
                turn_id=handle.turn_id,
                workspace=workspace.root,
                paths=validation_artifacts,
            )
            self.store.append_event(
                session_id=handle.session_id,
                turn_id=handle.turn_id,
                kind=RunEventKind.VERIFICATION_STARTED,
                payload={"source": "deterministic_registry"},
                idempotency_key=f"deterministic:start:{handle.turn_id}",
            )
            deterministic = validate_result(
                objective=safe_objective,
                workspace=workspace.root,
                artifacts=validation_artifacts,
            )
            semantic_passed = bool(result.validated)
            result.validated = bool(
                semantic_passed and deterministic.deterministic_passed
            )
            if semantic_passed and not deterministic.deterministic_passed:
                self.telemetry.metric(
                    "turn.false_success_prevented",
                    1,
                    session_id=handle.session_id,
                    turn_id=handle.turn_id,
                )
            if deterministic.evidence:
                result.verification_evidence = "\n".join(
                    [
                        result.verification_evidence,
                        *deterministic.evidence,
                    ]
                ).strip()
            self.store.append_event(
                session_id=handle.session_id,
                turn_id=handle.turn_id,
                kind=RunEventKind.VERIFICATION,
                payload={
                    "status": (
                        "pass"
                        if result.validated
                        else (
                            deterministic.status
                            if not deterministic.deterministic_passed
                            else "semantic_unresolved"
                        )
                    ),
                    "validated": result.validated,
                    "semantic_status": "pass" if semantic_passed else "unresolved",
                    "deterministic_passed": deterministic.deterministic_passed,
                    "checks": deterministic.checks,
                    "defects": deterministic.defects,
                    "artifacts": validation_artifacts,
                },
                idempotency_key=f"deterministic:result:{handle.turn_id}",
            )
            snapshot = self.store.get_snapshot(handle.turn_id)
            if not snapshot.terminal:
                if result.status == "success" and result.validated:
                    self.store.append_event(
                        session_id=handle.session_id,
                        turn_id=handle.turn_id,
                        kind=RunEventKind.COMPLETED,
                        payload={
                            "validated": True,
                            "artifacts": result.artifacts,
                            "steps": result.steps,
                            "usd_spent": result.spent_usd,
                        },
                        idempotency_key=f"terminal:{handle.turn_id}",
                    )
                elif result.status == "cancelled":
                    self.store.append_event(
                        session_id=handle.session_id,
                        turn_id=handle.turn_id,
                        kind=RunEventKind.CANCELLED,
                        payload={"status": result.status},
                        idempotency_key=f"terminal:{handle.turn_id}",
                    )
                else:
                    self.store.append_event(
                        session_id=handle.session_id,
                        turn_id=handle.turn_id,
                        kind=RunEventKind.FAILED,
                        payload={
                            "status": result.status or "invalid_output",
                            "error": result.message,
                            "failure_class": (
                                FailureClass.INVALID_OUTPUT.value
                                if result.status == "success"
                                else FailureClass.UNKNOWN.value
                            ),
                        },
                        idempotency_key=f"terminal:{handle.turn_id}",
                    )
            return result
        except asyncio.CancelledError:
            snapshot = self.store.get_snapshot(handle.turn_id)
            if not snapshot.terminal:
                self.store.append_event(
                    session_id=handle.session_id,
                    turn_id=handle.turn_id,
                    kind=RunEventKind.CANCELLED,
                    payload={"status": "cancelled"},
                    idempotency_key=f"terminal:{handle.turn_id}",
                )
            raise
        except Exception as exc:
            snapshot = self.store.get_snapshot(handle.turn_id)
            if not snapshot.terminal:
                self.store.append_event(
                    session_id=handle.session_id,
                    turn_id=handle.turn_id,
                    kind=RunEventKind.FAILED,
                    payload={
                        "status": "error",
                        "error": str(exc),
                        "failure_class": FailureClass.UNKNOWN.value,
                    },
                    idempotency_key=f"terminal:{handle.turn_id}",
                )
            raise
        finally:
            handle._controller = None
            self._handles.pop(handle.turn_id, None)
            with contextlib.suppress(Exception):
                snapshot = self.store.get_snapshot(handle.turn_id)
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                self.store.finish_span(
                    span_id,
                    status="ok" if snapshot.status == "success" else "error",
                    attributes={
                        "status": snapshot.status,
                        "steps": snapshot.steps_used,
                        "usd_spent": snapshot.usd_spent,
                        "duration_ms": elapsed_ms,
                    },
                )
                self.telemetry.metric(
                    "turn.completed",
                    1,
                    session_id=handle.session_id,
                    turn_id=handle.turn_id,
                    labels={"status": snapshot.status},
                )
                self.telemetry.metric(
                    "turn.latency",
                    elapsed_ms,
                    unit="ms",
                    session_id=handle.session_id,
                    turn_id=handle.turn_id,
                )
                self.telemetry.metric(
                    "turn.cost",
                    snapshot.usd_spent,
                    unit="USD",
                    session_id=handle.session_id,
                    turn_id=handle.turn_id,
                )

    def _mirror_controller_event(
        self,
        *,
        handle: RunHandle,
        workspace: SessionWorkspace,
        kind: str,
        data: dict[str, Any],
    ) -> None:
        snapshot = self.store.get_snapshot(handle.turn_id)
        if snapshot.terminal:
            return
        mapped: RunEventKind | None = None
        payload = dict(data)
        if kind == "run_start":
            mapped = RunEventKind.PLANNING_STARTED
        elif kind == "plan":
            mapped = RunEventKind.PLANNED
            plan = _read_json(workspace.root / "plan.json")
            goal = _read_json(workspace.root / "goal_card.json")
            plan_md = ""
            try:
                plan_md = (workspace.root / "plan.md").read_text(encoding="utf-8")[
                    :6000
                ]
            except OSError:
                pass
            payload = {
                **payload,
                "version": snapshot.plan_version + 1,
                "plan": list(plan.get("steps") or []),
                "goals": list(goal.get("items") or []),
                "plan_md": plan_md,
                "current_stage": (
                    str((plan.get("steps") or [{}])[0].get("id") or "")
                    if plan.get("steps")
                    else ""
                ),
            }
        elif kind == "plan_approval_required":
            # Progress only — Approve/Deny comes from approval_audit via
            # ApprovalGate.require_explicit (has approval_id). Mapping this
            # to APPROVAL_REQUIRED without an id left the UI "waiting" with
            # no controls.
            mapped = RunEventKind.PROGRESS
            payload = {
                **payload,
                "message": "Plan ready — waiting for Build/Approve",
                "action": "approve_plan",
                "risk_class": "plan",
            }
        elif kind == "control" and "validation" in data:
            mapped = RunEventKind.VERIFICATION
            state = _read_json(workspace.root / "task_state.json")
            payload = {
                **payload,
                "status": data.get("validation") or "unknown",
                "validated": data.get("validation") == "pass",
                "goals": list(state.get("goals") or snapshot.goals),
                "artifacts": list(state.get("artifacts") or []),
            }
        elif kind == "checkpoint":
            mapped = RunEventKind.CHECKPOINT
        elif kind == "todo_board":
            mapped = RunEventKind.TODO_BOARD
            board = _todo_board_payload(workspace, data=data)
            if board is not None:
                payload = board
        elif kind == "user_input_requested":
            mapped = RunEventKind.APPROVAL_REQUIRED
            payload["action"] = data.get("question") or "user_input"
        elif kind == "run_end":
            return
        elif kind in {
            "model",
            "context",
            "task_state",
            "monitor",
            "tool_guardrail",
            "goal_todo_sync",
            "model_failover",
            "design_explore_start",
            "design_explore_step",
            "design_explore_done",
            "design_explore_failover",
            "design_explore_error",
            "design_artifacts",
        }:
            mapped = RunEventKind.PROGRESS
            # Keep a stable source kind for WebUI toast/banner routing.
            payload.setdefault("source_kind", kind)
            if kind == "model_failover":
                frm = str(payload.get("from") or "?")
                to = str(payload.get("to") or "?")
                payload.setdefault("message", f"Model: {frm} → {to}")
            elif kind == "design_explore_start":
                payload.setdefault("message", "Explore…")
            elif kind == "design_explore_error":
                err = str(payload.get("error") or "explore failed")
                payload.setdefault(
                    "message", f"Explore skipped: {err}"[:400]
                )
                payload["degraded"] = True
            elif kind == "design_explore_failover":
                frm = str(payload.get("from") or "?")
                to = str(payload.get("to") or "?")
                payload.setdefault("message", f"Explore: {frm} → {to}")
        if mapped is None:
            return
        with contextlib.suppress(Exception):
            self.store.append_event(
                session_id=handle.session_id,
                turn_id=handle.turn_id,
                kind=mapped,
                payload=payload,
                idempotency_key=(
                    f"mirror:{handle.turn_id}:{kind}:"
                    f"{uuid.uuid5(uuid.NAMESPACE_OID, json.dumps(data, sort_keys=True, default=str))}"
                ),
            )
        # After checkpoint / goal sync, also push a fresh board snapshot.
        if kind in {"checkpoint", "goal_todo_sync"}:
            board = _todo_board_payload(workspace)
            if board is not None:
                with contextlib.suppress(Exception):
                    self.store.append_event(
                        session_id=handle.session_id,
                        turn_id=handle.turn_id,
                        kind=RunEventKind.TODO_BOARD,
                        payload=board,
                        idempotency_key=(
                            f"mirror:{handle.turn_id}:todo_board_after:{kind}:"
                            f"{uuid.uuid5(uuid.NAMESPACE_OID, json.dumps(data, sort_keys=True, default=str))}"
                        ),
                    )


def _todo_board_payload(
    workspace: SessionWorkspace,
    data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Prefer structured sink payload; else parse workspace todo.md."""
    from kageha.loop.todo_board import parse_todo_file, parse_todo_markdown

    raw = data if isinstance(data, dict) else {}
    items = raw.get("items")
    if isinstance(items, list) and raw.get("total") is not None:
        return {
            "label": str(raw.get("label") or "todos"),
            "done": int(raw.get("done") or 0),
            "total": int(raw.get("total") or 0),
            "items": [
                {
                    "id": str(it.get("id") or ""),
                    "text": str(it.get("text") or ""),
                    "done": bool(it.get("done")),
                }
                for it in items
                if isinstance(it, dict)
            ][:24],
        }
    if isinstance(raw.get("markdown"), str) and raw["markdown"].strip():
        board = parse_todo_markdown(
            raw["markdown"],
            label=str(raw.get("label") or "todos"),
        )
        return board if board.get("total") else None
    return parse_todo_file(workspace.root / "todo.md", label=str(raw.get("label") or "todos"))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:  # noqa: BLE001
        return {}
