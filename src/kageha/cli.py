"""Kageha CLI — chat, run, webui, models, skills, mcp, memory, jobs."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Optional

import typer

from kageha import __version__
from kageha.config import load_env

load_env()

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help=(
        "Kageha agent kernel — chat/run/webui + MCP/skills/memory. "
        "Optional packs via KAGEHA_TOOL_PACKS. Background work: kageha jobs."
    ),
)
models_app = typer.Typer(help="Model registry")
models_auth_app = typer.Typer(help="Subscription / OAuth model auth (ChatGPT, Gemini CLI)")
skills_app = typer.Typer(help="Skills (agentskills.io / Anthropic compatible)")
mcp_app = typer.Typer(help="Model Context Protocol servers")
memory_app = typer.Typer(help="Inspect and manage provenance-aware memory")
runtime_app = typer.Typer(help="Inspect and repair the durable runtime")
worktree_app = typer.Typer(help="Git worktree isolation for parallel agents")
jobs_app = typer.Typer(help="Durable background jobs")
project_app = typer.Typer(help="Project brain, rules, and hooks")
browser_app = typer.Typer(
    help="Browser / research backend (/browser · /research in chat)",
    invoke_without_command=True,
)
computer_app = typer.Typer(
    help="macOS computer-use (/computer in chat)",
    invoke_without_command=True,
)
app.add_typer(models_app, name="models")
models_app.add_typer(models_auth_app, name="auth")
app.add_typer(skills_app, name="skills")
app.add_typer(mcp_app, name="mcp")
app.add_typer(memory_app, name="memory")
app.add_typer(runtime_app, name="runtime")
app.add_typer(worktree_app, name="worktree")
app.add_typer(jobs_app, name="jobs")
app.add_typer(project_app, name="project")
app.add_typer(browser_app, name="browser")
app.add_typer(computer_app, name="computer")


@app.callback()
def main_callback() -> None:
    """Kageha — loop + harness + memory."""


@app.command("version")
def version_cmd() -> None:
    typer.echo(__version__)


@app.command("run")
def run_cmd(
    task: Optional[str] = typer.Argument(
        None, help="Task for the agent (optional with --resume)"
    ),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Skip HITL prompts"),
    resume: Optional[str] = typer.Option(
        None,
        "--resume",
        help="Continue an existing session by run_id (same workspace). Prefer: kageha chat --resume",
    ),
    quiet: bool = typer.Option(False, "--quiet", help="Hide live step/tool progress"),
    max_steps: Optional[int] = typer.Option(
        None,
        "--max-steps",
        help="Loop step ceiling (default: KAGEHA_MAX_STEPS or 40). Not the plan length.",
    ),
    output_dir: Optional[Path] = typer.Option(
        None,
        "--output-dir",
        help=(
            "Export generated files to this directory after the run. "
            "Use '.' to materialize requested relative paths in the current project."
        ),
    ),
    security: Optional[str] = typer.Option(
        None,
        "--security-profile",
        help="approval_fallback (default) | strict",
    ),
    sandbox: Optional[str] = typer.Option(
        None,
        "--sandbox",
        help="Shell isolation for this process: auto|off|docker|bwrap|seatbelt|ssh|modal",
    ),
    project: Path = typer.Option(
        Path.cwd(), "--project", "-C", help="Project root for AGENTS.md / rules / tools"
    ),
    attach: Optional[str] = typer.Option(
        None,
        "--attach",
        "-a",
        help=(
            "Run via App Server: start `kageha server --listen unix://` (or ws://), "
            "then pass auto | unix://… | ws://127.0.0.1:PORT. "
            "ws:// needs: uv sync --extra server-ws"
        ),
    ),
    agent_mode: Optional[str] = typer.Option(
        None,
        "--mode",
        help="normal | plan | goal (default: normal; /plan|/goal in the task selects mode)",
    ),
    build: bool = typer.Option(
        False,
        "--build",
        help="Plan: approve the design and execute (Build). "
        "Without this, plan stops after writing the plan artifact.",
    ),
) -> None:
    """Run the agent loop on a task (one-shot). For follow-ups use `kageha chat`."""
    from kageha.memory.models import TurnMemoryInput
    from kageha.memory.service import (
        get_memory_service,
        turn_memory_input_from_result,
    )
    from kageha.memory.skills import SkillRegistry
    from kageha.config import apply_sandbox_cli, security_profile
    from kageha.loop.mode_policy import (
        loop_mode_for,
        resolve_agent_mode,
    )

    try:
        selected_sandbox = apply_sandbox_cli(sandbox)
    except ValueError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if sandbox:
        typer.echo(f"[kageha] sandbox={selected_sandbox}", err=True)

    if not resume and not task:
        typer.echo("ERROR: provide a task, or use --resume RUN_ID", err=True)
        raise typer.Exit(code=2)

    # Slash in the task wins over a missing --mode; explicit --mode still wins
    # when there is no slash (resolve_agent_mode: slash → explicit → normal).
    mode = resolve_agent_mode(task or "", explicit=agent_mode)

    if attach:
        from kageha.chat.remote_turn import remote_turn

        result = asyncio.run(
            remote_turn(
                attach=attach,
                message=task or f"resume:{resume}",
                session_id=resume,
                project_root=str(project.resolve()),
                auto_approve=auto_approve,
                agent_mode=mode,
                loop_mode=loop_mode_for(mode),
                max_steps=int(max_steps or (40 if mode != "normal" else 24)),
                auto_build=build,
            )
        )
        typer.echo(json.dumps(result, indent=2, default=str))
        status = str(result.get("status") or "").lower()
        if status and status not in {
            "success",
            "ok",
            "completed",
            "awaiting_plan_approval",
            "awaiting_clarify",
        }:
            raise typer.Exit(code=1)
        return

    selected_security = security_profile(security)
    skills = SkillRegistry()
    task_text = (task or "").strip()
    memory = get_memory_service()
    try:
        explicit_memory = memory.apply_explicit_user_action(
            task_text,
            session_id=resume or "oneshot-explicit",
            project_root=str(project.resolve()),
        )
    except (RuntimeError, ValueError, KeyError) as exc:
        typer.echo(f"Memory error: {exc}", err=True)
        raise typer.Exit(1) from exc
    project_root = str(project.resolve())
    if explicit_memory is not None and not resume:
        typer.echo(
            f"Memory updated ({explicit_memory.id}, {explicit_memory.state}): "
            f"{explicit_memory.content}"
        )
        return
    correction = memory.apply_natural_correction(
        task_text,
        session_id=resume or "",
        project_root=project_root,
    )
    from kageha.memory.bootstrap import prepare_turn_memory

    memory_extra = prepare_turn_memory(
        memory,
        query=task_text or f"resume:{resume}",
        project_root=project_root,
        session_id=resume or "",
    )
    if isinstance(correction, list):
        memory_extra = (
            memory_extra
            + "\n\nThe user's correction matched multiple recalled memories, "
            f"which were quarantined: {', '.join(correction)}. Ask one concise "
            "clarifying question to identify the intended claim."
        ).strip()

    async def _run() -> None:
        catalog = skills.catalog(limit=40)
        durable: Any = None
        try:
            from kageha.runtime import AgentRuntime, SecurityProfile, TurnRequest

            durable = AgentRuntime()
            common = {
                "user_id": "local",
                "agent_id": "main",
                "project_root": project_root,
                "auto_approve": auto_approve,
                "auto_build": build,
                "security_profile": SecurityProfile(selected_security),
                "max_steps": max_steps,
                "skill_catalog": catalog,
                "system_extra": memory_extra,
                "export_dir": str(output_dir) if output_dir else "",
                "live": not quiet,
                "platform": "cli",
                "agent_mode": mode,
                "loop_mode": loop_mode_for(mode),
            }
            if resume:
                result = await durable.execute_resume(
                    resume,
                    task_text or "Continue until the remaining goals pass.",
                    **common,
                )
            else:
                result = await durable.execute(
                    TurnRequest(objective=task_text, **common)
                )
        except Exception as exc:
            memory.capture_turn(
                TurnMemoryInput(
                    session_id=resume or "oneshot-failed",
                    turn_id=f"error-{time.time_ns()}",
                    task=task_text or f"resume:{resume}",
                    user_text=task_text,
                    assistant_text=str(exc),
                    status="error",
                    verified=False,
                    project_root=project_root,
                )
            )
            memory.drain_jobs(max_seconds=2.0)
            raise
        finally:
            if durable is not None:
                durable.close()
        memory.capture_turn(
            turn_memory_input_from_result(
                result,
                task=task_text if not resume else (task_text or f"resume:{resume}"),
                user_text=task_text,
                project_root=project_root,
            )
        )
        memory.drain_jobs(max_seconds=2.0)
        from kageha.loop.artifacts import format_artifacts_report
        from kageha.memory.learning_loop import maybe_prompt_skill_distill
        from kageha.config import sessions_dir

        typer.echo(
            f"\nrun_id={result.run_id} status={result.status} "
            f"steps={result.steps} usd~{result.spent_usd:.4f}"
        )
        typer.echo(result.message)
        typer.echo(
            format_artifacts_report(
                run_id=result.run_id,
                artifacts=result.artifacts,
                workspace_root=sessions_dir() / result.run_id,
            )
        )
        maybe_prompt_skill_distill(
            result,
            task=task_text if not resume else (task_text or f"resume:{resume}"),
            registry=skills,
            interactive=not auto_approve,
        )

    asyncio.run(_run())


# --- memory ---


@memory_app.command("status")
def memory_status() -> None:
    from kageha.memory.service import get_memory_service

    typer.echo(json.dumps(get_memory_service().status(), indent=2, sort_keys=True))


@memory_app.command("list")
def memory_list(
    scope: str = typer.Argument("", help="global|project|session"),
    session_id: str = typer.Option("", "--session"),
    limit: int = typer.Option(100, "--limit", "-n"),
) -> None:
    from kageha.memory.service import get_memory_service

    scope = scope.strip().lower()
    if scope and scope not in {"global", "project", "session"}:
        typer.echo("ERROR: scope must be global|project|session", err=True)
        raise typer.Exit(2)
    rows = get_memory_service().inspect(
        scope_type=scope,
        project_root=str(Path.cwd()) if scope == "project" else "",
        session_id=session_id if scope == "session" else "",
        limit=limit,
    )
    if not rows:
        typer.echo("(no matching memories)")
        return
    for row in rows:
        typer.echo(
            f"{row.id}  [{row.state}/{row.scope_type}/{row.kind}]  "
            f"{row.content.replace(chr(10), ' ')[:220]}"
        )


@memory_app.command("show")
def memory_show(memory_id: str = typer.Argument(...)) -> None:
    from kageha.memory.service import get_memory_service

    record = get_memory_service().store.get_memory(memory_id)
    if record is None:
        typer.echo(f"ERROR: memory not found: {memory_id}", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(record.to_dict(), indent=2, sort_keys=True))


@memory_app.command("recall")
def memory_recall(
    query: str = typer.Argument(...),
    session_id: str = typer.Option("", "--session"),
) -> None:
    from kageha.memory.models import MemoryQuery
    from kageha.memory.service import get_memory_service

    context = get_memory_service().recall(
        MemoryQuery(
            query=query,
            project_root=str(Path.cwd()),
            session_id=session_id,
        )
    )
    typer.echo(context.render() or "(no relevant confirmed memory)")


@memory_app.command("why")
def memory_why(
    trace_id: str = typer.Argument(""),
    session_id: str = typer.Option("", "--session"),
) -> None:
    from kageha.memory.service import get_memory_service

    service = get_memory_service()
    trace = service.explain(trace_id) if trace_id else service.latest_trace(session_id=session_id)
    if trace is None:
        typer.echo("(no recall trace)")
        return
    typer.echo(json.dumps(trace.to_dict(), indent=2, sort_keys=True))


@memory_app.command("remember")
def memory_remember(
    text: str = typer.Argument(...),
    scope: str = typer.Option("", "--scope", help="global|project|session"),
    session_id: str = typer.Option("", "--session"),
) -> None:
    from kageha.memory.models import MemoryMutation
    from kageha.memory.service import get_memory_service

    record = get_memory_service().mutate(
        MemoryMutation(
            action="remember",
            content=text,
            scope_type=scope,
            project_root=str(Path.cwd()),
            session_id=session_id,
        )
    )
    typer.echo(json.dumps(record.to_dict(), indent=2, sort_keys=True))


@memory_app.command("correct")
def memory_correct(
    memory_id: str = typer.Argument(...),
    replacement: str = typer.Argument(...),
    session_id: str = typer.Option("", "--session"),
) -> None:
    from kageha.memory.models import MemoryMutation
    from kageha.memory.service import get_memory_service

    record = get_memory_service().mutate(
        MemoryMutation(
            action="correct",
            target=memory_id,
            content=replacement,
            project_root=str(Path.cwd()),
            session_id=session_id,
        )
    )
    typer.echo(json.dumps(record.to_dict(), indent=2, sort_keys=True))


@memory_app.command("forget")
def memory_forget(
    target: str = typer.Argument(...),
    session_id: str = typer.Option("", "--session"),
) -> None:
    from kageha.memory.models import MemoryMutation
    from kageha.memory.service import get_memory_service

    record = get_memory_service().mutate(
        MemoryMutation(
            action="forget",
            target=target,
            project_root=str(Path.cwd()),
            session_id=session_id,
        )
    )
    typer.echo(json.dumps(record.to_dict(), indent=2, sort_keys=True))


@memory_app.command("on")
def memory_on() -> None:
    from kageha.memory.service import get_memory_service, set_runtime_memory_setting

    get_memory_service()
    path = set_runtime_memory_setting("enabled", True)
    typer.echo(f"Memory enabled. Settings: {path}")


@memory_app.command("off")
def memory_off() -> None:
    from kageha.memory.service import get_memory_service, set_runtime_memory_setting

    get_memory_service()
    path = set_runtime_memory_setting("enabled", False)
    typer.echo(f"Memory disabled. Settings: {path}")


@memory_app.command("learn")
def memory_learn(mode: str = typer.Argument(..., help="on|off")) -> None:
    from kageha.memory.service import get_memory_service, set_runtime_memory_setting

    mode = mode.strip().lower()
    if mode not in {"on", "off"}:
        typer.echo("ERROR: use on|off", err=True)
        raise typer.Exit(2)
    get_memory_service()
    path = set_runtime_memory_setting("learning_enabled", mode == "on")
    typer.echo(f"Memory learning {mode}. Settings: {path}")


@memory_app.command("reindex")
def memory_reindex() -> None:
    from dataclasses import asdict

    from kageha.memory.service import get_memory_service

    typer.echo(
        json.dumps(
            asdict(get_memory_service().rebuild_index()),
            indent=2,
            sort_keys=True,
        )
    )


@memory_app.command("export")
def memory_export(
    output: Path = typer.Argument(Path("kageha-memory-export.md")),
) -> None:
    from kageha.memory.service import get_memory_service

    target = output.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(get_memory_service().export_markdown(), encoding="utf-8")
    typer.echo(str(target))


@memory_app.command("import-rules")
def memory_import_rules(
    project: Path = typer.Argument(
        Path("."),
        help="Project root containing AGENTS.md / CLAUDE.md / .cursor/rules",
    ),
    sync: bool = typer.Option(
        False,
        "--sync",
        help="Also retract previously imported rules removed from disk",
    ),
) -> None:
    from kageha.memory.service import get_memory_service

    root = project.expanduser().resolve()
    if not root.is_dir():
        typer.echo(f"ERROR: not a directory: {root}", err=True)
        raise typer.Exit(2)
    report = get_memory_service().import_project_rules(str(root), sync=sync)
    typer.echo(json.dumps(report, indent=2, sort_keys=True))


@memory_app.command("prune")
def memory_prune() -> None:
    from kageha.memory.service import get_memory_service

    typer.echo(json.dumps(get_memory_service().prune_idle(), indent=2, sort_keys=True))


@memory_app.command("consolidate")
def memory_consolidate(
    force: bool = typer.Option(False, "--force", help="Ignore cooldown"),
) -> None:
    from kageha.memory.service import get_memory_service

    typer.echo(
        json.dumps(get_memory_service().consolidate(force=force), indent=2, sort_keys=True)
    )


@memory_app.command("forgotten")
def memory_forgotten(
    limit: int = typer.Option(30, "--limit", "-n"),
) -> None:
    from kageha.memory.service import get_memory_service

    typer.echo(json.dumps(get_memory_service().forgotten(limit=limit), indent=2, sort_keys=True))


@memory_app.command("fetch")
def memory_fetch(target: str = typer.Argument(..., help="memory or episode id")) -> None:
    from kageha.memory.service import get_memory_service

    try:
        typer.echo(json.dumps(get_memory_service().fetch(target), indent=2, sort_keys=True))
    except ValueError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc


# --- durable runtime ---


@runtime_app.command("status")
def runtime_status() -> None:
    from kageha.runtime import RuntimeStore

    store = RuntimeStore()
    try:
        payload = {
            **store.status(),
            "sessions": store.list_sessions(limit=20),
        }
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    finally:
        store.close()


@runtime_app.command("heal-providers")
def runtime_heal_providers(
    force: bool = typer.Option(
        False,
        "--force",
        help="Clear all open circuits immediately (not only expired cooldowns)",
    ),
) -> None:
    """Reset durable provider circuits so models can be selected again."""
    from kageha.runtime.providers import ProviderControlPlane
    from kageha.runtime.store import RuntimeStore

    store = RuntimeStore()
    try:
        control = ProviderControlPlane(store, auto_heal=False)
        healed = control.heal_circuits(force=force)
        healthy = [
            row["model_id"]
            for row in store.provider_health()
            if control.is_model_healthy(str(row["model_id"]))
        ]
        typer.echo(
            json.dumps(
                {"healed": healed, "force": force, "healthy_models": healthy},
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        store.close()


@runtime_app.command("inspect")
def runtime_inspect(
    session_id: str = typer.Argument(..., help="Session id"),
) -> None:
    from kageha.runtime import RuntimeStore

    store = RuntimeStore()
    try:
        typer.echo(
            json.dumps(
                store.inspect_session(session_id),
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        store.close()


@runtime_app.command("rebuild")
def runtime_rebuild(
    session_id: str = typer.Argument(..., help="session id"),
) -> None:
    from kageha.runtime import RuntimeStore

    store = RuntimeStore()
    try:
        rebuilt = store.rebuild(session_id)
        typer.echo(
            json.dumps(
                {
                    turn_id: snapshot.to_dict()
                    for turn_id, snapshot in rebuilt.items()
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        store.close()


@runtime_app.command("metrics")
def runtime_metrics(
    since: float = typer.Option(0.0, "--since", help="Unix timestamp lower bound"),
) -> None:
    """Summarize local reliability, latency and cost metrics."""
    from kageha.runtime import RuntimeStore

    store = RuntimeStore()
    try:
        typer.echo(
            json.dumps(
                store.metric_summary(since=since),
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        store.close()


@runtime_app.command("benchmark")
def runtime_benchmark(
    suite_file: Path = typer.Option(
        ...,
        "--suite",
        help="Path to benchmark case YAML (required)",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    category: str = typer.Option("", "--category", help="Filter category"),
    repeats: int = typer.Option(3, "--repeats", min=1),
    security: str = typer.Option("strict", "--security-profile"),
    auto_approve: bool = typer.Option(
        True,
        "--auto-approve/--no-auto-approve",
        help="Skip HITL during benchmark (default on for CI/headless)",
    ),
) -> None:
    """Run and persist a reproducible maturity benchmark suite."""
    from kageha.runtime.benchmark import BenchmarkRunner, load_cases
    from kageha.runtime.types import SecurityProfile

    cases = load_cases(suite_file)
    if category:
        cases = [case for case in cases if case.category == category]
    if not cases:
        raise typer.BadParameter("no benchmark cases matched")
    runner = BenchmarkRunner()
    try:
        score = asyncio.run(
            runner.run(
                cases,
                suite=category or suite_file.stem,
                repeats=repeats,
                security_profile=SecurityProfile(security),
                auto_approve=auto_approve,
            )
        )
        typer.echo(json.dumps(score.to_dict(), indent=2, sort_keys=True))
    finally:
        runner.close()


@runtime_app.command("soak")
def runtime_soak(
    suite_file: Path = typer.Option(
        ...,
        "--suite",
        help="Path to soak case YAML (required)",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    hours: float = typer.Option(72.0, "--hours", min=0.01),
    max_turns: int = typer.Option(
        0,
        "--max-turns",
        help="Testing cap; zero runs for the full duration",
    ),
) -> None:
    """Run a mixed-task production soak (72 hours by default)."""
    from kageha.runtime.benchmark import load_cases, run_soak

    cases = load_cases(suite_file)
    score = asyncio.run(
        run_soak(
            cases,
            hours=hours,
            max_turns=max_turns,
        )
    )
    typer.echo(json.dumps(score.to_dict(), indent=2, sort_keys=True))
@app.command("memory-worker", hidden=True)
def memory_worker() -> None:
    """Run the persistent memory extraction worker under the supervisor."""
    from kageha.memory.service import get_memory_service

    service = get_memory_service(start_worker=True)
    try:
        while True:
            service.drain_jobs(max_seconds=0.25)
            time.sleep(0.25)
    except KeyboardInterrupt:
        service.stop_worker(timeout=2.0)


@app.command("chat")
def chat_cmd(
    resume: Optional[str] = typer.Option(
        None, "--resume", "-r", help="Open an existing session run_id for follow-ups"
    ),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Skip HITL prompts"),
    quiet: bool = typer.Option(False, "--quiet", help="Hide live step/tool progress"),
    max_steps: Optional[int] = typer.Option(
        None, "--max-steps", help="Per-turn loop step ceiling"
    ),
    voice: bool = typer.Option(
        False,
        "--voice",
        help=(
            "Local mic → STT (needs sox/ffmpeg + mic + API key). "
            "Headless hosts: type instead"
        ),
    ),
    project: Path = typer.Option(
        Path.cwd(), "--project", "-C", help="Project root for AGENTS.md / rules / tools"
    ),
    attach: Optional[str] = typer.Option(
        None,
        "--attach",
        "-a",
        help=(
            "Attach to App Server: `kageha server --listen unix://` then auto|unix://|ws://. "
            "ws:// needs: uv sync --extra server-ws"
        ),
    ),
    sandbox: Optional[str] = typer.Option(
        None,
        "--sandbox",
        help="Shell isolation for this process: auto|off|docker|bwrap|seatbelt|ssh|modal",
    ),
) -> None:
    """Interactive chat — ask, then follow up in the same session workspace."""
    from kageha.chat.repl import run_chat_repl
    from kageha.config import apply_sandbox_cli

    try:
        selected_sandbox = apply_sandbox_cli(sandbox)
    except ValueError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if sandbox:
        typer.echo(f"[kageha] sandbox={selected_sandbox}")

    asyncio.run(
        run_chat_repl(
            resume=resume,
            auto_approve=auto_approve,
            max_steps=max_steps,
            quiet=quiet,
            voice=voice,
            project_root=str(project.resolve()),
            attach=attach,
        )
    )


@app.command("sessions")
def sessions_cmd(
    limit: int = typer.Option(20, "--limit", "-n", help="How many recent sessions to show"),
) -> None:
    """List recent session workspaces (for --resume / chat -r)."""

    from kageha.runtime import RuntimeStore

    store = RuntimeStore()
    try:
        rows = store.list_sessions(limit)
    finally:
        store.close()
    if not rows:
        typer.echo("(no sessions)")
        raise typer.Exit()
    for row in rows:
        typer.echo(
            f"{row['id']}  {row['updated_at']:.3f}  "
            f"[{row['turn_status'] or row['status']}]  "
            f"{str(row['objective'])[:100]}"
        )
@app.command("server")
def server_cmd(
    listen: str = typer.Option(
        "stdio://",
        "--listen",
        "-l",
        help="stdio:// | unix://[path] | ws://127.0.0.1:PORT",
    ),
) -> None:
    """Start JSON-RPC App Server (stdio, Unix socket, or loopback WebSocket)."""
    from kageha.app_server_listen import main_listen

    main_listen(listen)
@worktree_app.command("list")
def worktree_list_cmd(
    project: Path = typer.Option(Path.cwd(), "--project", "-C"),
) -> None:
    """List git worktrees for the project."""
    from kageha.project.worktree import list_worktrees

    typer.echo(json.dumps(list_worktrees(project), indent=2))


@worktree_app.command("add")
def worktree_add_cmd(
    label: str = typer.Argument("agent"),
    project: Path = typer.Option(Path.cwd(), "--project", "-C"),
    base: str = typer.Option("HEAD", "--base"),
) -> None:
    """Create an isolated worktree under .kageha/worktrees/."""
    from kageha.project.worktree import create_worktree

    handle = create_worktree(project, label=label, base_ref=base)
    typer.echo(
        json.dumps(
            {"path": str(handle.path), "branch": handle.branch, "root": str(handle.root)},
            indent=2,
        )
    )


@worktree_app.command("remove")
def worktree_remove_cmd(
    path: Path = typer.Argument(..., help="Worktree path"),
    project: Path = typer.Option(Path.cwd(), "--project", "-C"),
) -> None:
    """Remove a worktree created by Kageha."""
    from kageha.project.worktree import WorktreeHandle

    handle = WorktreeHandle(root=project.resolve(), branch="", path=path.resolve())
    handle.remove(force=True)
    typer.echo(f"removed {path}")


@jobs_app.command("run")
def jobs_run_cmd(
    objective: str = typer.Argument(..., help="Background job objective"),
    project: Path = typer.Option(Path.cwd(), "--project", "-C"),
    agent_mode: str = typer.Option("plan", "--mode"),
    max_steps: int = typer.Option(40, "--max-steps"),
    wait: bool = typer.Option(False, "--wait", help="Block until the job finishes"),
    notify: str = typer.Option(
        "",
        "--notify",
        help="Optional notify label recorded on the job",
    ),
) -> None:
    """Enqueue a durable background job."""
    from kageha.project.async_jobs import enqueue_job, load_job

    job = enqueue_job(
        objective=objective,
        project_root=str(project.resolve()),
        agent_mode=agent_mode,
        max_steps=max_steps,
        notify_channel=notify,
        start=True,
    )
    typer.echo(json.dumps(job.to_dict(), indent=2))
    if wait:
        import time as _time

        while True:
            current = load_job(job.id)
            if current is None:
                raise typer.Exit(code=1)
            if current.status not in {"queued", "running"}:
                typer.echo(json.dumps(current.to_dict(), indent=2))
                if current.status != "success":
                    raise typer.Exit(code=1)
                break
            _time.sleep(1.0)


@jobs_app.command("list")
def jobs_list_cmd(limit: int = typer.Option(20, "--limit")) -> None:
    """List recent background jobs."""
    from kageha.project.async_jobs import list_jobs

    typer.echo(
        json.dumps([j.to_dict() for j in list_jobs(limit=limit)], indent=2)
    )


@jobs_app.command("status")
def jobs_status_cmd(job_id: str = typer.Argument(...)) -> None:
    """Show one background job."""
    from kageha.project.async_jobs import load_job

    job = load_job(job_id)
    if job is None:
        typer.echo(f"job not found: {job_id}", err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(job.to_dict(), indent=2))


@project_app.command("brain")
def project_brain_cmd(
    project: Path = typer.Option(Path.cwd(), "--project", "-C"),
) -> None:
    """Show loaded AGENTS.md / rules / commands for a project."""
    from kageha.project.brain import load_project_brain, render_project_brain

    brain = load_project_brain(project)
    if brain is None:
        typer.echo("(no project brain — add AGENTS.md or .kageha/rules/)")
        raise typer.Exit()
    typer.echo(
        json.dumps(
            {
                "root_file": brain.root_file,
                "rules": [r.name for r in brain.rules],
                "commands": brain.command_names,
                "project_root": str(brain.project_root),
            },
            indent=2,
        )
    )
    typer.echo("\n" + render_project_brain(brain))


@project_app.command("hooks")
def project_hooks_cmd(
    project: Path = typer.Option(Path.cwd(), "--project", "-C"),
) -> None:
    """List lifecycle hooks from ~/.kageha/hooks.json and .kageha/hooks.json."""
    from kageha.project.hooks import load_hook_runner

    runner = load_hook_runner(project)
    typer.echo(
        json.dumps(
            [
                {
                    "event": h.event,
                    "matcher": h.matcher,
                    "command": h.command[:120],
                    "http": h.http,
                    "deny_message": h.deny_message,
                }
                for h in runner.hooks
            ],
            indent=2,
        )
    )


@project_app.command("commands")
def project_commands_cmd(
    project: Path = typer.Option(Path.cwd(), "--project", "-C"),
) -> None:
    """List .kageha/commands slash recipes."""
    from kageha.project.brain import load_project_brain

    brain = load_project_brain(project)
    names = list(brain.command_names) if brain else []
    if not names:
        typer.echo("(no .kageha/commands/*.md)")
        raise typer.Exit()
    for name in names:
        typer.echo(name)


@browser_app.callback()
def browser_root(ctx: typer.Context) -> None:
    """``kageha browser`` with no subcommand → status (same as /browser)."""
    if ctx.invoked_subcommand is not None:
        return
    from kageha.harness.browser.prefs import status_text

    typer.echo(status_text())


@computer_app.callback()
def computer_root(ctx: typer.Context) -> None:
    """``kageha computer`` with no subcommand → status (same as /computer)."""
    if ctx.invoked_subcommand is not None:
        return
    from kageha.harness.tools.computer_prefs import status_text

    typer.echo(status_text())


@computer_app.command("status")
def computer_status_cmd() -> None:
    """Show computer-use pack / driver status (same as /computer)."""
    from kageha.harness.tools.computer_prefs import status_text

    typer.echo(status_text())
@computer_app.command("pack")
def computer_pack_cmd(
    mode: str = typer.Argument(..., help="on|off|auto"),
) -> None:
    """Enable, disable, or auto-gate the computer tool pack."""
    from kageha.harness.tools.computer_prefs import set_pack_mode, status_text

    try:
        set_pack_mode(mode)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(status_text())


@browser_app.command("status")
def browser_status_cmd() -> None:
    """Show browser / research backend prefs (same as /browser)."""
    from kageha.harness.browser.prefs import status_text

    typer.echo(status_text())


@browser_app.command("list")
def browser_list_cmd() -> None:
    """List available browser backends."""
    from kageha.harness.browser.backends import format_backend_list
    from kageha.harness.browser.prefs import load_browser_prefs

    typer.echo(format_backend_list(current=load_browser_prefs().backend))


@browser_app.command("use")
def browser_use_cmd(
    backend: str = typer.Argument(..., help="http|chromium|lightpanda|comet|cdp|docker|headless"),
    cdp: Optional[str] = typer.Option(None, "--cdp", help="CDP endpoint URL"),
) -> None:
    """Select browser backend (persists to ~/.kageha/browser.json)."""
    from kageha.harness.browser.prefs import set_backend, status_text

    try:
        set_backend(backend, cdp=cdp)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(status_text())


@browser_app.command("research")
def browser_research_cmd(
    query: str = typer.Argument(..., help="Research question"),
    depth: str = typer.Option("flash", "--depth", "-d", help="flash|standard|deep"),
) -> None:
    """Run blink research_run natively (same as /research)."""

    async def _run() -> str:
        from kageha.research.backend import research_run

        return await research_run(query, depth=depth)

    typer.echo(asyncio.run(_run()))


@app.command("research")
def research_cmd(
    query: str = typer.Argument(..., help="Research question"),
    depth: str = typer.Option("flash", "--depth", "-d", help="flash|standard|deep"),
) -> None:
    """Blink research (alias for ``kageha browser research`` / chat ``/research``)."""
    browser_research_cmd(query=query, depth=depth)


@project_app.command("status")
def project_status_cmd(
    project: Path = typer.Option(Path.cwd(), "--project", "-C"),
) -> None:
    """Compact project brain + hooks + worktree summary."""
    from kageha.project.brain import load_project_brain
    from kageha.project.hooks import load_hook_runner
    from kageha.project.worktree import is_git_repo, list_worktrees

    brain = load_project_brain(project)
    hooks = load_hook_runner(project)
    typer.echo(
        json.dumps(
            {
                "project_root": str(project.resolve()),
                "git": is_git_repo(project),
                "brain": None
                if brain is None
                else {
                    "root_file": brain.root_file,
                    "rules": len(brain.rules),
                    "commands": brain.command_names,
                },
                "hooks": len(hooks.hooks),
                "worktrees": list_worktrees(project),
            },
            indent=2,
        )
    )


@app.command("webui")
def webui_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address"),
    port: int = typer.Option(8788, "--port", "-p", help="HTTP port"),
    open_browser: bool = typer.Option(
        False, "--open", help="Open the UI in a browser"
    ),
    attach: Optional[str] = typer.Option(
        None,
        "--attach",
        "-a",
        help="Attach to a long-lived App Server (unix:// | ws://127.0.0.1:PORT | auto)",
    ),
    project: Path = typer.Option(
        Path.cwd(),
        "--project",
        "-C",
        help="Default project root for Labs / review / worktrees",
    ),
) -> None:
    """Serve the Kageha chat + memory Web UI (REST over App Server)."""
    from kageha.webui.server import serve_webui

    serve_webui(
        host=host,
        port=port,
        open_browser=open_browser,
        attach=attach,
        project_root=str(project.resolve()),
    )
@models_app.command("list")
def models_list() -> None:
    from kageha.models.registry import ModelRegistry

    reg = ModelRegistry.load()
    available = {m.id for m in reg.available_models()}
    for mid, m in reg.models.items():
        flag = "✓" if mid in available else "·"
        typer.echo(f"{flag} {mid:20} provider={m.provider:12} model={m.model} roles={m.roles}")


@models_app.command("test")
def models_test(model_id: Optional[str] = typer.Argument(None)) -> None:
    from kageha.models.registry import ModelRegistry

    reg = ModelRegistry.load()
    ids = [model_id] if model_id else [m.id for m in reg.available_models()]
    if not ids:
        typer.echo("No models with API keys configured")
        raise typer.Exit(1)

    async def _test() -> None:
        failures = 0
        for mid in ids:
            try:
                model = reg.build(mid)
                text = await model.smoke()
                if not text:
                    raise RuntimeError("model returned an empty response")
                typer.echo(f"OK {mid}: {text[:80]!r}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                typer.echo(f"FAIL {mid}: {e}")
        if failures:
            raise typer.Exit(1)

    asyncio.run(_test())


@models_app.command("add")
def models_add(
    model_id: str = typer.Option(..., "--id"),
    protocol: str = typer.Option(..., "--protocol", help="openai_compat|anthropic_compat|gemini"),
    base_url: str = typer.Option("", "--base-url"),
    api_key_env: str = typer.Option(..., "--api-key-env"),
    model: str = typer.Option(..., "--model"),
    roles: str = typer.Option("default", "--roles", help="comma-separated"),
) -> None:
    from kageha.models.registry import ModelRegistry

    reg = ModelRegistry.load()
    path = reg.add_model(
        model_id=model_id,
        protocol=protocol,
        base_url=base_url,
        api_key_env=api_key_env,
        model=model,
        roles=[r.strip() for r in roles.split(",") if r.strip()],
    )
    typer.echo(f"Wrote {path}")


@models_auth_app.command("probe")
def models_auth_probe() -> None:
    """Detect local Codex / Gemini CLI / Antigravity logins (no token values)."""
    from kageha.models.auth_store import probe_local_logins

    typer.echo(json.dumps(probe_local_logins(), indent=2))


@models_auth_app.command("list")
def models_auth_list() -> None:
    """List stored subscription auth profiles (metadata only)."""
    from kageha.models.auth_cli import auth_status_payload

    typer.echo(json.dumps(auth_status_payload(), indent=2))


@models_auth_app.command("status")
def models_auth_status(
    provider: Optional[str] = typer.Argument(
        None, help="Provider id (chatgpt, gemini-cli, antigravity, …)"
    ),
) -> None:
    """Show auth status + local login probe."""
    from kageha.models.auth_cli import auth_status_payload

    payload = auth_status_payload(provider)
    typer.echo(json.dumps(payload, indent=2))
    if provider and payload.get("profile") is None:
        raise typer.Exit(1)


@models_auth_app.command("import")
def models_auth_import(
    target: str = typer.Argument(
        ...,
        help="chatgpt|openai-codex|gemini-cli|antigravity",
    ),
    path: Optional[Path] = typer.Option(
        None,
        "--path",
        "-p",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Override path to auth JSON",
    ),
) -> None:
    """Import ChatGPT/Codex or Gemini CLI / Antigravity OAuth into ~/.kageha/auth/."""
    from kageha.models.auth_cli import run_import

    try:
        prof = run_import(target, path=path)
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(prof.as_public_dict(), indent=2))
    typer.echo("Tokens saved under ~/.kageha/auth/ (mode 0600). Not printed.")


@models_auth_app.command("logout")
def models_auth_logout(
    provider: str = typer.Argument(..., help="chatgpt|gemini-cli|antigravity|…"),
) -> None:
    """Delete stored auth profile(s) for a provider."""
    from kageha.models.auth_cli import run_logout

    ok = run_logout(provider)
    typer.echo(json.dumps({"provider": provider, "deleted": ok}, indent=2))
    if not ok:
        raise typer.Exit(1)


@models_app.command("setup")
@models_app.command("configure")
def models_setup(
    no_test: bool = typer.Option(
        False, "--no-test", help="Skip the smoke test at the end"
    ),
    skip_auth: bool = typer.Option(
        False,
        "--skip-auth",
        help="Skip subscription-auth import step at the start",
    ),
) -> None:
    """Interactive provider wizard (Hermes-style). Writes .env + ~/.kageha/models.yaml."""
    from kageha.models.setup import run_models_setup

    result = run_models_setup(
        smoke_test=False if no_test else None,
        skip_auth=skip_auth,
    )
    if not result.get("ok"):
        raise typer.Exit(1)
    if result.get("smoke_ok") is False:
        raise typer.Exit(1)


@models_app.command("providers")
def models_providers() -> None:
    """List built-in provider presets for `models setup`."""
    from kageha.models.setup import list_presets

    for p in list_presets():
        typer.echo(
            f"{p.key:14} {p.label:32} env={p.api_key_env} default={p.default_model}"
        )
@skills_app.command("list")
def skills_list() -> None:
    from kageha.memory.skills import SkillRegistry

    typer.echo(SkillRegistry().catalog())


@skills_app.command("add")
def skills_add(source: Path = typer.Argument(...)) -> None:
    """Install a local skill folder (must contain SKILL.md)."""
    from kageha.memory.skills import SkillRegistry

    skill = SkillRegistry().add_local(source)
    typer.echo(f"Added skill {skill.name} from {source}")


@skills_app.command("install")
def skills_install(
    spec: str = typer.Argument(
        ...,
        help=(
            "Local path, GitHub owner/repo, or owner/repo/skill "
            "(e.g. anthropics/skills/pdf)"
        ),
    ),
    only: Optional[str] = typer.Option(
        None,
        "--only",
        help="Comma-separated skill names to install from a repo",
    ),
    force: bool = typer.Option(
        False, "--force", help="Replace existing user skills with the same name"
    ),
) -> None:
    """Install Agent Skills from disk or GitHub (Anthropic / agentskills.io compatible)."""
    from kageha.memory.skills_install import install_skills

    only_list = [x.strip() for x in (only or "").split(",") if x.strip()] or None
    try:
        result = install_skills(spec, only=only_list, force=force)
    except Exception as e:  # noqa: BLE001
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"source: {result.source}")
    typer.echo(f"dest:   {result.dest_root}")
    if result.installed:
        typer.echo("installed: " + ", ".join(result.installed))
    else:
        typer.echo("installed: (none)")
    if result.skipped:
        typer.echo("skipped:   " + ", ".join(result.skipped))
    typer.echo(
        "Note: document skills may need local deps (python-docx, openpyxl, "
        "reportlab, etc.). Audit scripts/ before trusting network skills."
    )


@skills_app.command("browse")
def skills_browse(
    repo: str = typer.Argument(
        "anthropics/skills",
        help="GitHub owner/repo to list (default: anthropics/skills)",
    ),
) -> None:
    """List skills available in a remote Agent Skills repo (no install)."""
    from kageha.memory.skills_install import list_remote_skills

    try:
        rows = list_remote_skills(repo)
    except Exception as e:  # noqa: BLE001
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(code=1) from e
    if not rows:
        typer.echo("(no skills found)")
        raise typer.Exit()
    for r in rows:
        typer.echo(f"{r['name']:28}  {r['description']}")


@skills_app.command("new")
def skills_new(name: str, description: str = "") -> None:
    from kageha.memory.skills import SkillRegistry

    path = SkillRegistry().create_stub(name, description)
    typer.echo(f"Scaffolded {path}")


@skills_app.command("remove")
def skills_remove(name: str) -> None:
    from kageha.memory.skills import SkillRegistry

    SkillRegistry().remove(name)
    typer.echo(f"Removed {name}")


@skills_app.command("reload")
def skills_reload() -> None:
    from kageha.memory.skills import SkillRegistry

    reg = SkillRegistry()
    typer.echo(f"Loaded {len(reg.skills)} skills")


@skills_app.command("reindex")
def skills_reindex() -> None:
    """Rebuild Gemini embedding index for skill retrieval (needs GEMINI_API_KEY)."""
    from kageha.memory.skill_embeddings import get_skill_embedding_index
    from kageha.memory.skills import SkillRegistry
    from kageha.models.embeddings import resolve_embedding_config

    cfg = resolve_embedding_config()
    if cfg is None:
        typer.echo(
            "No embedding backend available. Set GEMINI_API_KEY "
            "(or change embedding.provider/model in models.yaml)."
        )
        raise typer.Exit(1)
    reg = SkillRegistry()
    index = get_skill_embedding_index(force_new=True)
    # Drop cache so ensure re-embeds everything for the active model.
    if index.path.is_file():
        index.path.unlink()
        index._load()
    ok = index.ensure(reg.skills)
    if not ok:
        typer.echo("Embedding reindex failed (API error or embeddings disabled).")
        raise typer.Exit(1)
    n = len((index._data.get("skills") or {}))
    typer.echo(
        f"Indexed {n} skills with {cfg.provider}/{cfg.model} "
        f"(dim={cfg.dimensions}) → {index.path}"
    )


@skills_app.command("validate")
def skills_validate(name: Optional[str] = typer.Argument(None)) -> None:
    from kageha.memory.skills import SkillRegistry, validate_skill

    reg = SkillRegistry()
    skills = [reg.get(name)] if name else list(reg.skills.values())
    bad = False
    for s in skills:
        if not s:
            typer.echo(f"MISSING {name}")
            raise typer.Exit(1)
        errs = validate_skill(s)
        if errs:
            bad = True
            typer.echo(f"BAD {s.name}: {'; '.join(errs)}")
        else:
            typer.echo(f"OK {s.name}")
    if bad:
        raise typer.Exit(1)


# --- curator ---
@mcp_app.command("list")
def mcp_list() -> None:
    """List configured MCP servers (from mcp.yaml / host editor configs)."""
    from kageha.mcp.config import ensure_default_mcp_yaml, load_mcp_config

    ensure_default_mcp_yaml()
    servers = load_mcp_config()
    if not servers:
        typer.echo("(no MCP servers configured — edit ~/.kageha/mcp.yaml)")
        return
    for name, s in servers.items():
        state = "on" if s.enabled else "off"
        target = s.command or s.url or "?"
        typer.echo(f"{name:20} [{state}] {s.transport:5}  {target} {' '.join(s.args)}")


@mcp_app.command("add")
def mcp_add(
    name: str = typer.Argument(..., help="Server name"),
    command: str = typer.Option("", "--command", "-c", help="Executable (stdio)"),
    args: list[str] = typer.Option(None, "--arg", "-a", help="Arg (repeatable)"),
    url: str = typer.Option("", "--url", help="SSE/HTTP URL instead of stdio"),
) -> None:
    """Add or update an MCP server in ~/.kageha/mcp.yaml."""
    from kageha.mcp.config import McpServerConfig, load_mcp_config, save_mcp_config

    if not command and not url:
        typer.echo("ERROR: provide --command (stdio) or --url (sse/http)", err=True)
        raise typer.Exit(2)
    servers = load_mcp_config()
    transport = "sse" if url else "stdio"
    servers[name] = McpServerConfig(
        name=name,
        transport=transport,
        command=command if not url else "",
        args=list(args or []),
        url=url,
        enabled=True,
    )
    path = save_mcp_config(servers)
    typer.echo(f"Wrote {name} → {path}")


@mcp_app.command("remove")
def mcp_remove(name: str = typer.Argument(...)) -> None:
    from kageha.mcp.config import load_mcp_config, save_mcp_config

    servers = load_mcp_config()
    if name not in servers:
        typer.echo(f"ERROR: unknown server {name}", err=True)
        raise typer.Exit(1)
    del servers[name]
    save_mcp_config(servers)
    typer.echo(f"Removed {name}")


@mcp_app.command("test")
def mcp_test(
    name: Optional[str] = typer.Argument(None, help="Server name (default: all)"),
) -> None:
    """Connect and list tools/resources/prompts for MCP server(s)."""
    from kageha.mcp.client import McpHub
    from kageha.mcp.config import load_mcp_config

    async def _run() -> None:
        hub = McpHub(load_mcp_config())
        try:
            if name:
                conn = await hub.connect(name)
                rows = [r for r in hub.status() if r["name"] == name]
                typer.echo(
                    json.dumps(
                        {
                            "status": rows,
                            "tools": [t.name for t in conn.tools],
                            "prompts": [p.name for p in conn.prompts],
                            "roots": hub.roots,
                        },
                        indent=2,
                    )
                )
                if conn.error:
                    raise typer.Exit(code=1)
            else:
                await hub.connect_all()
                detail = []
                for n, c in hub.connected.items():
                    detail.append(
                        {
                            "name": n,
                            "ok": c.ok,
                            "error": c.error,
                            "tools": [t.name for t in c.tools],
                            "resources": [r.uri for r in c.resources[:10]],
                            "prompts": [p.name for p in c.prompts[:10]],
                        }
                    )
                typer.echo(json.dumps({"servers": detail, "roots": hub.roots}, indent=2))
                if any(not d["ok"] and d.get("error") != "disabled" for d in detail):
                    # only fail if something was enabled and failed
                    enabled_fail = [
                        d
                        for d in detail
                        if not d["ok"] and d.get("error") not in {"disabled", ""}
                    ]
                    if enabled_fail:
                        raise typer.Exit(code=1)
        finally:
            await hub.close()

    asyncio.run(_run())


@mcp_app.command("serve")
def mcp_serve(
    auto_approve: bool = typer.Option(
        True, "--auto-approve/--no-auto-approve", help="Auto-approve tools when serving"
    ),
) -> None:
    """Run Kageha as an MCP server over stdio (expose tools to MCP hosts).

    HTTP/SSE serve is not implemented yet; hosts should launch this as a stdio command.
    """
    from kageha.mcp.server import run_mcp_server

    asyncio.run(run_mcp_server(auto_approve=auto_approve))


@mcp_app.command("init")
def mcp_init() -> None:
    """Create ~/.kageha/mcp.yaml if missing."""
    from kageha.mcp.config import ensure_default_mcp_yaml

    path = ensure_default_mcp_yaml()
    typer.echo(f"MCP config: {path}")
