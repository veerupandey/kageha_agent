"""Optional container sandbox for forged tools (best-effort docker)."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from kageha.harness.sandbox import ShellResult


async def run_in_docker(
    command: str,
    cwd: Path,
    *,
    image: str = "python:3.12-slim",
    network: str = "none",
    timeout: float = 120.0,
) -> ShellResult:
    if not shutil.which("docker"):
        return ShellResult(
            command=command,
            exit_code=127,
            stdout="",
            stderr="docker not available; falling back is caller's responsibility",
        )
    # Mount only the session workspace read-write; no host network by default
    docker_cmd = (
        f"docker run --rm --network={network} "
        f"-v {cwd}:/work -w /work {image} "
        f"bash -lc {command!r}"
    )
    proc = await asyncio.create_subprocess_shell(
        docker_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return ShellResult(command=command, exit_code=124, stdout="", stderr="docker timeout")
    return ShellResult(
        command=command,
        exit_code=proc.returncode or 0,
        stdout=out_b.decode(errors="replace")[:8000],
        stderr=err_b.decode(errors="replace")[:8000],
    )
