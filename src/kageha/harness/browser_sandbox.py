"""Dockerized Chromium CDP + optional noVNC observer (OpenClaw-style)."""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import socket
import time
from dataclasses import dataclass


# Prefer baked Kageha image; fall back to browserless for zero-build setups.
DEFAULT_IMAGE = "kageha-browser:local"
FALLBACK_IMAGE = "browserless/chrome:latest"


@dataclass
class DockerBrowserSession:
    container_id: str
    cdp_endpoint: str
    host_port: int
    novnc_url: str = ""
    novnc_port: int = 0
    novnc_password: str = ""


def browser_docker_enabled(mode: str | None = None) -> bool:
    raw = (mode or os.environ.get("KAGEHA_BROWSER_MODE") or "").strip().lower()
    return raw in {"docker", "sandbox", "browser-sandbox", "sandboxed"}


def browser_novnc_enabled() -> bool:
    raw = os.environ.get("KAGEHA_BROWSER_NOVNC", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def browser_docker_image() -> str:
    return (
        os.environ.get("KAGEHA_BROWSER_DOCKER_IMAGE")
        or os.environ.get("KAGEHA_SANDBOX_BROWSER_IMAGE")
        or DEFAULT_IMAGE
    ).strip()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _docker_image_exists(exe: str, image: str) -> bool:
    try:
        import subprocess

        r = subprocess.run(
            [exe, "image", "inspect", image],
            capture_output=True,
            check=False,
        )
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _resolve_image(exe: str) -> tuple[str, bool]:
    """Return (image, is_kageha_baked) — baked images expose CDP:9222 + noVNC:6080."""
    img = browser_docker_image()
    if _docker_image_exists(exe, img):
        return img, img.startswith("kageha-browser") or "kageha-browser" in img
    # Auto-fallback so docker mode still works without a local build.
    if img != FALLBACK_IMAGE and _docker_image_exists(exe, FALLBACK_IMAGE):
        return FALLBACK_IMAGE, False
    return img, img.startswith("kageha-browser") or "kageha-browser" in img


async def start_docker_browser(*, image: str | None = None) -> DockerBrowserSession:
    """Start a Chromium container with CDP on localhost (+ noVNC when baked image)."""
    exe = shutil.which("docker")
    if not exe:
        raise RuntimeError("docker not found — cannot start browser sandbox")
    if image:
        img = image
        baked = "kageha-browser" in img
    else:
        img, baked = _resolve_image(exe)

    cdp_port = _free_port()
    novnc_port = _free_port() if (baked and browser_novnc_enabled()) else 0
    novnc_password = (
        os.environ.get("KAGEHA_BROWSER_NOVNC_PASSWORD") or ""
    ).strip() or secrets.token_urlsafe(8)
    name = f"kageha-browser-{cdp_port}"

    if baked:
        # Our image: CDP 9222, noVNC 6080
        cmd = [
            exe,
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--pids-limit=256",
            "--shm-size=1g",
            "--security-opt=no-new-privileges",
            "-p",
            f"127.0.0.1:{cdp_port}:9222",
            "-e",
            f"NOVNC_PASSWORD={novnc_password}",
        ]
        if novnc_port:
            cmd.extend(["-p", f"127.0.0.1:{novnc_port}:6080"])
        cmd.append(img)
    else:
        # browserless/chrome: CDP on 3000, no noVNC
        cmd = [
            exe,
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--pids-limit=256",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "-p",
            f"127.0.0.1:{cdp_port}:3000",
            "-e",
            "CONNECTION_TIMEOUT=600000",
            img,
        ]
        novnc_password = ""
        novnc_port = 0

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out_b, err_b = await proc.communicate()
    if proc.returncode != 0:
        hint = ""
        if baked and not _docker_image_exists(exe, img):
            hint = (
                "\nBuild the baked image: "
                "docker build -t kageha-browser:local docker/browser"
            )
        raise RuntimeError(
            f"docker run failed ({proc.returncode}): "
            f"{(err_b or out_b).decode(errors='replace')[:500]}{hint}"
        )
    cid = out_b.decode().strip() or name
    endpoint = f"http://127.0.0.1:{cdp_port}"
    await _wait_cdp(endpoint, timeout_s=60.0)
    novnc_url = ""
    if novnc_port:
        # noVNC UI; password is the x11vnc password (prompted by client)
        novnc_url = f"http://127.0.0.1:{novnc_port}/vnc.html?autoconnect=1&resize=remote"
    return DockerBrowserSession(
        container_id=cid,
        cdp_endpoint=endpoint,
        host_port=cdp_port,
        novnc_url=novnc_url,
        novnc_port=novnc_port,
        novnc_password=novnc_password,
    )


async def _wait_cdp(endpoint: str, *, timeout_s: float) -> None:
    import httpx

    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                for path in ("/json/version", "/", "/pressure"):
                    resp = await client.get(endpoint.rstrip("/") + path)
                    if resp.status_code < 500:
                        return
                    last = f"{path}:{resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        await asyncio.sleep(0.5)
    raise RuntimeError(f"browser sandbox CDP not ready at {endpoint}: {last}")


async def stop_docker_browser(session: DockerBrowserSession | None) -> None:
    if session is None:
        return
    exe = shutil.which("docker")
    if not exe:
        return
    proc = await asyncio.create_subprocess_exec(
        exe,
        "rm",
        "-f",
        session.container_id,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


def browser_sandbox_status() -> dict[str, str | bool]:
    docker_ok = bool(shutil.which("docker"))
    return {
        "mode_env": os.environ.get("KAGEHA_BROWSER_MODE") or "headless",
        "docker_backend": browser_docker_enabled(),
        "docker_available": docker_ok,
        "image": browser_docker_image(),
        "novnc": browser_novnc_enabled(),
    }
