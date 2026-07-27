"""Detachable worker process for durable cloud jobs.

Spawned with ``python -m kageha.project.job_worker <job_id>`` so the job
survives CLI exit (unlike a daemon thread).
"""

from __future__ import annotations

import asyncio
import sys


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print("usage: python -m kageha.project.job_worker <job_id>", file=sys.stderr)
        return 2
    job_id = args[0].strip()
    from kageha.project.async_jobs import load_job, run_job_inline

    job = load_job(job_id)
    if job is None:
        print(f"job not found: {job_id}", file=sys.stderr)
        return 1
    if job.status == "cancelled" or job.cancel_requested:
        return 0
    asyncio.run(run_job_inline(job_id))
    final = load_job(job_id)
    if final is None:
        return 1
    if final.status in {"success", "cancelled"}:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
