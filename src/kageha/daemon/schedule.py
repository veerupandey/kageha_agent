"""Lightweight schedule store + curator/consolidate cron ticks (no external scheduler dep)."""

from __future__ import annotations

import json
import os
import platform
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kageha.config import kageha_home


SCHEDULE_NAME = "schedule.json"


@dataclass
class ScheduleJob:
    id: str
    kind: str  # curator | consolidate
    interval_seconds: int
    enabled: bool = True
    last_run: float = 0.0
    next_run: float = 0.0
    opts: dict[str, Any] | None = None


def schedule_path() -> Path:
    return kageha_home() / SCHEDULE_NAME


def load_schedule() -> dict[str, Any]:
    path = schedule_path()
    if not path.is_file():
        return {"jobs": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"jobs": []}
    if not isinstance(data, dict):
        return {"jobs": []}
    data.setdefault("jobs", [])
    return data


def save_schedule(data: dict[str, Any]) -> Path:
    path = schedule_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def default_curator_interval() -> int:
    raw = (os.environ.get("KAGEHA_CURATOR_CRON") or "").strip()
    # Accept seconds, or "7d" / "24h" / "30m"
    if not raw:
        return 7 * 24 * 3600
    if raw.isdigit():
        return max(60, int(raw))
    unit = raw[-1].lower()
    try:
        n = float(raw[:-1])
    except ValueError:
        return 7 * 24 * 3600
    if unit == "d":
        return max(60, int(n * 86400))
    if unit == "h":
        return max(60, int(n * 3600))
    if unit == "m":
        return max(60, int(n * 60))
    return 7 * 24 * 3600


def default_consolidate_interval() -> int:
    """Align daemon consolidate tick with memory consolidate cooldown."""
    from kageha.memory.consolidate import consolidate_cooldown_hours

    hours = consolidate_cooldown_hours()
    if hours <= 0:
        return 6 * 3600
    return max(60, int(hours * 3600))


def ensure_curator_job(*, days: int = 30) -> ScheduleJob:
    data = load_schedule()
    jobs = list(data.get("jobs") or [])
    for raw in jobs:
        if isinstance(raw, dict) and raw.get("id") == "curator":
            job = ScheduleJob(
                id="curator",
                kind="curator",
                interval_seconds=int(raw.get("interval_seconds") or default_curator_interval()),
                enabled=bool(raw.get("enabled", True)),
                last_run=float(raw.get("last_run") or 0),
                next_run=float(raw.get("next_run") or 0),
                opts=dict(raw.get("opts") or {"days": days}),
            )
            return job
    now = time.time()
    job = ScheduleJob(
        id="curator",
        kind="curator",
        interval_seconds=default_curator_interval(),
        enabled=True,
        last_run=0.0,
        next_run=now,
        opts={"days": days, "dry_run": False},
    )
    jobs.append(asdict(job))
    data["jobs"] = jobs
    save_schedule(data)
    return job


def ensure_consolidate_job() -> ScheduleJob:
    data = load_schedule()
    jobs = list(data.get("jobs") or [])
    for raw in jobs:
        if isinstance(raw, dict) and raw.get("id") == "consolidate":
            return ScheduleJob(
                id="consolidate",
                kind="consolidate",
                interval_seconds=int(
                    raw.get("interval_seconds") or default_consolidate_interval()
                ),
                enabled=bool(raw.get("enabled", True)),
                last_run=float(raw.get("last_run") or 0),
                next_run=float(raw.get("next_run") or 0),
                opts=dict(raw.get("opts") or {"force": False}),
            )
    now = time.time()
    job = ScheduleJob(
        id="consolidate",
        kind="consolidate",
        interval_seconds=default_consolidate_interval(),
        enabled=True,
        last_run=0.0,
        next_run=now,
        opts={"force": False},
    )
    jobs.append(asdict(job))
    data["jobs"] = jobs
    save_schedule(data)
    return job


def upsert_job(job: ScheduleJob) -> None:
    data = load_schedule()
    jobs = [j for j in (data.get("jobs") or []) if not (isinstance(j, dict) and j.get("id") == job.id)]
    jobs.append(asdict(job))
    data["jobs"] = jobs
    save_schedule(data)


def due_jobs(now: float | None = None) -> list[ScheduleJob]:
    now = now if now is not None else time.time()
    out: list[ScheduleJob] = []
    for raw in load_schedule().get("jobs") or []:
        if not isinstance(raw, dict) or not raw.get("enabled", True):
            continue
        job = ScheduleJob(
            id=str(raw.get("id") or ""),
            kind=str(raw.get("kind") or ""),
            interval_seconds=int(raw.get("interval_seconds") or 0),
            enabled=True,
            last_run=float(raw.get("last_run") or 0),
            next_run=float(raw.get("next_run") or 0),
            opts=dict(raw.get("opts") or {}),
        )
        if job.id and job.next_run <= now:
            out.append(job)
    return out


def run_tick(*, force: bool = False) -> list[str]:
    """Execute due jobs. Returns human-readable action lines."""
    from kageha.memory.curator import run_curator
    from kageha.memory.service import get_memory_service

    lines: list[str] = []
    now = time.time()
    if force:
        jobs = [ensure_curator_job(), ensure_consolidate_job()]
    else:
        jobs = due_jobs(now)
    if not jobs:
        lines.append("(no due jobs)")
        return lines
    for job in jobs:
        opts = job.opts or {}
        if job.kind == "curator":
            days = int(opts.get("days") or 30)
            dry = bool(opts.get("dry_run"))
            lines.append(f"[{datetime.now(timezone.utc).isoformat()}] curator run days={days}")
            for line in run_curator(days=days, dry_run=dry):
                lines.append(f"  {line}")
        elif job.kind == "consolidate":
            force_run = bool(opts.get("force")) or force
            lines.append(
                f"[{datetime.now(timezone.utc).isoformat()}] consolidate force={force_run}"
            )
            report = get_memory_service().consolidate(force=force_run)
            lines.append(
                "  "
                f"superseded={report.get('superseded_duplicates', 0)} "
                f"quarantine_expired={report.get('expired_quarantine', 0)} "
                f"digest={report.get('digest_path') or '-'} "
                f"skipped={report.get('skipped_cooldown', False)}"
            )
        else:
            lines.append(f"SKIP unknown job kind {job.kind}")
            continue
        job.last_run = now
        job.next_run = now + max(60, job.interval_seconds)
        upsert_job(job)
    return lines


def render_crontab_line() -> str:
    """Hourly tick; due logic lives in schedule.json."""
    return "0 * * * * cd \"$HOME\" && kageha daemon tick >>\"$HOME/.kageha/daemon.log\" 2>&1"


def render_launchd_plist() -> str:
    home = str(kageha_home())
    # Prefer uv-run if available path unknown — use kageha from PATH.
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.kageha.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>kageha daemon tick</string>
  </array>
  <key>StartInterval</key>
  <integer>3600</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{home}/daemon.log</string>
  <key>StandardErrorPath</key>
  <string>{home}/daemon.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
"""


def install_scheduler() -> str:
    """Install OS scheduler hook (launchd on macOS, crontab elsewhere)."""
    ensure_curator_job()
    ensure_consolidate_job()
    if platform.system() == "Darwin":
        plist = Path.home() / "Library/LaunchAgents/ai.kageha.daemon.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(render_launchd_plist(), encoding="utf-8")
        os.system(f"launchctl unload {plist} 2>/dev/null; launchctl load {plist}")
        return f"Installed launchd agent → {plist}"
    # Print crontab line for user to install (avoid rewriting whole crontab blindly).
    line = render_crontab_line()
    hint = kageha_home() / "crontab.snippet"
    hint.write_text(line + "\n", encoding="utf-8")
    return (
        f"Wrote crontab snippet → {hint}\n"
        f"Install with: (crontab -l 2>/dev/null; cat {hint}) | crontab -"
    )


def uninstall_scheduler() -> str:
    if platform.system() == "Darwin":
        plist = Path.home() / "Library/LaunchAgents/ai.kageha.daemon.plist"
        if plist.is_file():
            os.system(f"launchctl unload {plist} 2>/dev/null")
            plist.unlink(missing_ok=True)
            return f"Removed launchd agent {plist}"
        return "No launchd agent installed"
    hint = kageha_home() / "crontab.snippet"
    if hint.is_file():
        hint.unlink()
    return (
        "Removed crontab snippet file. "
        "Edit `crontab -e` manually to drop the kageha daemon tick line."
    )


def status_text() -> str:
    data = load_schedule()
    jobs = data.get("jobs") or []
    if not jobs:
        return "(no scheduled jobs — run `kageha daemon install`)"
    lines = []
    for raw in jobs:
        if not isinstance(raw, dict):
            continue
        nxt = float(raw.get("next_run") or 0)
        last = float(raw.get("last_run") or 0)
        lines.append(
            f"{raw.get('id')}: enabled={raw.get('enabled', True)} "
            f"interval={raw.get('interval_seconds')}s "
            f"last={_fmt(last)} next={_fmt(nxt)}"
        )
    return "\n".join(lines) if lines else "(empty)"


def _fmt(ts: float) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
