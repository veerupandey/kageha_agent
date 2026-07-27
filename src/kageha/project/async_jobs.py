"""Durable async / cloud-style job dispatch.

Jobs are recorded under ``~/.kageha/jobs/`` and executed in a background
process so the client can disconnect. Completion can notify via a simple
JSON status file (WebUI/channels can poll).

WebUI reconnect uses ``session_id`` / ``turn_id`` (journal-backed) so a
Jobs panel can attach after a browser restart.
"""

from __future__ import annotations

import json
import os
import signal
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kageha.config import kageha_home

_TERMINAL = frozenset({"success", "error", "cancelled"})
_PAUSED = frozenset({"awaiting_plan_approval", "awaiting_clarify"})
_ACTIVE = frozenset({"queued", "running"})
_DONE = _TERMINAL


@dataclass
class JobRecord:
    id: str
    objective: str
    project_root: str
    status: str = "queued"  # queued|running|success|error|cancelled|awaiting_*
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    run_id: str = ""
    session_id: str = ""
    turn_id: str = ""
    thread_id: str = ""
    pid: int = 0
    cancel_requested: bool = False
    message: str = ""
    artifacts: list[str] = field(default_factory=list)
    error: str = ""
    agent_mode: str = "plan"
    loop_mode: str = "full"
    max_steps: int = 40
    notify_channel: str = ""
    pr_url: str = ""
    auto_build: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def jobs_dir() -> Path:
    path = kageha_home() / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_path(job_id: str) -> Path:
    return jobs_dir() / f"{job_id}.json"


def save_job(job: JobRecord) -> None:
    job.updated_at = time.time()
    path = _job_path(job.id)
    path.write_text(
        json.dumps(job.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_job(job_id: str) -> JobRecord | None:
    path = _job_path(job_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    jid = str(data.get("id") or job_id)
    session_id = str(data.get("session_id") or data.get("run_id") or jid)
    return JobRecord(
        id=jid,
        objective=str(data.get("objective") or ""),
        project_root=str(data.get("project_root") or ""),
        status=str(data.get("status") or "queued"),
        created_at=float(data.get("created_at") or time.time()),
        updated_at=float(data.get("updated_at") or time.time()),
        run_id=str(data.get("run_id") or session_id or ""),
        session_id=session_id,
        turn_id=str(data.get("turn_id") or ""),
        thread_id=str(data.get("thread_id") or f"job-{jid}"),
        pid=int(data.get("pid") or 0),
        cancel_requested=bool(data.get("cancel_requested")),
        message=str(data.get("message") or ""),
        artifacts=list(data.get("artifacts") or []),
        error=str(data.get("error") or ""),
        agent_mode=str(data.get("agent_mode") or "plan"),
        loop_mode=str(data.get("loop_mode") or "full"),
        max_steps=int(data.get("max_steps") or 40),
        notify_channel=str(data.get("notify_channel") or ""),
        pr_url=str(data.get("pr_url") or ""),
        auto_build=bool(data.get("auto_build")),
    )


def _status_bucket(status: str) -> str:
    st = str(status or "").strip().lower()
    if st in _ACTIVE:
        return st
    if st in _PAUSED:
        return "paused"
    if st in _DONE:
        return "done"
    return st or "unknown"


def parse_status_filter(raw: str | None) -> set[str] | None:
    """Parse ``status`` query into concrete job statuses.

    Accepts exact statuses and aliases: ``active`` (queued+running), ``done``.
    Comma-separated values allowed. Empty → no filter.
    """
    text = str(raw or "").strip().lower()
    if not text:
        return None
    wanted: set[str] = set()
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        if token == "active":
            wanted |= set(_ACTIVE)
        elif token == "paused":
            wanted |= set(_PAUSED)
        elif token == "done":
            wanted |= set(_DONE)
        elif token in {
            "queued",
            "running",
            "success",
            "error",
            "cancelled",
            "awaiting_plan_approval",
            "awaiting_clarify",
        }:
            wanted.add(token)
        else:
            wanted.add(token)
    return wanted or None


def list_jobs(limit: int = 40, *, status: str | None = None) -> list[JobRecord]:
    filt = parse_status_filter(status)
    rows: list[JobRecord] = []
    for path in sorted(jobs_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        job = load_job(path.stem)
        if not job:
            continue
        if filt is not None and job.status not in filt:
            continue
        rows.append(job)
        if len(rows) >= limit:
            break
    return rows


def job_counts(limit: int = 200) -> dict[str, int]:
    counts = {"queued": 0, "running": 0, "paused": 0, "done": 0, "total": 0}
    for job in list_jobs(limit=limit):
        counts["total"] += 1
        bucket = _status_bucket(job.status)
        if bucket in counts:
            counts[bucket] += 1
    return counts


def job_can_cancel(job: JobRecord) -> bool:
    return job.status in _ACTIVE and not (
        job.status == "cancelled" or job.cancel_requested
    )


def job_attachable(job: JobRecord) -> bool:
    """True when a journal session is likely openable (worker has started)."""
    if not (job.session_id or job.run_id):
        return False
    # turn_id is written as soon as the worker submits; enough for reconnect.
    if job.turn_id:
        return True
    # Running/finished/paused jobs without turn_id still map to a runtime session.
    return job.status in {"running", "success", "error", *_PAUSED}


def job_to_api_dict(job: JobRecord) -> dict[str, Any]:
    data = job.to_dict()
    data["bucket"] = _status_bucket(job.status)
    data["can_cancel"] = job_can_cancel(job)
    data["attachable"] = job_attachable(job)
    return data


def attach_info(job_id: str) -> dict[str, Any]:
    """Return session/turn binding for WebUI SSE reconnect."""
    job = load_job(job_id)
    if job is None:
        raise FileNotFoundError(f"job not found: {job_id}")
    session_id = str(job.session_id or job.run_id or "").strip()
    thread_id = str(job.thread_id or (f"job-{job.id}" if session_id else "")).strip()
    turn_id = str(job.turn_id or "").strip()
    return {
        "job_id": job.id,
        "status": job.status,
        "session_id": session_id,
        "thread_id": thread_id,
        "turn_id": turn_id,
        "attachable": job_attachable(job),
        "objective": job.objective,
        "job": job_to_api_dict(job),
    }


def cancel_job(job_id: str) -> JobRecord:
    """Request cancellation; SIGTERM the worker when a pid is known."""
    job = load_job(job_id)
    if job is None:
        raise FileNotFoundError(f"job not found: {job_id}")
    if job.status in _TERMINAL:
        return job
    job.cancel_requested = True
    if job.status == "queued":
        job.status = "cancelled"
        job.message = job.message or "Cancelled before start"
    elif job.status == "running":
        job.status = "cancelled"
        job.message = job.message or "Cancel requested"
    pid = int(job.pid or 0)
    save_job(job)
    if pid > 0:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
        except OSError:
            pass
    _notify(job)
    return load_job(job_id) or job


def enqueue_job(
    *,
    objective: str,
    project_root: str,
    agent_mode: str = "plan",
    loop_mode: str = "full",
    max_steps: int = 40,
    notify_channel: str = "",
    session_id: str = "",
    auto_build: bool = False,
    start: bool = True,
) -> JobRecord:
    jid = uuid.uuid4().hex[:12]
    sid = str(session_id or "").strip() or jid
    job = JobRecord(
        id=jid,
        objective=objective,
        project_root=str(project_root),
        agent_mode=agent_mode,
        loop_mode=loop_mode,
        max_steps=max_steps,
        notify_channel=notify_channel,
        auto_build=bool(auto_build),
        # Pre-bind durable session so attach survives enqueue → worker gap.
        # --resume reuses an existing plan session for /build.
        session_id=sid,
        run_id=sid,
        thread_id=f"job-{jid}",
    )
    save_job(job)
    if start:
        start_job(job.id)
    return load_job(job.id) or job


async def run_job_inline(job_id: str) -> JobRecord:
    """Execute a job in the current process (used by job_worker)."""
    current = load_job(job_id)
    if current is None:
        raise FileNotFoundError(f"job not found: {job_id}")
    if current.status == "cancelled" or current.cancel_requested:
        current.status = "cancelled"
        save_job(current)
        _notify(current)
        return current

    current.status = "running"
    current.pid = os.getpid()
    save_job(current)
    try:
        from kageha.config import security_profile
        from kageha.runtime import AgentRuntime, SecurityProfile, TurnRequest

        runtime = AgentRuntime()
        result = None
        try:
            handle = runtime.submit(
                TurnRequest(
                    objective=current.objective,
                    session_id=current.session_id or current.id,
                    user_id="local",
                    agent_id=f"job:{current.id}",
                    project_root=current.project_root,
                    auto_approve=True,
                    auto_build=bool(current.auto_build),
                    security_profile=SecurityProfile(security_profile()),
                    max_steps=current.max_steps,
                    live=False,
                    platform="async_job",
                    loop_mode=current.loop_mode,
                    agent_mode=current.agent_mode,
                    system_extra=(
                        "You are running as a durable background job. "
                        "Complete the objective and leave clear artifacts. "
                        "If a PR is appropriate, open one and report the URL."
                    ),
                )
            )
            # Persist attach targets immediately so WebUI can reconnect mid-run.
            current = load_job(job_id) or current
            current.session_id = handle.session_id
            current.turn_id = handle.turn_id
            current.run_id = handle.session_id
            current.thread_id = current.thread_id or f"job-{current.id}"
            current.status = "running"
            current.pid = os.getpid()
            save_job(current)

            if current.cancel_requested:
                handle.cancel()
            result = await handle.result()
        finally:
            runtime.close()

        current = load_job(job_id) or current
        if current.cancel_requested or current.status == "cancelled":
            current.status = "cancelled"
            msg = ""
            if result is not None:
                msg = (result.message or "")[:8000]
                current.run_id = getattr(result, "run_id", "") or current.run_id
                current.session_id = current.session_id or current.run_id
            current.message = current.message or msg or "Cancelled"
            save_job(current)
            _notify(current)
            return load_job(job_id) or current

        if result is None:
            current.status = "error"
            current.error = "job produced no result"
            save_job(current)
            _notify(current)
            return load_job(job_id) or current

        current.status = (
            "success"
            if str(result.status).lower() in {"success", "ok", "completed"}
            else str(result.status or "error")
        )
        current.run_id = result.run_id
        current.session_id = current.session_id or result.run_id
        if getattr(result, "turn_id", ""):
            current.turn_id = str(result.turn_id)
        current.message = (result.message or "")[:8000]
        current.artifacts = list(result.artifacts or [])[:50]
        for token in current.message.split():
            if "github.com" in token and "/pull/" in token:
                current.pr_url = token.strip("()<>[].,")
                break
        save_job(current)
        _notify(current)
    except Exception as exc:  # noqa: BLE001
        current = load_job(job_id) or current
        if current.cancel_requested:
            current.status = "cancelled"
            current.message = current.message or "Cancelled"
        else:
            current.status = "error"
            current.error = str(exc)[:2000]
        save_job(current)
        _notify(current)
    return load_job(job_id) or current


def start_job(job_id: str) -> JobRecord:
    """Spawn a detached worker process so the job survives CLI exit."""
    import subprocess
    import sys

    job = load_job(job_id)
    if job is None:
        raise FileNotFoundError(f"job not found: {job_id}")
    if job.status == "running":
        return job
    if job.status == "cancelled" or job.cancel_requested:
        return job

    job.status = "queued"
    save_job(job)
    log_path = jobs_dir() / f"{job_id}.log"
    log_fh = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
    try:
        proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "kageha.project.job_worker", job_id],
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log_fh.close()
    job = load_job(job_id) or job
    job.pid = int(proc.pid or 0)
    save_job(job)
    return load_job(job_id) or job


def _notify(job: JobRecord) -> None:
    """Best-effort completion marker for pollers / future channel hooks."""
    marker = jobs_dir() / f"{job.id}.done"
    try:
        marker.write_text(
            json.dumps(
                {
                    "id": job.id,
                    "status": job.status,
                    "run_id": job.run_id,
                    "session_id": job.session_id,
                    "turn_id": job.turn_id,
                    "pr_url": job.pr_url,
                    "notify_channel": job.notify_channel,
                    "updated_at": job.updated_at,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
