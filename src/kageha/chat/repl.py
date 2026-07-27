"""Interactive multi-turn chat — same session workspace across follow-ups."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from kageha.chat.line_edit import remember, setup_line_editing
from kageha.chat.present import format_chat_reply, print_chat_reply
from kageha.chat.quick import (
    answer_before_workspace,
    answer_status,
    answer_where,
    is_where_question,
)
from kageha.chat.progress import TransientProgress
from kageha.chat.turn_manager import (
    build_turn_context,
    classify_turn,
    expand_user_message,
    ground_artifact_followup,
    new_task_prompt,
    persist_turn_decision,
    prefer_loop_mode,
    route_for_decision,
    resolve_artifact_references,
)
from kageha.config import security_profile
from kageha.harness.sandbox import SessionWorkspace
from kageha.knowledge.registry import attached_kbs
from kageha.loop.artifacts import (
    artifacts_touched_since,
    classify_artifacts,
    humanize_turn_reply,
    snapshot_artifact_mtimes,
)
from kageha.loop.task_state import TaskState
from kageha.memory.models import TurnMemoryInput
from kageha.memory.service import (
    get_memory_service,
    memory_enabled,
    memory_learning_enabled,
    turn_memory_input_from_result,
)
from kageha.memory.skills import SkillRegistry


HELP = """
Just talk — ask, request work, or refine what we made.

Commands:
  /help  /where  /files  /status  /sessions
  /resume <id>   /new
  /model [list|reset|<id>|planner <id>|executor <id>]
  /comet [start|status]      Launch/check logged-in browser
  /browser [list|use <be>|comet|cdp <url>|status]
  /computer [status|doctor|pack on|off|auto|allowlist]
  /research [flash|standard|deep] <query>
  /permissions [auto|ask]
  /memory status|list|why|on|off|learn|remember|correct|forget
  /project:<name>  /cmd <name>   Project recipes from .kageha/commands/
  /best-of-n <objective>         Parallel worktree attempts
  /verbose       Live reasoning, todo ✓/○ checklists, tools, session meta
  /quiet         Compact status only (default)
  /voice         Record mic → STT for next turn (needs sox/ffmpeg + STT key)
  /quit

↑/↓ history · Tab completes /commands
""".strip()


def _permissions_status(auto_approve: bool) -> str:
    return "Approvals: auto" if auto_approve else "Approvals: ask before risky tools"


def _apply_permissions(arg: str, *, auto_approve: bool) -> tuple[bool, str]:
    """Parse /permissions [mode]. Returns (new_flag, message)."""
    token = (arg or "").strip().lower()
    if not token:
        return auto_approve, _permissions_status(auto_approve)
    if token in {"auto", "on", "yes", "true", "1", "allow"}:
        return True, _permissions_status(True)
    if token in {"ask", "off", "no", "false", "0", "prompt", "hitl"}:
        return False, _permissions_status(False)
    return auto_approve, "Usage: /permissions [auto|ask]\n" + _permissions_status(auto_approve)


def list_sessions(limit: int = 20) -> list[dict[str, str]]:
    """List journal-backed sessions."""
    from kageha.runtime import RuntimeStore

    store = RuntimeStore()
    try:
        rows = store.list_sessions(limit)
    finally:
        store.close()
    return [
        {
            "run_id": str(row["id"]),
            "task": str(row["objective"])[:120] or "(no objective)",
            "status": str(row["turn_status"] or row["status"]),
            "mtime": datetime.fromtimestamp(
                float(row["updated_at"]), tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC"),
        }
        for row in rows
    ]


def _append_chat_log(ws: SessionWorkspace, role: str, text: str) -> None:
    path = ws.root / "chat.jsonl"
    rec = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "role": role,
        "text": text[:8000],
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


async def run_chat_repl(
    *,
    resume: str | None = None,
    auto_approve: bool = False,
    max_steps: int | None = None,
    quiet: bool = False,
    kb: list[str] | None = None,
    voice: bool = False,
    project_root: str | None = None,
    attach: str | None = None,
) -> None:
    """Readline-style loop: each user message continues the same session."""
    # Redundant with LoopController.defer_human_input by design: third-party
    # entry-point tool packs must also know that chat clarifications are turns,
    # never nested TTY reads.
    os.environ["KAGEHA_CHAT_MODE"] = "1"
    cwd = (project_root or os.getcwd()).strip() or os.getcwd()
    if attach:
        from kageha.chat.remote_turn import remote_ping

        try:
            await remote_ping(attach)
            print(f"[kageha] attached App Server ({attach})")
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: could not attach to App Server: {exc}")
            return
    kbs = list(kb or []) + attached_kbs()
    skills = SkillRegistry()
    catalog = skills.catalog(limit=40)
    pins = ", ".join(kbs) if kbs else ""
    memory = get_memory_service(start_worker=True)
    from kageha.chat.memory_commands import ChatMemorySettings

    memory_settings = ChatMemorySettings(
        enabled=memory_enabled(),
        learning=memory_learning_enabled(),
    )
    from kageha.runtime import AgentRuntime

    durable_runtime = AgentRuntime()

    def open_workspace(session_id: str) -> SessionWorkspace:
        # Opening must not manufacture an unknown durable session.
        durable_runtime.store.inspect_session(session_id)
        return SessionWorkspace.create(session_id)

    run_id: str | None = resume
    workspace: SessionWorkspace | None = None
    # Mutable for /permissions during the session (CLI --auto-approve is the start value).
    approve_all = bool(auto_approve)
    # Session model pin (also persisted on workspace when available).
    model_override: str | None = None
    model_once: str | None = None
    model_role_overrides: dict[str, str] = {}
    # Manus-style: conversation first. /verbose reveals routing + session meta.
    verbose = False
    voice_mode = bool(voice)
    if resume:
        workspace = open_workspace(resume)
        model_override = workspace.get_model_override()
        model_once = workspace.get_model_once()
        model_role_overrides = workspace.get_model_role_overrides()
        print(f"Resumed · {resume}")
    else:
        print("Kageha chat · type a request, or /help")
    if voice_mode:
        print(
            "Voice mode on — empty Enter records mic; type to send text. "
            "Toggle with /voice. Replies spoken when KAGEHA_VOICE_REPLY=1."
        )

    setup_line_editing()
    print(_permissions_status(approve_all))
    if model_override or model_once or model_role_overrides:
        from kageha.chat.model_commands import model_status

        print(
            model_status(
                model_override,
                once=model_once,
                role_overrides=model_role_overrides,
            )
        )
    print()

    while True:
        try:
            prompt = "you> " if not voice_mode else "you/mic> "
            line = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            durable_runtime.close()
            break

        if voice_mode and not line:
            from kageha.chat.voice_io import listen_once

            try:
                line = await listen_once()
            except Exception as exc:  # noqa: BLE001
                print(f"Voice capture failed: {exc}")
                continue
            if not line:
                print("(empty transcription)")
                continue
            print(f"you> {line}")

        if not line:
            continue
        remember(line)

        low = line.lower()
        if low in {"/quit", "/exit", ":q", "quit", "exit"}:
            print("Bye.")
            durable_runtime.close()
            break
        if low in {"/", "/help", "help", "?"}:
            print(HELP)
            continue
        if low == "/voice":
            voice_mode = not voice_mode
            print("Voice mode on." if voice_mode else "Voice mode off.")
            continue
        if low == "/verbose":
            verbose = True
            print(
                "Verbose on — reasoning traces, todo checklists, and tool steps "
                "will print as they happen."
            )
            continue
        if low == "/quiet":
            verbose = False
            print("Quiet on — compact status only.")
            continue
        if low == "/new":
            run_id = None
            workspace = None
            model_override = None
            model_once = None
            model_role_overrides = {}
            print("Ready for a new task.")
            continue
        if low == "/model" or low.startswith("/model ") or low in {"/models"} or low.startswith(
            "/models "
        ):
            from kageha.chat.model_commands import handle_model_command

            result = handle_model_command(
                line,
                override=model_override,
                once=model_once,
                role_overrides=model_role_overrides,
                workspace=workspace,
            )
            if result.handled:
                if result.changed:
                    model_override = result.override
                    model_once = result.once
                    model_role_overrides = dict(result.role_overrides)
                print(result.message)
                continue
        if low == "/permissions" or low.startswith("/permissions "):
            arg = line.split(maxsplit=1)[1] if " " in line else ""
            approve_all, msg = _apply_permissions(arg, auto_approve=approve_all)
            print(msg)
            continue
        if low == "/comet" or low.startswith("/comet "):
            from kageha.chat.comet import handle_comet_command

            handled, message = await handle_comet_command(line)
            if handled:
                print_chat_reply(message)
                continue
        if (
            low == "/browser"
            or low.startswith("/browser ")
            or low == "/research"
            or low.startswith("/research ")
        ):
            from kageha.chat.browser_commands import handle_browser_or_research

            handled, message = await handle_browser_or_research(line)
            if handled:
                print_chat_reply(message)
                continue
        if low == "/computer" or low.startswith("/computer "):
            from kageha.chat.computer_commands import handle_computer_command

            handled, message = await handle_computer_command(line)
            if handled:
                print_chat_reply(message)
                continue
        if low == "/memory" or low.startswith("/memory "):
            from kageha.chat.memory_commands import handle_memory_command

            handled, message = handle_memory_command(
                line,
                service=memory,
                settings=memory_settings,
                session_id=run_id or "",
                project_root=cwd,
            )
            if handled:
                print(message)
                continue
        if low in {"/status", "/where"}:
            if workspace and run_id:
                if low == "/where":
                    print_chat_reply(answer_where(workspace))
                else:
                    from kageha.chat.model_commands import model_status

                    print(f"Session {run_id}")
                    print(workspace.root)
                    print(_permissions_status(approve_all))
                    print(
                        model_status(
                            model_override or workspace.get_model_override(),
                            once=model_once or workspace.get_model_once(),
                            role_overrides=model_role_overrides
                            or workspace.get_model_role_overrides(),
                        )
                    )
            else:
                print_chat_reply(answer_before_workspace(low))
            continue
        if low == "/files":
            if not workspace:
                print_chat_reply(answer_before_workspace(low))
                continue
            print_chat_reply(answer_where(workspace))
            continue
        if low == "/sessions":
            rows = [
                {
                    "run_id": row["id"],
                    "mtime": f"{row['updated_at']:.3f}",
                    "status": row["turn_status"] or row["status"],
                    "task": str(row["objective"])[:120],
                }
                for row in durable_runtime.store.list_sessions(15)
            ]
            if not rows:
                print("No sessions yet.")
                continue
            for r in rows:
                print(f"{r['run_id']}  {r['mtime']}  [{r['status']}]  {r['task']}")
            continue
        if low.startswith("/resume"):
            parts = line.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                print("usage: /resume <run_id>")
                continue
            rid = parts[1].strip()
            try:
                workspace = open_workspace(rid)
            except FileNotFoundError as e:
                print(f"ERROR: {e}")
                continue
            run_id = rid
            model_override = workspace.get_model_override()
            model_once = workspace.get_model_once()
            model_role_overrides = workspace.get_model_role_overrides()
            print(f"Resumed · {rid}")
            print_chat_reply(answer_where(workspace))
            if model_override or model_once or model_role_overrides:
                from kageha.chat.model_commands import model_status

                print(
                    model_status(
                        model_override,
                        once=model_once,
                        role_overrides=model_role_overrides,
                    )
                )
            continue
        if (
            low.startswith("/project:")
            or low.startswith("/cmd ")
            or low == "/best-of-n"
            or low.startswith("/best-of-n ")
        ):
            from kageha.chat.project_commands import handle_project_command

            handled, message = handle_project_command(
                line, project_root=cwd
            )
            if handled:
                if message.startswith("__BEST_OF_N__:"):
                    from kageha.project.best_of_n import format_best_of_n, run_best_of_n

                    objective = message.split(":", 1)[1]
                    print(f"[kageha] best-of-n → {objective[:120]}")
                    try:
                        result = await run_best_of_n(
                            objective=objective,
                            project_root=cwd,
                            n=2,
                            auto_approve=approve_all,
                        )
                        print_chat_reply(format_best_of_n(result))
                    except Exception as exc:  # noqa: BLE001
                        print(f"ERROR: {exc}")
                    continue
                # Expand project slash recipe into the user turn.
                line = message
                low = line.lower()
        if line.startswith("/"):
            print(f"Unknown command: {line}  (/help)")
            continue

        try:
            explicit_memory = memory.apply_explicit_user_action(
                line,
                session_id=run_id or "chat-unbound",
                project_root=cwd,
            )
        except (RuntimeError, ValueError, KeyError) as exc:
            print_chat_reply(f"Memory error: {exc}")
            continue
        if explicit_memory is not None:
            print_chat_reply(
                f"Memory updated ({explicit_memory.id}, {explicit_memory.state}): "
                f"{explicit_memory.content}"
            )
            continue

        # --- turn manager: classify before agent loop ---
        turn_ctx = build_turn_context(workspace)
        decision = await classify_turn(line, turn_ctx)
        route = route_for_decision(
            decision,
            has_session=bool(run_id and workspace),
            message=line,
            turn_ctx=turn_ctx,
        )
        # Expand "try" / "do it" using the prior actionable ask.
        agent_line = expand_user_message(line, turn_ctx) if route in {
            "resume",
            "new_run",
            "first_run",
        } else line
        if route == "resume":
            referenced = resolve_artifact_references(
                agent_line,
                turn_ctx,
                preferred=decision.reuse_artifacts,
            )
            if referenced:
                agent_line = ground_artifact_followup(agent_line, referenced)
        persist_turn_decision(
            workspace, decision, message=line, route=route
        )
        from kageha.chat.turn_manager import prefer_agent_mode

        agent_mode = prefer_agent_mode(line, workspace=workspace)
        loop_mode = prefer_loop_mode(
            line,
            decision,
            route=route,
            workspace=workspace,
            agent_mode=agent_mode,
        )
        if verbose:
            print(
                f"[turn] {decision.intent} "
                f"(route={route}, mode={agent_mode}, loop={loop_mode}, "
                f"source={decision.source}) — {decision.reason}"
            )

        correction = memory.apply_natural_correction(
            line,
            session_id=run_id or "",
            project_root=cwd,
        )
        if isinstance(correction, list) and route in {
            "quick_where",
            "quick_status",
        }:
            print_chat_reply(
                "That could refer to more than one recalled memory. I suppressed them "
                "for now. Tell me which ID to correct: " + ", ".join(correction)
            )
            continue
        if correction is not None and not isinstance(correction, list) and route in {
            "quick_where",
            "quick_status",
        }:
            print_chat_reply(
                f"Got it — I retracted that memory ({correction.id}). "
                "Tell me the correct version if you want it replaced."
            )
            continue

        if route == "cancel":
            if workspace:
                _append_chat_log(workspace, "user", line)
                reply = "Okay — stopped. Send a new task whenever you're ready."
                _append_chat_log(workspace, "assistant", reply)
            else:
                reply = "Okay — nothing in progress."
            if memory_settings.enabled:
                memory.capture_turn(
                    TurnMemoryInput(
                        session_id=run_id or "chat-unbound",
                        turn_id=uuid.uuid4().hex,
                        task=line,
                        user_text=line,
                        assistant_text=reply,
                        status="cancelled",
                        verified=False,
                        project_root=cwd,
                        learn=memory_settings.learning,
                    )
                )
            print_chat_reply(reply)
            continue

        if route == "quick_remote":
            from kageha.chat.quick_remote import execute_quick_remote, should_quick_remote

            if workspace:
                _append_chat_log(workspace, "user", line)
            action = should_quick_remote(line, turn_ctx)
            reply = await execute_quick_remote(
                line, action=action, auto_approve=bool(auto_approve)
            )
            if workspace:
                _append_chat_log(workspace, "assistant", reply)
            if memory_settings.enabled:
                memory.capture_turn(
                    TurnMemoryInput(
                        session_id=run_id or "chat-unbound",
                        turn_id=uuid.uuid4().hex,
                        task=line,
                        user_text=line,
                        assistant_text=reply,
                        status="success",
                        verified=True,
                        project_root=cwd,
                        learn=memory_settings.learning,
                    )
                )
            print_chat_reply(format_chat_reply(text=reply))
            continue

        if route in {"quick_where", "quick_status"}:
            if not (run_id and workspace):
                if route == "quick_where" or is_where_question(line):
                    reply = "There aren't any files yet because no task has started."
                else:
                    reply = answer_before_workspace("/status")
                if memory_settings.enabled:
                    memory.capture_turn(
                        TurnMemoryInput(
                            session_id="chat-unbound",
                            turn_id=uuid.uuid4().hex,
                            task=line,
                            user_text=line,
                            assistant_text=reply,
                            status="success",
                            verified=True,
                            project_root=cwd,
                            learn=memory_settings.learning,
                        )
                    )
                print_chat_reply(format_chat_reply(text=reply))
                continue
            _append_chat_log(workspace, "user", line)
            if route == "quick_where" or is_where_question(line):
                reply = answer_where(workspace)
            else:
                reply = answer_status(workspace)
            _append_chat_log(workspace, "assistant", reply)
            if memory_settings.enabled:
                memory.capture_turn(
                    TurnMemoryInput(
                        session_id=run_id,
                        turn_id=uuid.uuid4().hex,
                        task=line,
                        user_text=line,
                        assistant_text=reply,
                        status="success",
                        verified=True,
                        project_root=cwd,
                        learn=memory_settings.learning,
                    )
                )
            print_chat_reply(format_chat_reply(text=reply))
            continue

        # --- agent turn (model chooses 0..N tool steps) ---
        from kageha.chat.history import session_continuity_extra

        memory_extra = ""
        if memory_settings.enabled:
            from kageha.memory.bootstrap import prepare_turn_memory

            memory_extra = prepare_turn_memory(
                memory,
                query=agent_line,
                project_root=cwd,
                session_id=run_id or "",
                trace_root=str(workspace.root) if workspace else "",
            )
        if isinstance(correction, list):
            memory_extra = (
                memory_extra
                + "\n\nThe user's correction matched multiple recalled memories, "
                f"which were quarantined: {', '.join(correction)}. Ask one concise "
                "clarifying question to identify the intended claim."
            ).strip()
        if workspace:
            cont = session_continuity_extra(workspace, current_user=line)
            if cont:
                memory_extra = (memory_extra + "\n\n" + cont).strip()
        if run_id and workspace and route in {"resume", "new_run", "first_run"}:
            memory_extra += (
                "\n\nYou are in interactive chat with a full tool + skill harness. "
                "Be concise and human. Lead with absolute file paths when "
                "reporting deliverables. Avoid markdown tables unless asked. "
                "You HAVE browser_* (if pack enabled), skill_run for network_scan / "
                "sony_bravia / android_tv, "
                "bash/shell, skill_load/skill_run, and can write/execute scripts. "
                "Never claim you cannot open a browser, scan the LAN, or control "
                "a paired device when those tools/skills exist — use them (or "
                "load the matching skill) instead of refusing. "
                "If unsure whether you can help, try the tools. "
                "Remember facts and decisions from the recent conversation above."
            )
        try:
            before_arts = (
                classify_artifacts(workspace.list_files()) if workspace else []
            )
            before_tool_results = 0
            if workspace:
                try:
                    before_state = TaskState.load(workspace.path("task_state.json"))
                    before_tool_results = len(before_state.tool_results)
                except Exception:  # noqa: BLE001
                    pass
            before_mtimes = (
                snapshot_artifact_mtimes(workspace.root, before_arts)
                if workspace
                else {}
            )
            with TransientProgress(
                enabled=not quiet,
                detailed=verbose,
            ) as progress:
                from kageha.runtime import SecurityProfile, TurnRequest

                if route == "resume" and run_id and workspace:
                    _append_chat_log(workspace, "user", line)
                    objective = agent_line
                    use_existing = True
                else:
                    prior_id = run_id if route == "new_run" else None
                    objective = new_task_prompt(
                        agent_line,
                        prior_run_id=prior_id,
                        reuse_artifacts=decision.reuse_artifacts,
                    )
                    use_existing = bool(prior_id and workspace)
                    if use_existing:
                        assert workspace is not None
                        _append_chat_log(
                            workspace,
                            "system",
                            f"new task turn in session {prior_id} "
                            f"(discard_old_plan={decision.discard_old_plan})",
                        )
                request_args = {
                    "user_id": "local",
                    "agent_id": "main",
                    "project_root": cwd,
                    "auto_approve": approve_all,
                    "security_profile": SecurityProfile(security_profile()),
                    "max_steps": max_steps or 40,
                    "knowledge_bases": tuple(kbs),
                    "skill_catalog": catalog,
                    "kb_pins": pins,
                    "system_extra": memory_extra,
                    "model_override": model_override or "",
                    "live": not quiet,
                    "log_handler": progress.update,
                    "defer_human_input": True,
                    "platform": "cli",
                    # Codex-style: act/followup by default; full for plan/spec/goal.
                    "loop_mode": loop_mode,
                    "agent_mode": agent_mode,
                }
                if attach:
                    from kageha.chat.remote_turn import remote_turn
                    from types import SimpleNamespace

                    remote = await remote_turn(
                        attach=attach,
                        message=objective,
                        thread_id=f"chat-{run_id or 'new'}",
                        session_id=run_id if use_existing else None,
                        project_root=cwd,
                        auto_approve=approve_all,
                        agent_mode=agent_mode,
                        loop_mode=loop_mode,
                        max_steps=int(max_steps or 40),
                    )
                    result = SimpleNamespace(
                        run_id=str(
                            remote.get("run_id")
                            or remote.get("session_id")
                            or run_id
                            or ""
                        ),
                        status=str(remote.get("status") or "success"),
                        message=str(remote.get("message") or ""),
                        steps=int(remote.get("steps") or 0),
                        spent_usd=float(remote.get("spent_usd") or 0),
                        artifacts=list(remote.get("artifacts") or []),
                        turn_id=str(remote.get("turn_id") or ""),
                        turn_artifacts=list(
                            remote.get("turn_artifacts")
                            or remote.get("artifacts")
                            or []
                        ),
                        validated=bool(remote.get("validated")),
                        verified_facts=list(remote.get("verified_facts") or []),
                        verification_evidence=str(
                            remote.get("verification_evidence") or ""
                        ),
                        recovered_failures=list(
                            remote.get("recovered_failures") or []
                        ),
                        active_skills=list(remote.get("active_skills") or []),
                    )
                elif use_existing and run_id:
                    result = await durable_runtime.execute_resume(
                        run_id,
                        objective,
                        **request_args,
                    )
                else:
                    result = await durable_runtime.execute(
                        TurnRequest(objective=objective, **request_args)
                    )
                run_id = result.run_id
                if not run_id:
                    raise RuntimeError("turn completed without a run_id")
                workspace = open_workspace(run_id)
                if model_override and workspace.get_model_override() != model_override:
                    workspace.set_model_override(model_override)
                if model_role_overrides and (
                    workspace.get_model_role_overrides() != model_role_overrides
                ):
                    workspace.set_model_role_overrides(model_role_overrides)
                if route != "resume":
                    _append_chat_log(workspace, "user", line)
                persist_turn_decision(
                    workspace, decision, message=line, route=route
                )

            if memory_settings.enabled:
                memory.capture_turn(
                    turn_memory_input_from_result(
                        result,
                        task=line,
                        user_text=line,
                        project_root=cwd,
                        learn=memory_settings.learning,
                    )
                )
            all_arts = classify_artifacts(workspace.list_files())
            result_evidence = ""
            try:
                persisted_state = TaskState.load(workspace.path("task_state.json"))
                # A session can contain many unrelated turns. Never surface
                # evidence produced before this user message.
                start = max(
                    before_tool_results,
                    persisted_state.turn_tool_result_start,
                )
                current_turn_results = persisted_state.tool_results[start:]
                for note in reversed(current_turn_results):
                    if note.ok and note.tool not in {
                        "todo_write", "write_file", "bash", "read_file"
                    }:
                        result_evidence = note.summary
                        break
            except Exception:  # noqa: BLE001
                pass
            reply_blob = "\n".join(
                [
                    result.message or "",
                    result.status or "",
                    result_evidence or "",
                ]
            )
            # Include overwrites + paths named in the model reply / tool notes
            new_arts = (
                classify_artifacts(result.turn_artifacts)
                if result.turn_artifacts
                else artifacts_touched_since(
                    workspace.root,
                    all_arts,
                    before_mtimes,
                    also_mention=reply_blob,
                )
            )
            highlight = new_arts if before_mtimes else all_arts[:6]
            summary = humanize_turn_reply(
                message=result.message or "",
                status=result.status,
                user_line=line,
                new_artifacts=highlight,
                workspace_root=workspace.root,
                result_evidence=result_evidence,
            )
            chat_text = format_chat_reply(
                text=summary,
                files=highlight,
                workspace_root=workspace.root,
                max_files=3,
            )
            _append_chat_log(workspace, "assistant", chat_text)
            print_chat_reply(chat_text)
            if voice_mode:
                from kageha.chat.voice_io import (
                    play_audio,
                    synthesize_reply_wav,
                    voice_reply_enabled,
                )

                if voice_reply_enabled():
                    try:
                        wav = workspace.root / "artifacts" / "voice_reply.wav"
                        await synthesize_reply_wav(chat_text, wav)
                        play_audio(wav)
                    except Exception as exc:  # noqa: BLE001
                        print(f"(voice reply skipped: {exc})")
            if verbose:
                print(
                    f"[{result.run_id} · {result.status} · "
                    f"{result.steps} steps · ~${result.spent_usd:.3f}]"
                )
                print()
            if not approve_all:
                from kageha.memory.learning_loop import maybe_prompt_skill_distill

                maybe_prompt_skill_distill(
                    result,
                    task=line,
                    registry=SkillRegistry(),
                    interactive=True,
                )
        except Exception as e:  # noqa: BLE001
            if memory_settings.enabled:
                memory.capture_turn(
                    TurnMemoryInput(
                        session_id=run_id or "chat-unbound",
                        turn_id=uuid.uuid4().hex,
                        task=line,
                        user_text=line,
                        assistant_text=str(e),
                        status="error",
                        verified=False,
                        project_root=cwd,
                        learn=memory_settings.learning,
                    )
                )
            print(f"Something went wrong: {e}")
