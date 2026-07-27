"""Kageha CLI — run, models, skills, kb, server, eval."""

from __future__ import annotations

import asyncio
import json
import os
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
        "Kageha trimmed agent harness — core: chat/run/webui + MCP/skills/memory. "
        "Default channels: telegram, whatsapp, teams. "
        "Optional packs via KAGEHA_TOOL_PACKS (see docs/ARCHITECTURE.md)."
    ),
)
models_app = typer.Typer(help="Model registry")
models_auth_app = typer.Typer(help="Subscription / OAuth model auth (ChatGPT, Gemini CLI)")
skills_app = typer.Typer(help="Skills (agentskills.io / Anthropic compatible)")
curator_app = typer.Typer(help="Skill curator (usage, archive, pin)")
mcp_app = typer.Typer(help="Model Context Protocol servers")
kb_app = typer.Typer(help="Knowledge bases (optional pack)")
memory_app = typer.Typer(help="Inspect and manage provenance-aware memory")
runtime_app = typer.Typer(help="Inspect and repair the durable runtime")
daemon_app = typer.Typer(help="Supervise Kageha services")
gateway_app = typer.Typer(
    help="Optional multi-adapter channel supervisor (see docs/USAGE.md)"
)
connect_app = typer.Typer(help="OAuth connections (optional pack; prefer MCP)")
bravia_app = typer.Typer(help="Sony Bravia TV (skill-backed CLI; not a tool pack)")
worktree_app = typer.Typer(help="Git worktree isolation for parallel agents")
cloud_app = typer.Typer(help="Durable async / background jobs")
project_app = typer.Typer(help="Project brain, rules, hooks, and slash commands")
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
app.add_typer(curator_app, name="curator")
app.add_typer(mcp_app, name="mcp")
app.add_typer(kb_app, name="kb")
app.add_typer(memory_app, name="memory")
app.add_typer(runtime_app, name="runtime")
app.add_typer(daemon_app, name="daemon")
app.add_typer(gateway_app, name="gateway")
app.add_typer(connect_app, name="connect")
app.add_typer(bravia_app, name="bravia")
app.add_typer(worktree_app, name="worktree")
app.add_typer(cloud_app, name="cloud")
app.add_typer(project_app, name="project")
app.add_typer(browser_app, name="browser")
app.add_typer(computer_app, name="computer")


@app.callback()
def main_callback() -> None:
    """Kageha — loop + harness + memory + KB."""


@app.command("version")
def version_cmd() -> None:
    typer.echo(__version__)


@app.command("run")
def run_cmd(
    task: Optional[str] = typer.Argument(
        None, help="Task for the agent (optional with --resume)"
    ),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Skip HITL prompts"),
    kb: list[str] = typer.Option(None, "--kb", help="Attach knowledge base id(s)"),
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
        help="strict or approval_fallback",
    ),
    project: Path = typer.Option(
        Path.cwd(), "--project", "-C", help="Project root for AGENTS.md / rules / tools"
    ),
    attach: Optional[str] = typer.Option(
        None,
        "--attach",
        "-a",
        help="Run via long-lived App Server (unix:// | ws://127.0.0.1:PORT | auto)",
    ),
    agent_mode: Optional[str] = typer.Option(
        None,
        "--mode",
        help="normal | plan | spec | goal (default: normal; "
        "/plan|/spec|/goal in the task also selects mode)",
    ),
    build: bool = typer.Option(
        False,
        "--build",
        help="Plan/Spec: approve the design and execute (Build). "
        "Without this, plan/spec stop after writing plan artifacts.",
    ),
) -> None:
    """Run the agent loop on a task (one-shot). For follow-ups use `kageha chat`."""
    from kageha.knowledge.registry import attached_kbs
    from kageha.memory.models import TurnMemoryInput
    from kageha.memory.service import (
        get_memory_service,
        turn_memory_input_from_result,
    )
    from kageha.memory.skills import SkillRegistry
    from kageha.config import security_profile
    from kageha.loop.mode_policy import (
        loop_mode_for,
        resolve_agent_mode,
    )

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
        }:
            raise typer.Exit(code=1)
        return

    kbs = list(kb or []) + attached_kbs()
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
        pins = ", ".join(kbs) if kbs else ""
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
                "knowledge_bases": tuple(kbs),
                "skill_catalog": catalog,
                "kb_pins": pins,
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


@app.command("doctor")
def doctor(
    deep: bool = typer.Option(False, "--deep", help="Run live providers and replay all sessions"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Check runtime, provider, sandbox, supervisor and channel readiness."""
    from kageha.runtime.doctor import run_doctor

    report = run_doctor(deep=deep)
    if as_json:
        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        for check in report.checks:
            mark = "PASS" if check.ok else ("WARN" if check.severity != "error" else "FAIL")
            typer.echo(f"{mark:4}  {check.name:20} {check.detail}")
    if not report.ok:
        raise typer.Exit(1)


def _supervisor():
    from kageha.runtime.supervisor import ServiceSupervisor

    return ServiceSupervisor()


@daemon_app.command("install")
def daemon_install(
) -> None:
    """Install launchd (macOS) or systemd-user (Linux) service files."""
    supervisor = _supervisor()
    try:
        for path in supervisor.install():
            typer.echo(str(path))
    finally:
        supervisor.close()


@daemon_app.command("start")
def daemon_start(
    service: str = typer.Argument("all", help="all|app-server|memory-worker|whatsapp"),
) -> None:
    supervisor = _supervisor()
    try:
        typer.echo(json.dumps(supervisor.start(service), indent=2))
    finally:
        supervisor.close()


@daemon_app.command("stop")
def daemon_stop(
    service: str = typer.Argument("all", help="all|app-server|memory-worker|whatsapp"),
) -> None:
    supervisor = _supervisor()
    try:
        typer.echo(json.dumps(supervisor.stop(service), indent=2))
    finally:
        supervisor.close()


@daemon_app.command("restart")
def daemon_restart(
    service: str = typer.Argument("all", help="all|app-server|memory-worker|whatsapp"),
) -> None:
    supervisor = _supervisor()
    try:
        typer.echo(json.dumps(supervisor.restart(service), indent=2))
    finally:
        supervisor.close()


@daemon_app.command("status")
def daemon_status(
) -> None:
    supervisor = _supervisor()
    try:
        typer.echo(json.dumps(supervisor.status(), indent=2, sort_keys=True))
    finally:
        supervisor.close()


@daemon_app.command("logs")
def daemon_logs(
    service: str = typer.Argument("app-server"),
    lines: int = typer.Option(100, "--lines", "-n"),
) -> None:
    supervisor = _supervisor()
    try:
        typer.echo(supervisor.logs(service, lines=lines))
    finally:
        supervisor.close()


def _gateway():
    from kageha.gateway import ChannelGateway

    return ChannelGateway()


@gateway_app.command("init")
def gateway_init(
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite existing ~/.kageha/gateway.yaml with the template",
    ),
) -> None:
    """Write ~/.kageha/gateway.yaml if missing (or overwrite with --force)."""
    from kageha.gateway.config import DEFAULT_GATEWAY_YAML, ensure_default_gateway_yaml, gateway_config_path

    path = gateway_config_path()
    if path.is_file() and force:
        path.write_text(DEFAULT_GATEWAY_YAML, encoding="utf-8")
        typer.echo(f"Wrote {path}")
        return
    created = not path.is_file()
    ensure_default_gateway_yaml(path=path)
    typer.echo(f"{'Wrote' if created else 'Exists'} {path}")


@gateway_app.command("start")
def gateway_start(
    channel: str = typer.Argument(
        "all",
        help="all|telegram|discord|slack|whatsapp-qr|…",
    ),
) -> None:
    """Start configured channel adapters under the gateway supervisor group."""
    gateway = _gateway()
    try:
        if not gateway.config.enabled_channels() and channel == "all":
            typer.echo(
                "No channels enabled. Edit ~/.kageha/gateway.yaml "
                "(kageha gateway init) or set KAGEHA_GATEWAY_CHANNELS=telegram,…"
            )
            raise typer.Exit(1)
        try:
            result = gateway.start(channel)
        except KeyError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from exc
        typer.echo(json.dumps(result, indent=2))
    finally:
        gateway.close()


@gateway_app.command("stop")
def gateway_stop(
    channel: str = typer.Argument("all", help="all|telegram|discord|…"),
) -> None:
    """Stop gateway-supervised channel adapters."""
    gateway = _gateway()
    try:
        try:
            typer.echo(json.dumps(gateway.stop(channel), indent=2))
        except KeyError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from exc
    finally:
        gateway.close()


@gateway_app.command("restart")
def gateway_restart(
    channel: str = typer.Argument("all", help="all|telegram|discord|…"),
) -> None:
    gateway = _gateway()
    try:
        try:
            typer.echo(json.dumps(gateway.restart(channel), indent=2))
        except KeyError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from exc
    finally:
        gateway.close()


@gateway_app.command("status")
def gateway_status(
) -> None:
    """Show gateway process group status and enabled channels."""
    gateway = _gateway()
    try:
        typer.echo(json.dumps(gateway.status(), indent=2, sort_keys=True))
    finally:
        gateway.close()


@gateway_app.command("logs")
def gateway_logs(
    channel: str = typer.Argument("telegram"),
    lines: int = typer.Option(100, "--lines", "-n"),
) -> None:
    gateway = _gateway()
    try:
        typer.echo(gateway.logs(channel, lines=lines))
    finally:
        gateway.close()


@gateway_app.command("install")
def gateway_install(
) -> None:
    """Install launchd/systemd unit files for enabled gateway channels."""
    gateway = _gateway()
    try:
        if not gateway.config.enabled_channels():
            typer.echo("No channels enabled; nothing to install.")
            raise typer.Exit(1)
        for path in gateway.install():
            typer.echo(str(path))
    finally:
        gateway.close()


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
    kb: list[str] = typer.Option(None, "--kb", help="Attach knowledge base id(s)"),
    quiet: bool = typer.Option(False, "--quiet", help="Hide live step/tool progress"),
    max_steps: Optional[int] = typer.Option(
        None, "--max-steps", help="Per-turn loop step ceiling"
    ),
    voice: bool = typer.Option(
        False,
        "--voice",
        help="Voice mode: empty Enter records mic→STT (sox/ffmpeg + STT key)",
    ),
    project: Path = typer.Option(
        Path.cwd(), "--project", "-C", help="Project root for AGENTS.md / rules / tools"
    ),
    attach: Optional[str] = typer.Option(
        None,
        "--attach",
        "-a",
        help="Attach turns to App Server daemon (unix:// | ws:// | auto)",
    ),
) -> None:
    """Interactive chat — ask, then follow up in the same session workspace."""
    from kageha.chat.repl import run_chat_repl

    asyncio.run(
        run_chat_repl(
            resume=resume,
            auto_approve=auto_approve,
            max_steps=max_steps,
            quiet=quiet,
            kb=list(kb or []),
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


@app.command("sandbox")
def sandbox_cmd() -> None:
    """Show OS shell + browser sandbox status."""
    from kageha.harness.browser_sandbox import browser_sandbox_status
    from kageha.harness.shell_sandbox import (
        describe_sandbox_for_help,
        docker_read_only_root,
        sandbox_status,
        workspace_access,
    )
    from kageha.harness.terminal_backend import describe_modal_status

    st = sandbox_status()
    typer.echo(describe_sandbox_for_help())
    typer.echo(f"read_only_root={docker_read_only_root()} (docker)")
    typer.echo(f"workspace_access={workspace_access()}")
    typer.echo(describe_modal_status())
    typer.echo(
        "Serverless: KAGEHA_SANDBOX=modal (+ MODAL_TOKEN_* / modal token); "
        "optional image via KAGEHA_SANDBOX_MODAL_IMAGE. "
        "Remote sandboxes have network — elevate still HITL."
    )
    bs = browser_sandbox_status()
    typer.echo(
        f"browser_mode={bs['mode_env']} docker_backend={bs['docker_backend']} "
        f"docker_available={bs['docker_available']} image={bs['image']}"
    )
    if not st.available and st.profile != "off":
        raise typer.Exit(code=1)


@daemon_app.command("schedule-status")
def daemon_schedule_status() -> None:
    """Show scheduled jobs (curator cron, etc.)."""
    from kageha.daemon.schedule import status_text

    typer.echo(status_text())


@daemon_app.command("tick")
def daemon_tick(
    force: bool = typer.Option(False, "--force", help="Run even if not due"),
) -> None:
    """Run due scheduled jobs (platform=cron semantics for curator)."""
    from kageha.daemon.schedule import run_tick

    for line in run_tick(force=force):
        typer.echo(line)


@daemon_app.command("schedule-install")
def daemon_schedule_install() -> None:
    """Install OS scheduler (launchd on macOS, crontab snippet elsewhere)."""
    from kageha.daemon.schedule import install_scheduler

    typer.echo(install_scheduler())


@daemon_app.command("schedule-uninstall")
def daemon_schedule_uninstall() -> None:
    """Remove OS scheduler hooks."""
    from kageha.daemon.schedule import uninstall_scheduler

    typer.echo(uninstall_scheduler())


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


@app.command("review")
def review_cmd(
    base: str = typer.Option("main", "--base", "-b", help="Base ref"),
    head: str = typer.Option("HEAD", "--head", help="Head ref"),
    project: Path = typer.Option(Path.cwd(), "--project", "-C", help="Project root"),
    promote: bool = typer.Option(
        False, "--promote-rules", help="Write HIGH+ findings to .kageha/rules/"
    ),
    auto_approve: bool = typer.Option(True, "--auto-approve/--no-auto-approve"),
) -> None:
    """Defect-first review of the git diff (Claude ultrareview / Bugbot lite)."""
    from kageha.project.review import run_review

    result = asyncio.run(
        run_review(
            project_root=project,
            base=base,
            head=head,
            promote_rules=promote,
            auto_approve=auto_approve,
        )
    )
    typer.echo(json.dumps(result.to_dict(), indent=2))
    if result.findings and any(
        f.severity in {"CRITICAL", "HIGH"} for f in result.findings
    ):
        raise typer.Exit(code=1)


@app.command("babysit")
def babysit_cmd(
    pr: str = typer.Argument(..., help="PR number or URL"),
    project: Path = typer.Option(Path.cwd(), "--project", "-C"),
    max_rounds: int = typer.Option(3, "--rounds", "-n"),
    auto_approve: bool = typer.Option(True, "--auto-approve/--no-auto-approve"),
) -> None:
    """Poll PR checks and fix failures until green or stuck (Cursor babysit)."""
    from kageha.project.review import babysit_pr

    result = asyncio.run(
        babysit_pr(
            pr=pr,
            project_root=project,
            max_rounds=max_rounds,
            auto_approve=auto_approve,
        )
    )
    typer.echo(json.dumps(result.to_dict(), indent=2))
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("best-of-n")
def best_of_n_cmd(
    objective: str = typer.Argument(..., help="Task for each isolated attempt"),
    n: int = typer.Option(2, "--n", "-n", min=2, max=5),
    project: Path = typer.Option(Path.cwd(), "--project", "-C"),
    max_steps: int = typer.Option(24, "--max-steps"),
    keep_losers: bool = typer.Option(False, "--keep-losers"),
    auto_approve: bool = typer.Option(True, "--auto-approve/--no-auto-approve"),
) -> None:
    """Run N worktree-isolated attempts and pick a winner (Cursor /best-of-n)."""
    from kageha.project.best_of_n import format_best_of_n, run_best_of_n

    result = asyncio.run(
        run_best_of_n(
            objective=objective,
            project_root=project,
            n=n,
            max_steps=max_steps,
            auto_approve=auto_approve,
            keep_losers=keep_losers,
        )
    )
    typer.echo(format_best_of_n(result))


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


@cloud_app.command("run")
def cloud_run_cmd(
    objective: str = typer.Argument(..., help="Background job objective"),
    project: Path = typer.Option(Path.cwd(), "--project", "-C"),
    agent_mode: str = typer.Option("plan", "--mode"),
    max_steps: int = typer.Option(40, "--max-steps"),
    wait: bool = typer.Option(False, "--wait", help="Block until the job finishes"),
    notify: str = typer.Option(
        "",
        "--notify",
        help="Optional channel key recorded for completion pollers",
    ),
) -> None:
    """Enqueue a durable background job (Codex cloud / Cursor background lite)."""
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


@cloud_app.command("list")
def cloud_list_cmd(limit: int = typer.Option(20, "--limit")) -> None:
    """List recent background jobs."""
    from kageha.project.async_jobs import list_jobs

    typer.echo(
        json.dumps([j.to_dict() for j in list_jobs(limit=limit)], indent=2)
    )


@cloud_app.command("status")
def cloud_status_cmd(job_id: str = typer.Argument(...)) -> None:
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


@computer_app.command("doctor")
def computer_doctor_cmd() -> None:
    """Probe driver, permissions, and tool-calling model (same as /computer doctor)."""

    async def _run() -> str:
        from kageha.chat.computer_commands import handle_computer_command

        _handled, msg = await handle_computer_command("/computer doctor")
        return msg

    typer.echo(asyncio.run(_run()))


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
@app.command("serve-ui")
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


@app.command("eval")
def eval_cmd(
    suite: Path = typer.Option(Path("tests/golden/suite.json"), "--suite"),
    auto_approve: bool = typer.Option(True, "--auto-approve/--no-auto-approve"),
) -> None:
    """Run golden-task evaluation suite."""
    from kageha.eval.harness import run_suite, summary

    async def _run() -> None:
        results = await run_suite(suite, auto_approve=auto_approve)
        s = summary(results)
        typer.echo(json.dumps(s, indent=2))
        if s["failed"]:
            raise typer.Exit(code=1)

    asyncio.run(_run())


# --- models ---


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


@models_app.command("doctor")
def models_doctor(
    no_smoke: bool = typer.Option(False, "--no-smoke", help="Skip API smoke tests"),
    model_id: Optional[str] = typer.Option(None, "--model", help="Smoke only this model"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON report"),
    plain: bool = typer.Option(False, "--plain", help="Disable Rich TUI formatting"),
    fix: bool = typer.Option(False, "--fix", help="Offer models setup when checks fail"),
) -> None:
    """Diagnose models, keys, roles, sandbox, and tools.yaml."""
    from kageha.models.doctor import (
        format_doctor_report,
        maybe_fix_interactive,
        run_models_doctor,
    )

    report = run_models_doctor(smoke=not no_smoke, model_id=model_id)
    if as_json:
        typer.echo(json.dumps(report.to_dict(), indent=2))
    else:
        text = format_doctor_report(report, rich=not plain)
        typer.echo(text.rstrip("\n"))
    if fix:
        maybe_fix_interactive(report)
    if not report.ok:
        raise typer.Exit(1)


# --- skills ---


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


@curator_app.command("status")
def curator_status(
    days: int = typer.Option(30, "--days", help="Stale threshold in days"),
) -> None:
    """Show usage, pin, stale, and archive status for user skills."""
    from kageha.memory.curator import status_rows

    rows = status_rows(stale_days=days)
    if not rows:
        typer.echo("(no user skills tracked)")
        return
    for r in rows:
        flags = []
        if r.pinned:
            flags.append("pinned")
        if r.stale:
            flags.append("stale")
        if r.archived:
            flags.append("archived")
        flag_s = ",".join(flags) if flags else "active"
        typer.echo(
            f"{r.name:28} loads={r.loads:<4} last={r.last_used:22} [{flag_s}]"
        )


@curator_app.command("run")
def curator_run(
    days: int = typer.Option(30, "--days"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    consolidate: bool = typer.Option(False, "--consolidate"),
    yes: bool = typer.Option(False, "--yes", help="Allow consolidate writes"),
) -> None:
    """Archive stale unpinned user skills; optional LLM consolidate."""
    from kageha.memory.curator import consolidate_skills, run_curator

    for line in run_curator(days=days, dry_run=dry_run):
        typer.echo(line)
    if consolidate:
        typer.echo(asyncio.run(consolidate_skills(yes=yes)))


@curator_app.command("pin")
def curator_pin(name: str = typer.Argument(...)) -> None:
    from kageha.memory.curator import set_pinned

    typer.echo(set_pinned(name, True))


@curator_app.command("unpin")
def curator_unpin(name: str = typer.Argument(...)) -> None:
    from kageha.memory.curator import set_pinned

    typer.echo(set_pinned(name, False))


@curator_app.command("restore")
def curator_restore(
    name: str = typer.Argument(...),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    from kageha.memory.curator import restore_skill

    typer.echo(restore_skill(name, dry_run=dry_run))


# --- mcp ---


@mcp_app.command("list")
def mcp_list() -> None:
    """List configured MCP servers (from mcp.yaml / Cursor / Claude Desktop)."""
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
    """Run Kageha as an MCP server over stdio (expose tools to Cursor/Claude/etc.).

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


# --- connect (OAuth integrations) ---


@connect_app.command("list")
def connect_list() -> None:
    """List available connection providers and login status."""
    from kageha.connections.registry import list_providers
    from kageha.connections.store import ConnectionStore

    store = ConnectionStore()
    rows = []
    for p in list_providers():
        st = p.status(store=store)
        rows.append(
            {
                "id": p.id,
                "label": p.label,
                "connected": st.connected,
                "account": st.account,
                "description": p.description,
            }
        )
    typer.echo(json.dumps(rows, indent=2))


@connect_app.command("credentials")
def connect_credentials(
    path: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Downloaded Google Desktop client_secret JSON",
    ),
    also_env: bool = typer.Option(
        False,
        "--env/--no-env",
        help="Also write Client ID/Secret into project .env",
    ),
) -> None:
    """Register Google Desktop OAuth client JSON (like ``gog auth credentials``)."""
    from kageha.connections.setup import install_google_client_json

    try:
        result = install_google_client_json(path, also_env=also_env)
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))
    typer.echo("Next: kageha connect login gmail")


@connect_app.command("setup")
def connect_setup(
    what: str = typer.Argument(
        "google",
        help="Which credentials to configure (google)",
    ),
    credentials: Optional[Path] = typer.Option(
        None,
        "--credentials",
        "-c",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to client_secret_….json (gog-style)",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Overwrite existing client without asking"
    ),
    also_env: bool = typer.Option(
        False,
        "--env/--no-env",
        help="Also write Client ID/Secret into project .env",
    ),
) -> None:
    """Register Google OAuth client (JSON file or interactive paste)."""
    from kageha.connections.setup import run_google_oauth_setup

    key = (what or "google").strip().lower()
    if key not in {"google", "gmail", "gcal", "gdrive"}:
        typer.echo(
            "ERROR: only 'google' setup is supported "
            f"(aliases: gmail, gcal, gdrive). Got: {what}",
            err=True,
        )
        raise typer.Exit(1)
    try:
        result = run_google_oauth_setup(
            yes=yes, credentials=credentials, also_env=also_env
        )
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc
    safe = {
        k: v
        for k, v in result.items()
        if k not in {"GOOGLE_OAUTH_CLIENT_SECRET", "client_secret"}
    }
    typer.echo(json.dumps(safe, indent=2))


@connect_app.command("login")
def connect_login(
    provider: str = typer.Argument(..., help="Provider id (gmail, gcal, gdrive, github)"),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Print URL only; do not open a browser"
    ),
    no_setup: bool = typer.Option(
        False,
        "--no-setup",
        help="Do not prompt for Google client credentials when missing",
    ),
) -> None:
    """Interactive OAuth login for a connection provider."""
    from kageha.connections.registry import get_provider
    from kageha.connections.setup import ensure_google_oauth_client
    from kageha.connections.store import ConnectionStore

    try:
        p = get_provider(provider)
    except KeyError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc
    if p.id in {"gmail", "gcal", "gdrive"}:
        try:
            ensure_google_oauth_client(interactive=not no_setup)
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"ERROR: {exc}", err=True)
            raise typer.Exit(1) from exc
    store = ConnectionStore()
    try:
        st = p.login(store=store, open_browser=not no_browser)
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(st.as_dict(), indent=2))


@connect_app.command("status")
def connect_status(
    provider: Optional[str] = typer.Argument(
        None, help="Provider id (default: all)"
    ),
) -> None:
    """Show connection status (refreshes tokens when possible)."""
    from kageha.connections.registry import get_provider, list_providers
    from kageha.connections.store import ConnectionStore

    store = ConnectionStore()
    if provider:
        try:
            p = get_provider(provider)
        except KeyError as exc:
            typer.echo(f"ERROR: {exc}", err=True)
            raise typer.Exit(1) from exc
        st = p.status(store=store)
        typer.echo(json.dumps(st.as_dict(), indent=2))
        if not st.connected:
            raise typer.Exit(1)
        return
    rows = [p.status(store=store).as_dict() for p in list_providers()]
    typer.echo(json.dumps(rows, indent=2))


@connect_app.command("logout")
def connect_logout(
    provider: str = typer.Argument(..., help="Provider id to disconnect"),
) -> None:
    """Delete stored credentials for a provider."""
    from kageha.connections.registry import get_provider
    from kageha.connections.store import ConnectionStore

    try:
        p = get_provider(provider)
    except KeyError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc
    store = ConnectionStore()
    st = p.logout(store=store)
    typer.echo(json.dumps(st.as_dict(), indent=2))


# --- kb ---


@kb_app.command("create")
def kb_create(
    kb_id: str,
    engine: str = typer.Option("zvec", "--engine"),
    source: list[Path] = typer.Option(None, "--source"),
    embed: bool = typer.Option(True, "--embed/--no-embed"),
) -> None:
    from kageha.knowledge.facade import KnowledgeFacade

    sources = [str(s) for s in (source or [])]
    facade = KnowledgeFacade()
    kb = facade.create(kb_id, engine=engine, sources=sources if embed else None)
    if sources and not embed:
        typer.echo(f"Created empty KB {kb.kb_id}; sources not ingested")
    else:
        typer.echo(f"Created KB {kb.kb_id} engine={kb.engine} at {kb.root}")


@kb_app.command("ingest")
def kb_ingest(kb_id: str, sources: list[str] = typer.Argument(...)) -> None:
    from kageha.knowledge.facade import KnowledgeFacade

    result = KnowledgeFacade().ingest(kb_id, list(sources))
    typer.echo(json.dumps(result, indent=2))


@kb_app.command("list")
def kb_list() -> None:
    from kageha.knowledge.facade import list_kbs

    for item in list_kbs():
        typer.echo(f"{item.get('id')} engine={item.get('engine')} sources={len(item.get('sources') or [])}")


@kb_app.command("attach")
def kb_attach(kb_id: str, user: bool = typer.Option(False, "--user")) -> None:
    from kageha.knowledge.registry import attach

    path = attach(kb_id, project=not user)
    typer.echo(f"Attached {kb_id} in {path}")


@kb_app.command("search")
def kb_search(kb_id: str, query: str) -> None:
    from kageha.knowledge.facade import KnowledgeFacade

    hits = KnowledgeFacade().search(kb_id, query)
    typer.echo(json.dumps(hits, indent=2)[:8000])


@kb_app.command("delete")
def kb_delete(kb_id: str) -> None:
    from kageha.knowledge.facade import KnowledgeFacade

    KnowledgeFacade().delete(kb_id)
    typer.echo(f"Deleted {kb_id}")


@app.command("telegram")
def telegram_cmd(
    auto_approve_tasks: bool = typer.Option(False, "--auto-approve-tasks"),
) -> None:
    """Default channel: poll Telegram for tasks (outbound sends still HITL)."""
    from kageha.channels.telegram import TelegramChannel

    ch = TelegramChannel()
    if not ch.available:
        typer.echo("TELEGRAM_BOT_TOKEN missing")
        raise typer.Exit(1)
    if ch.allowed_users is not None and len(ch.allowed_users) == 0:
        typer.echo(
            "TELEGRAM_ALLOWED_USERS is empty (fail-closed). "
            "Set chat ids or TELEGRAM_ALLOW_ALL_USERS=1"
        )
        raise typer.Exit(1)
    asyncio.run(ch.poll_and_run(auto_approve_tasks=auto_approve_tasks))


@app.command("discord")
def discord_cmd(
    auto_approve_tasks: bool = typer.Option(False, "--auto-approve-tasks"),
) -> None:
    """Optional channel: Discord bot (prefer telegram/whatsapp/teams for defaults)."""
    from kageha.channels.discord import DiscordChannel

    ch = DiscordChannel()
    if not ch.available:
        typer.echo("DISCORD_BOT_TOKEN missing")
        raise typer.Exit(1)
    if ch.allowed_users is not None and len(ch.allowed_users) == 0:
        typer.echo(
            "DISCORD_ALLOWED_USERS is empty (fail-closed). "
            "Set user ids or DISCORD_ALLOW_ALL_USERS=1"
        )
        raise typer.Exit(1)
    asyncio.run(ch.poll_and_run(auto_approve_tasks=auto_approve_tasks))


@app.command("slack")
def slack_cmd(
    auto_approve_tasks: bool = typer.Option(False, "--auto-approve-tasks"),
) -> None:
    """Run Slack Socket Mode bot. Requires SLACK_BOT_TOKEN + SLACK_APP_TOKEN."""
    from kageha.channels.slack import SlackChannel

    ch = SlackChannel()
    if not ch.available:
        typer.echo("SLACK_BOT_TOKEN and SLACK_APP_TOKEN required")
        raise typer.Exit(1)
    if ch.allowed_users is not None and len(ch.allowed_users) == 0:
        typer.echo(
            "SLACK_ALLOWED_USERS is empty (fail-closed). "
            "Set user ids or SLACK_ALLOW_ALL_USERS=1"
        )
        raise typer.Exit(1)
    asyncio.run(ch.poll_and_run(auto_approve_tasks=auto_approve_tasks))


@app.command("whatsapp")
def whatsapp_cmd(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8787, "--port"),
    path: str = typer.Option("/webhook/whatsapp", "--path"),
    auto_approve_tasks: bool = typer.Option(
        False,
        "--auto-approve-tasks",
        help="Skip in-chat tool approvals (not recommended)",
    ),
) -> None:
    """Default channel: WhatsApp Cloud API webhook.

    Requires: WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_ALLOWED_USERS
    Optional: WHATSAPP_APP_SECRET, WHATSAPP_VERIFY_TOKEN, WHATSAPP_ALLOW_ALL_USERS
    Install: uv sync --extra channels
    Expose HTTPS via cloudflared/ngrok to Meta's Callback URL.
    """
    from kageha.channels.whatsapp import WhatsAppChannel

    ch = WhatsAppChannel()
    if not ch.available:
        typer.echo("Set WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID")
        raise typer.Exit(1)
    asyncio.run(
        ch.serve(
            host=host,
            port=port,
            path=path,
            auto_approve_tasks=auto_approve_tasks,
        )
    )


@app.command("whatsapp-setup")
def whatsapp_setup_cmd(
    start: bool = typer.Option(
        False,
        "--start/--no-start",
        help="Start QR bridge after saving (default: ask)",
    ),
    no_prompt_start: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Save and start without asking 'start now?'",
    ),
) -> None:
    """Ask for your WhatsApp number and write it to .env, then optionally start QR bridge."""
    from kageha.channels.whatsapp_setup import run_whatsapp_setup

    start_after: bool | None
    if no_prompt_start or start:
        start_after = True
    else:
        start_after = None  # wizard asks
    result = run_whatsapp_setup(start_after=start_after)
    if result.get("start") == "1":
        from kageha.channels.whatsapp_qr import WhatsAppQRChannel

        typer.echo("Starting WhatsApp QR bridge…")
        asyncio.run(WhatsAppQRChannel().run_forever())


@app.command("whatsapp-qr")
def whatsapp_qr_cmd(
    auto_approve_tasks: bool = typer.Option(
        False,
        "--auto-approve-tasks",
        help="Skip in-chat tool approvals (not recommended)",
    ),
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Delete saved linked-device session before start (new QR)",
    ),
    setup: bool = typer.Option(
        False,
        "--setup",
        help="Run number setup wizard before starting",
    ),
) -> None:
    """Optional WhatsApp path: Baileys QR linked device (prefer Cloud API default).

    No Meta Business app. Requires Node.js. Session: ~/.kageha/platforms/whatsapp/session
    If allowlist is missing, prompts to set your phone number in .env.
    Unofficial — ban risk; prefer a dedicated number.
    """
    import os
    import shutil

    from kageha.channels.whatsapp_qr import WhatsAppQRChannel, default_auth_dir
    from kageha.channels.whatsapp_setup import needs_whatsapp_setup, run_whatsapp_setup
    from kageha.config import load_env


    if setup or needs_whatsapp_setup():
        if needs_whatsapp_setup() and not setup:
            typer.echo("WhatsApp allowlist not set — running setup…")
        run_whatsapp_setup(start_after=False)
        load_env()

    if reset:
        auth = Path(os.environ.get("KAGEHA_WA_AUTH_DIR") or default_auth_dir())
        if auth.is_dir():
            shutil.rmtree(auth)
            typer.echo(f"Cleared session {auth}")
        auth.mkdir(parents=True, exist_ok=True)

    ch = WhatsAppQRChannel(auto_approve_tasks=auto_approve_tasks)
    asyncio.run(ch.run_forever())


@app.command("signal")
def signal_cmd(
    auto_approve_tasks: bool = typer.Option(False, "--auto-approve-tasks"),
) -> None:
    """Poll Signal via signal-cli JSON-RPC + SSE (chat HITL).

    Requires a running signal-cli daemon. Env: SIGNAL_HTTP_URL, SIGNAL_ALLOWED_USERS
    Optional: SIGNAL_ACCOUNT, SIGNAL_ALLOW_ALL_USERS=1, KAGEHA_SIGNAL_HITL=0
    """
    from kageha.channels.signal import SignalChannel

    ch = SignalChannel()
    if not ch.available:
        typer.echo("SIGNAL_HTTP_URL missing (e.g. http://127.0.0.1:8080)")
        raise typer.Exit(1)
    if ch.allowed_users is not None and len(ch.allowed_users) == 0:
        typer.echo(
            "SIGNAL_ALLOWED_USERS is empty (fail-closed). "
            "Set E.164 numbers or SIGNAL_ALLOW_ALL_USERS=1"
        )
        raise typer.Exit(1)
    asyncio.run(ch.poll_and_run(auto_approve_tasks=auto_approve_tasks))


@app.command("matrix")
def matrix_cmd(
    auto_approve_tasks: bool = typer.Option(False, "--auto-approve-tasks"),
) -> None:
    """Poll Matrix /sync and answer in rooms (chat HITL).

    Env: MATRIX_HOMESERVER, MATRIX_ACCESS_TOKEN, MATRIX_ALLOWED_USERS
    Optional: MATRIX_USER_ID, MATRIX_ALLOW_ALL_USERS=1, KAGEHA_MATRIX_HITL=0
    Unencrypted rooms recommended (no E2EE crypto store in this adapter).
    """
    from kageha.channels.matrix import MatrixChannel

    ch = MatrixChannel()
    if not ch.available:
        typer.echo("MATRIX_HOMESERVER and MATRIX_ACCESS_TOKEN required")
        raise typer.Exit(1)
    if ch.allowed_users is not None and len(ch.allowed_users) == 0:
        typer.echo(
            "MATRIX_ALLOWED_USERS is empty (fail-closed). "
            "Set MXIDs (@user:server) or MATRIX_ALLOW_ALL_USERS=1"
        )
        raise typer.Exit(1)
    asyncio.run(ch.poll_and_run(auto_approve_tasks=auto_approve_tasks))


@app.command("email")
def email_cmd(
    auto_approve_tasks: bool = typer.Option(False, "--auto-approve-tasks"),
) -> None:
    """Poll IMAP for tasks and reply via SMTP (chat HITL via reply).

    Password auth: EMAIL_IMAP_HOST/USER/PASSWORD, EMAIL_SMTP_HOST, EMAIL_FROM
    Or Gmail OAuth: ``kageha connect login gmail`` (XOAUTH2; hosts default to Gmail)
    Optional: EMAIL_ALLOWED_USERS, EMAIL_ALLOW_ALL_USERS=1
    """
    from kageha.channels.email import EmailChannel

    ch = EmailChannel()
    if not ch.available:
        typer.echo(
            "Configure email via EMAIL_IMAP_* + EMAIL_SMTP_* env vars, "
            "or run: kageha connect login gmail"
        )
        raise typer.Exit(1)
    if ch.allowed_users is not None and len(ch.allowed_users) == 0:
        typer.echo(
            "EMAIL_ALLOWED_USERS is empty (fail-closed). "
            "Set addresses or EMAIL_ALLOW_ALL_USERS=1"
        )
        raise typer.Exit(1)
    asyncio.run(ch.poll_and_run(auto_approve_tasks=auto_approve_tasks))


@app.command("imessage")
def imessage_cmd(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8790, "--port"),
    path: str = typer.Option("/webhook/imessage", "--path"),
    auto_approve_tasks: bool = typer.Option(False, "--auto-approve-tasks"),
) -> None:
    """iMessage via BlueBubbles Server webhook + REST (macOS).

    Env: BLUEBUBBLES_URL, BLUEBUBBLES_PASSWORD, IMESSAGE_ALLOWED_USERS
    Point BlueBubbles webhooks at this path (tunnel HTTPS if needed).
    Install: uv sync --extra channels
    """
    from kageha.channels.imessage import iMessageChannel

    ch = iMessageChannel()
    if not ch.available:
        typer.echo("BLUEBUBBLES_URL and BLUEBUBBLES_PASSWORD required")
        raise typer.Exit(1)
    if ch.allowed_users is not None and len(ch.allowed_users) == 0:
        typer.echo(
            "IMESSAGE_ALLOWED_USERS is empty (fail-closed). "
            "Set handles or IMESSAGE_ALLOW_ALL_USERS=1"
        )
        raise typer.Exit(1)
    asyncio.run(
        ch.serve(
            host=host,
            port=port,
            path=path,
            auto_approve_tasks=auto_approve_tasks,
        )
    )


@app.command("irc")
def irc_cmd(
    auto_approve_tasks: bool = typer.Option(False, "--auto-approve-tasks"),
) -> None:
    """IRC channel (TLS by default) with chat HITL.

    Env: IRC_HOST, IRC_NICK, IRC_CHANNELS=#chan1,#chan2, IRC_ALLOWED_USERS
    """
    from kageha.channels.irc import IRCChannel

    ch = IRCChannel()
    if not ch.available:
        typer.echo("IRC_HOST, IRC_NICK, and IRC_CHANNELS required")
        raise typer.Exit(1)
    if ch.allowed_users is not None and len(ch.allowed_users) == 0:
        typer.echo(
            "IRC_ALLOWED_USERS is empty (fail-closed). "
            "Set nicks or IRC_ALLOW_ALL_USERS=1"
        )
        raise typer.Exit(1)
    asyncio.run(ch.poll_and_run(auto_approve_tasks=auto_approve_tasks))


@app.command("mattermost")
def mattermost_cmd(
    auto_approve_tasks: bool = typer.Option(False, "--auto-approve-tasks"),
) -> None:
    """Mattermost REST poller with chat HITL.

    Env: MATTERMOST_URL, MATTERMOST_TOKEN, MATTERMOST_CHANNEL_IDS, MATTERMOST_ALLOWED_USERS
    """
    from kageha.channels.mattermost import MattermostChannel

    ch = MattermostChannel()
    if not ch.available:
        typer.echo("MATTERMOST_URL, MATTERMOST_TOKEN, MATTERMOST_CHANNEL_IDS required")
        raise typer.Exit(1)
    if ch.allowed_users is not None and len(ch.allowed_users) == 0:
        typer.echo(
            "MATTERMOST_ALLOWED_USERS is empty (fail-closed). "
            "Set user ids or MATTERMOST_ALLOW_ALL_USERS=1"
        )
        raise typer.Exit(1)
    asyncio.run(ch.poll_and_run(auto_approve_tasks=auto_approve_tasks))


@app.command("teams")
def teams_cmd(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8791, "--port"),
    path: str = typer.Option("/webhook/teams", "--path"),
    auto_approve_tasks: bool = typer.Option(False, "--auto-approve-tasks"),
) -> None:
    """Default channel: Microsoft Teams Incoming Webhook + inbound HTTP webhook.

    Env: TEAMS_WEBHOOK_URL, TEAMS_ALLOWED_USERS
    Install: uv sync --extra channels
    """
    from kageha.channels.teams import TeamsChannel

    ch = TeamsChannel()
    if not ch.available:
        typer.echo("TEAMS_WEBHOOK_URL required")
        raise typer.Exit(1)
    if ch.allowed_users is not None and len(ch.allowed_users) == 0:
        typer.echo(
            "TEAMS_ALLOWED_USERS is empty (fail-closed). "
            "Set users or TEAMS_ALLOW_ALL_USERS=1"
        )
        raise typer.Exit(1)
    asyncio.run(
        ch.serve(
            host=host,
            port=port,
            path=path,
            auto_approve_tasks=auto_approve_tasks,
        )
    )


@app.command("setup")
def setup_cmd() -> None:
    """Multi-screen setup wizard (Textual if installed, else Rich prompts).

    Covers Gemini key, Google OAuth client JSON, and a primary chat channel.
    """
    from kageha.setup_wizard import run_setup_wizard

    result = run_setup_wizard()
    typer.echo(json.dumps(result, indent=2))


@bravia_app.command("discover")
def bravia_discover_cmd() -> None:
    """Scan Wi‑Fi for Sony Bravia / Android TV ADB."""
    from kageha.devices.android_tv import discover_tv_candidates

    typer.echo(json.dumps(discover_tv_candidates(), indent=2))


@bravia_app.command("status")
def bravia_status_cmd(
    host: str = typer.Option("", help="TV IP (default: env or auto-discover)"),
) -> None:
    """Show power/volume and whether cookie pairing / PSK is ready."""
    from kageha.devices import bravia as bravia_mod

    h = bravia_mod.resolve_host(host)
    if not h:
        typer.echo("No Bravia found. Is the TV on the same Wi‑Fi?", err=True)
        raise typer.Exit(1)
    client = bravia_mod.client_from_env(h)
    assert client is not None
    prof = bravia_mod.load_profile(h)
    code, data, _ = client.rpc("appControl", "getApplicationList")
    typer.echo(
        json.dumps(
            {
                "host": h,
                "power": client.power_status(),
                "volume": client.volume_info(),
                "paired": bool(prof and prof.get("paired") and prof.get("cookies")),
                "psk_env": bool(bravia_mod._psk()),
                "apps_auth_ok": code < 400 and "error" not in data,
                "apps_error": data.get("error"),
            },
            indent=2,
        )
    )


@bravia_app.command("pair")
def bravia_pair_cmd(
    host: str = typer.Option("", help="TV IP (default: env or auto-discover)"),
    pin: str = typer.Option("", help="PIN from TV (omit to request a new PIN)"),
) -> None:
    """PIN-pair with a Sony Bravia (cookie auth stored in ~/.kageha/bravia/)."""
    from kageha.devices import bravia as bravia_mod

    h = bravia_mod.resolve_host(host)
    if not h:
        typer.echo("No Bravia found on Wi‑Fi. Set --host or KAGEHA_BRAVIA_HOST.", err=True)
        raise typer.Exit(1)
    if not pin:
        result = bravia_mod.pair_start(h)
        typer.echo(json.dumps(result, indent=2))
        if result.get("awaiting_pin"):
            entered = typer.prompt("Enter PIN shown on the TV")
            result = bravia_mod.pair_finish(h, entered)
            typer.echo(json.dumps(result, indent=2))
            if not result.get("ok"):
                raise typer.Exit(1)
            typer.echo(f"Tip: add KAGEHA_BRAVIA_HOST={h} to .env")
        return
    result = bravia_mod.pair_finish(h, pin)
    typer.echo(json.dumps(result, indent=2))
    if not result.get("ok"):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
