"""Project brain + hooks + worktree helpers."""

from __future__ import annotations

import json
from pathlib import Path

from kageha.project.brain import (
    load_project_brain,
    load_project_command,
    render_project_brain,
)
from kageha.project.hooks import HookRunner, HookSpec, load_hook_runner
from kageha.project.worktree import create_worktree, is_git_repo, list_worktrees
from kageha.app_server_listen import parse_listen_url
from kageha.project.async_jobs import enqueue_job, jobs_dir, load_job


def test_load_agents_md_prefers_agents_over_claude(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("# Agents\nUse pnpm.\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Claude\nUse npm.\n", encoding="utf-8")
    brain = load_project_brain(tmp_path)
    assert brain is not None
    assert brain.root_file == "AGENTS.md"
    text = render_project_brain(brain)
    assert "Use pnpm" in text
    assert "Use npm" not in text


def test_load_claude_md_fallback(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("Always run tests.\n", encoding="utf-8")
    brain = load_project_brain(tmp_path)
    assert brain is not None
    assert brain.root_file == "CLAUDE.md"
    assert "Always run tests" in render_project_brain(brain)


def test_kageha_rules_and_commands(tmp_path: Path):
    rules = tmp_path / ".kageha" / "rules"
    rules.mkdir(parents=True)
    (rules / "style.md").write_text(
        "---\nglobs: **/*.py\n---\n\nNo bare except.\n",
        encoding="utf-8",
    )
    (rules / "always.md").write_text("Prefer typed APIs.\n", encoding="utf-8")
    cmds = tmp_path / ".kageha" / "commands"
    cmds.mkdir(parents=True)
    (cmds / "review.md").write_text("Review the diff carefully.\n", encoding="utf-8")
    (tmp_path / "KAGEHA.md").write_text("Project: demo\n", encoding="utf-8")

    brain = load_project_brain(tmp_path)
    assert brain is not None
    assert "review" in brain.command_names
    rendered = render_project_brain(brain, touched_paths=["src/foo.py"])
    assert "Prefer typed APIs" in rendered
    assert "No bare except" in rendered
    assert load_project_command(tmp_path, "review") == "Review the diff carefully."


def test_hooks_pre_tool_deny(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # Point kageha_home via KAGEHA_HOME
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    hooks_path = tmp_path / ".kageha" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "preToolUse": [
                        {
                            "matcher": "bash",
                            "deny_message": "bash blocked in tests",
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    runner = load_hook_runner(tmp_path)
    blocked = runner.run("preToolUse", tool_name="bash", payload={"arguments": {}})
    assert blocked.allowed is False
    assert "bash blocked" in blocked.message


def test_hook_command_exit_2_blocks(tmp_path: Path):
    runner = HookRunner(
        hooks=[
            HookSpec(
                event="preToolUse",
                command="exit 2",
                deny_message="nope",
            )
        ],
        project_root=tmp_path,
    )
    result = runner.run("preToolUse", tool_name="write_file", payload={})
    assert result.allowed is False


def test_parse_listen_url():
    assert parse_listen_url("stdio://")[0] == "stdio"
    kind, path = parse_listen_url("unix://")
    assert kind == "unix"
    assert path.endswith("app-server.sock")
    kind, url = parse_listen_url("ws://127.0.0.1:4500")
    assert kind == "ws"
    assert "4500" in url


def test_worktree_create_and_list(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert is_git_repo(tmp_path)
    handle = create_worktree(tmp_path, label="agent")
    assert handle.path.is_dir()
    assert (handle.path / "README.md").is_file()
    rows = list_worktrees(tmp_path)
    assert any(str(handle.path) in (r.get("path") or "") for r in rows)
    handle.remove(force=True)


def test_async_job_enqueue_without_start(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    job = enqueue_job(
        objective="noop",
        project_root=str(tmp_path),
        start=False,
    )
    assert job.status == "queued"
    assert (jobs_dir() / f"{job.id}.json").is_file()
    loaded = load_job(job.id)
    assert loaded is not None
    assert loaded.objective == "noop"
    assert loaded.session_id == loaded.id
    assert loaded.thread_id == f"job-{loaded.id}"


def test_async_job_resume_auto_build(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    job = enqueue_job(
        objective="Execute the approved plan.",
        project_root=str(tmp_path),
        session_id="plan-session-1",
        auto_build=True,
        agent_mode="plan",
        start=False,
    )
    loaded = load_job(job.id)
    assert loaded is not None
    assert loaded.session_id == "plan-session-1"
    assert loaded.run_id == "plan-session-1"
    assert loaded.auto_build is True
    assert loaded.id != "plan-session-1"


def test_jobs_run_rejects_bare_build_slash(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner

    from kageha.cli import app
    from kageha.project.async_jobs import save_job

    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    waiting = enqueue_job(
        objective="/plan make an ad",
        project_root=str(tmp_path),
        start=False,
    )
    waiting.status = "awaiting_plan_approval"
    save_job(waiting)

    runner = CliRunner()
    result = runner.invoke(app, ["jobs", "run", "/build make an ad"])
    assert result.exit_code == 2
    out = (result.stdout or "") + (result.stderr or "")
    assert "--resume" in out
    assert waiting.session_id in out


def test_async_job_list_filter_cancel_attach(tmp_path: Path, monkeypatch):
    from kageha.project.async_jobs import (
        attach_info,
        cancel_job,
        job_to_api_dict,
        list_jobs,
        save_job,
    )

    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    queued = enqueue_job(
        objective="stay queued",
        project_root=str(tmp_path),
        start=False,
    )
    running = enqueue_job(
        objective="pretend running",
        project_root=str(tmp_path),
        start=False,
    )
    running.status = "running"
    running.turn_id = "turn-abc"
    save_job(running)
    done = enqueue_job(
        objective="finished",
        project_root=str(tmp_path),
        start=False,
    )
    done.status = "success"
    save_job(done)

    active = {j.id for j in list_jobs(limit=20, status="active")}
    assert queued.id in active
    assert running.id in active
    assert done.id not in active

    done_ids = {j.id for j in list_jobs(limit=20, status="done")}
    assert done.id in done_ids
    assert queued.id not in done_ids

    cancelled = cancel_job(queued.id)
    assert cancelled.status == "cancelled"
    assert cancelled.cancel_requested

    info = attach_info(running.id)
    assert info["attachable"] is True
    assert info["session_id"] == running.session_id
    assert info["turn_id"] == "turn-abc"
    assert info["thread_id"] == f"job-{running.id}"
    assert job_to_api_dict(running)["can_cancel"] is True
    assert job_to_api_dict(queued)["attachable"] is False


def test_promote_findings_to_rules(tmp_path: Path):
    from kageha.project.review import ReviewFinding, promote_findings_to_rules

    written = promote_findings_to_rules(
        tmp_path,
        [
            ReviewFinding(
                severity="HIGH",
                summary="HIGH: SQL injection in `db.py`",
                path="db.py",
            )
        ],
    )
    assert written
    assert (tmp_path / written[0]).is_file()


def test_unix_app_server_ping():
    import asyncio
    import tempfile

    from kageha.app_server_listen import rpc_over_unix, serve_unix

    # Keep path short — AF_UNIX has a ~104 byte limit on macOS.
    sock = Path(tempfile.mkdtemp(prefix="kh-")) / "s.sock"

    async def _run() -> dict:
        task = asyncio.create_task(serve_unix(str(sock)))
        try:
            for _ in range(50):
                if sock.exists():
                    break
                await asyncio.sleep(0.02)
            return await rpc_over_unix(
                str(sock),
                {"jsonrpc": "2.0", "id": 7, "method": "ping", "params": {}},
            )
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    resp = asyncio.run(_run())
    assert resp["id"] == 7
    assert resp["result"]["pong"] is True
