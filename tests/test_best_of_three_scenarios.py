"""Parallel scenario tests for best-of-three parity surfaces."""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from kageha.app_server_client import RemoteAppServer, open_app_server, resolve_attach_url
from kageha.app_server_listen import serve_unix
from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.builtin import register
from kageha.models.router import ModelRouter
from kageha.models.registry import ModelRegistry
from kageha.project.async_jobs import enqueue_job, jobs_dir, load_job
from kageha.project.brain import load_project_brain, render_project_brain
from kageha.project.hooks import load_hook_runner, normalize_hook_event
from kageha.project.worktree import create_worktree


def _git_repo(tmp: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=tmp,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"],
        cwd=tmp,
        check=True,
        capture_output=True,
    )
    (tmp / "README.md").write_text("base\n", encoding="utf-8")
    (tmp / "src").mkdir()
    (tmp / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=tmp, check=True, capture_output=True
    )
    return tmp


def test_scenario_agents_md_and_glob_rules(tmp_path: Path):
    _git_repo(tmp_path)
    (tmp_path / "AGENTS.md").write_text("Always use ruff.\n", encoding="utf-8")
    rules = tmp_path / ".kageha" / "rules"
    rules.mkdir(parents=True)
    (rules / "py.md").write_text(
        "---\nglobs: [**/*.py]\n---\n\nNo bare except.\n",
        encoding="utf-8",
    )
    brain = load_project_brain(tmp_path)
    text = render_project_brain(brain)
    assert "Always use ruff" in text
    assert "No bare except" in text  # glob rules load even without touched_paths


def test_scenario_hook_aliases_and_block(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    cfg = tmp_path / ".kageha" / "hooks.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        json.dumps({"hooks": {"Stop": [{"command": "printf done"}]}}),
        encoding="utf-8",
    )
    assert normalize_hook_event("Stop") == "stop"
    runner = load_hook_runner(tmp_path)
    assert runner.for_event("stop")
    out = runner.run("stop", payload={"status": "success"})
    assert out.allowed is True
    assert "done" in (out.extra_context or "")


def test_scenario_worktree_tools_write_isolated(tmp_path: Path):
    repo = _git_repo(tmp_path)
    wt_a = create_worktree(repo, label="a")
    wt_b = create_worktree(repo, label="b")

    async def _write(root: Path, name: str, content: str) -> str:
        ws = SessionWorkspace.create()
        reg = ModelRegistry.load()
        ctx = HarnessContext(
            workspace=ws,
            approvals=ApprovalGate(auto_approve=True),
            router=ModelRouter(reg),
            project_root=str(root),
        )
        tools = register(ctx)
        return await tools.get("write_file").call(path=name, content=content)

    async def _run() -> None:
        await asyncio.gather(
            _write(wt_a.path, "marker.txt", "from-a\n"),
            _write(wt_b.path, "marker.txt", "from-b\n"),
        )

    asyncio.run(_run())
    assert (wt_a.path / "marker.txt").read_text() == "from-a\n"
    assert (wt_b.path / "marker.txt").read_text() == "from-b\n"
    assert not (repo / "marker.txt").exists()
    wt_a.remove(force=True)
    wt_b.remove(force=True)


def test_scenario_unix_attach_ping():
    sock = Path(tempfile.mkdtemp(prefix="kh-")) / "s.sock"

    async def _run() -> dict:
        task = asyncio.create_task(serve_unix(str(sock)))
        try:
            for _ in range(50):
                if sock.exists():
                    break
                await asyncio.sleep(0.02)
            remote = RemoteAppServer(f"unix://{sock}")
            return await remote.handle(
                {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}
            )
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    resp = asyncio.run(_run())
    assert resp["result"]["pong"] is True
    assert resolve_attach_url("auto").startswith("unix://")
    local = open_app_server(None)
    assert hasattr(local, "handle")


def test_scenario_durable_job_subprocess(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    # Enqueue without starting the heavy agent — then mark via worker path unit test
    job = enqueue_job(
        objective="noop-test",
        project_root=str(tmp_path),
        start=False,
    )
    assert job.status == "queued"
    assert (jobs_dir() / f"{job.id}.json").is_file()
    # Simulate worker completion marker path
    from kageha.project.async_jobs import _notify, save_job

    job.status = "success"
    job.message = "done https://github.com/acme/repo/pull/9"
    save_job(job)
    _notify(job)
    loaded = load_job(job.id)
    assert loaded is not None
    assert loaded.status == "success"
    assert (jobs_dir() / f"{job.id}.done").is_file()


def test_scenario_review_promote_rules(tmp_path: Path):
    from kageha.project.review import ReviewFinding, promote_findings_to_rules

    written = promote_findings_to_rules(
        tmp_path,
        [
            ReviewFinding(
                severity="HIGH",
                summary="HIGH: race in `worker.py`",
                path="worker.py",
            )
        ],
    )
    assert written
    text = (tmp_path / written[0]).read_text(encoding="utf-8")
    assert "globs: [worker.py]" in text


@pytest.mark.parametrize(
    "event",
    ["preToolUse", "PostToolUse", "beforeShell", "Stop", "SubagentStart"],
)
def test_scenario_hook_event_normalize(event: str):
    canon = normalize_hook_event(event)
    assert canon in {
        "preToolUse",
        "postToolUse",
        "beforeShell",
        "stop",
        "subagentStart",
    }
