"""Adapter for the cua-driver macOS sidecar (Unix socket preferred, CLI fallback).

Kageha owns HITL / allowlists / tool names; cua-driver owns AX + background input.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import platform
import re
import shutil
import socket
import time
from pathlib import Path
from typing import Any


class ComputerDriverError(RuntimeError):
    """Raised when the sidecar is missing, misconfigured, or returns an error."""


# Process-local timing (OSWorld-Human-style: measure driver vs LLM).
_TIMING: dict[str, Any] = {
    "calls": 0,
    "driver_ms_total": 0.0,
    "socket_calls": 0,
    "cli_calls": 0,
    "last_ms": 0.0,
    "last_transport": "",
    "last_tool": "",
}


def reset_timing() -> None:
    _TIMING.update(
        {
            "calls": 0,
            "driver_ms_total": 0.0,
            "socket_calls": 0,
            "cli_calls": 0,
            "last_ms": 0.0,
            "last_transport": "",
            "last_tool": "",
        }
    )


def timing_snapshot() -> dict[str, Any]:
    return dict(_TIMING)


def driver_bin() -> str | None:
    """Resolve cua-driver executable path."""
    for key in ("KAGEHA_CUA_DRIVER", "CUA_DRIVER_BIN"):
        raw = (os.environ.get(key) or "").strip()
        if raw and Path(raw).is_file() and os.access(raw, os.X_OK):
            return raw
    which = shutil.which("cua-driver")
    if which:
        return which
    home = Path.home() / ".local" / "bin" / "cua-driver"
    if home.is_file() and os.access(home, os.X_OK):
        return str(home)
    app = Path("/Applications/CuaDriver.app/Contents/MacOS/cua-driver")
    if app.is_file() and os.access(app, os.X_OK):
        return str(app)
    return None


def socket_path() -> Path | None:
    """Resolve cua-driver daemon Unix socket path."""
    for key in ("KAGEHA_CUA_SOCKET", "CUA_DRIVER_SOCKET"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            p = Path(raw)
            if p.exists():
                return p
    default = Path.home() / "Library/Caches/cua-driver/cua-driver.sock"
    if default.exists():
        return default
    return None


def transport_mode() -> str:
    """``auto`` (default) | ``socket`` | ``cli``."""
    raw = (os.environ.get("KAGEHA_CUA_TRANSPORT") or "auto").strip().lower()
    return raw if raw in {"auto", "socket", "cli"} else "auto"


def require_macos() -> str | None:
    if platform.system() != "Darwin":
        return "ERROR: computer-use v1 is macOS-only"
    return None


def driver_available() -> bool:
    return require_macos() is None and driver_bin() is not None


def _record_timing(*, tool: str, ms: float, transport: str) -> None:
    _TIMING["calls"] = int(_TIMING["calls"]) + 1
    _TIMING["driver_ms_total"] = float(_TIMING["driver_ms_total"]) + ms
    _TIMING["last_ms"] = ms
    _TIMING["last_transport"] = transport
    _TIMING["last_tool"] = tool
    if transport == "socket":
        _TIMING["socket_calls"] = int(_TIMING["socket_calls"]) + 1
    else:
        _TIMING["cli_calls"] = int(_TIMING["cli_calls"]) + 1


def _unwrap_socket_payload(data: dict[str, Any], *, tool: str) -> dict[str, Any]:
    """Normalize daemon socket response → CLI-shaped dict."""
    if data.get("ok") is False or data.get("error"):
        raise ComputerDriverError(str(data.get("error") or f"{tool} failed"))
    result = data.get("result")
    if isinstance(result, dict):
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        # Some tools only return text content; keep a thin envelope.
        content = result.get("content")
        if isinstance(content, list):
            texts = [
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict)
            ]
            joined = "\n".join(t for t in texts if t).strip()
            if joined:
                return {"ok": True, "text": joined[:4000]}
        return result
    if result is None:
        return {}
    return {"result": result}


async def _call_socket(
    tool: str,
    args: dict[str, Any] | None,
    *,
    timeout: float,
) -> dict[str, Any]:
    path = socket_path()
    if path is None:
        raise ComputerDriverError("cua-driver socket not found")

    def _sync() -> dict[str, Any]:
        payload = {
            "method": "call",
            "name": tool,
            "args": args or {},
            "client_kind": "python_sdk",
        }
        raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(max(0.5, float(timeout)))
        try:
            sock.connect(str(path))
            sock.sendall(raw)
            buf = bytearray()
            while True:
                chunk = sock.recv(1 << 20)
                if not chunk:
                    break
                buf.extend(chunk)
                if b"\n" in chunk:
                    break
        finally:
            with contextlib.suppress(OSError):
                sock.close()
        text = bytes(buf).decode("utf-8", errors="replace").strip()
        if not text:
            return {}
        try:
            data = json.loads(text.splitlines()[0])
        except json.JSONDecodeError as exc:
            raise ComputerDriverError(
                f"cua-driver socket non-JSON for {tool}: {text[:400]}"
            ) from exc
        if not isinstance(data, dict):
            return {"result": data}
        return _unwrap_socket_payload(data, tool=tool)

    return await asyncio.to_thread(_sync)


async def ensure_daemon(*, timeout: float = 8.0) -> None:
    """Best-effort: start CuaDriver serve if status is not running."""
    err = require_macos()
    if err:
        raise ComputerDriverError(err)
    # Fast path: socket already live.
    if socket_path() is not None:
        return
    binary = driver_bin()
    if not binary:
        raise ComputerDriverError(
            "cua-driver not found. Run: scripts/install_computer_driver.sh "
            "and grant Accessibility + Screen Recording to CuaDriver.app"
        )
    try:
        status = await _run_raw(binary, ["status"], timeout=min(4.0, timeout))
        if "running" in status.lower() and "not running" not in status.lower():
            return
    except ComputerDriverError:
        pass
    # Launch via LaunchServices so TCC attributes to CuaDriver.app
    app = Path("/Applications/CuaDriver.app")
    if app.is_dir():
        proc = await asyncio.create_subprocess_exec(
            "open",
            "-n",
            "-g",
            "-a",
            "CuaDriver",
            "--args",
            "serve",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    else:
        await _run_raw(binary, ["serve"], timeout=1.0, check=False)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if socket_path() is not None:
            return
        try:
            status = await _run_raw(binary, ["status"], timeout=2.0)
            if "running" in status.lower() and "not running" not in status.lower():
                return
        except ComputerDriverError:
            pass
        await asyncio.sleep(0.25)
    raise ComputerDriverError(
        "cua-driver daemon did not start. Try: open -n -g -a CuaDriver --args serve"
    )


async def call(
    tool: str,
    args: dict[str, Any] | None = None,
    *,
    timeout: float = 60.0,
    ensure: bool = True,
) -> dict[str, Any]:
    """Invoke cua-driver via Unix socket (preferred) or CLI fallback."""
    err = require_macos()
    if err:
        raise ComputerDriverError(err)
    mode = transport_mode()
    last_exc: Exception | None = None
    for attempt in range(2):
        if ensure or attempt > 0:
            try:
                await ensure_daemon()
            except ComputerDriverError as exc:
                last_exc = exc
                continue
        t0 = time.perf_counter()
        try:
            if mode != "cli" and socket_path() is not None:
                data = await _call_socket(tool, args, timeout=timeout)
                _record_timing(
                    tool=tool,
                    ms=(time.perf_counter() - t0) * 1000.0,
                    transport="socket",
                )
                return data
            if mode == "socket":
                raise ComputerDriverError("socket transport required but unavailable")
            binary = driver_bin()
            if not binary:
                raise ComputerDriverError(
                    "cua-driver not found. Run: scripts/install_computer_driver.sh"
                )
            payload = json.dumps(args or {}, separators=(",", ":"))
            stdout = await _run_raw(
                binary, ["call", tool, payload], timeout=timeout
            )
        except ComputerDriverError as exc:
            last_exc = exc
            msg = str(exc).lower()
            if attempt == 0 and (
                "closed connection" in msg
                or "not running" in msg
                or "socket" in msg
            ):
                await asyncio.sleep(0.4)
                continue
            raise
        text = stdout.strip()
        ms = (time.perf_counter() - t0) * 1000.0
        _record_timing(tool=tool, ms=ms, transport="cli")
        if not text:
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ComputerDriverError(
                f"cua-driver returned non-JSON for {tool}: {text[:400]}"
            ) from exc
        if isinstance(data, dict) and data.get("error"):
            raise ComputerDriverError(str(data.get("error")))
        if not isinstance(data, dict):
            return {"result": data}
        return data
    raise ComputerDriverError(str(last_exc) if last_exc else f"{tool} failed")


async def permissions_status() -> dict[str, Any]:
    binary = driver_bin()
    if not binary:
        return {"accessibility": False, "screen_recording": False, "error": "missing"}
    try:
        await ensure_daemon(timeout=5.0)
        stdout = await _run_raw(
            binary, ["permissions", "status", "--json"], timeout=8.0
        )
        return json.loads(stdout)
    except Exception as exc:  # noqa: BLE001
        return {"accessibility": False, "screen_recording": False, "error": str(exc)}


async def _run_raw(
    binary: str,
    argv: list[str],
    *,
    timeout: float,
    check: bool = True,
) -> str:
    proc = await asyncio.create_subprocess_exec(
        binary,
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise ComputerDriverError(
            f"cua-driver timed out after {timeout}s: {' '.join(argv)}"
        ) from exc
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    if check and proc.returncode != 0:
        msg = (stderr or stdout or f"exit {proc.returncode}").strip()
        raise ComputerDriverError(f"cua-driver {' '.join(argv)} failed: {msg[:500]}")
    return stdout


def ref_to_index(ref: str) -> int | None:
    """Parse browser-style ``e12`` (or bare ``12``) into element_index."""
    raw = (ref or "").strip().lower()
    if not raw:
        return None
    if raw.startswith("e") and raw[1:].isdigit():
        return int(raw[1:])
    if raw.isdigit():
        return int(raw)
    return None


_ACTIONABLE_ROLE_HINTS = (
    "button",
    "textfield",
    "textarea",
    "checkbox",
    "radio",
    "popup",
    "menuitem",
    "link",
    "slider",
    "tab",
    "combo",
    "incrementor",
    "statictext",
)


def _role_rank(role: str) -> int:
    r = role.lower()
    for i, hint in enumerate(_ACTIONABLE_ROLE_HINTS):
        if hint in r:
            return i
    return 100


_STATIC_TEXT_RE = re.compile(
    r'AXStaticText\s*=\s*"([^"]*)"',
    re.I,
)


def _clean_ax_text(raw: str) -> str:
    # Strip bidi / directionality marks Calculator injects into display strings.
    drop = {
        "\u200e",  # LRM
        "\u200f",  # RLM
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\ufeff",
    }
    return "".join(
        ch for ch in raw if ch not in drop and (ord(ch) >= 32 or ch in "\n\t")
    ).strip()


def readings_from_tree_markdown(tree_markdown: str) -> list[dict[str, Any]]:
    """Pull display strings from markdown AX trees (often unindexed)."""
    out: list[dict[str, Any]] = []
    for i, match in enumerate(_STATIC_TEXT_RE.finditer(tree_markdown or "")):
        val = _clean_ax_text(match.group(1))
        if not val:
            continue
        out.append(
            {
                "ref": f"display:{i}",
                "role": "AXStaticText",
                "label": "",
                "value": val[:120],
            }
        )
    return out[:24]


def elements_to_snapshot(
    elements: list[dict[str, Any]],
    *,
    limit: int = 120,
    tree_markdown: str = "",
    compact: bool = True,
) -> tuple[str, dict[str, int], list[dict[str, Any]]]:
    """Map driver elements → ``eN`` lines, ref→index map, and reading hints.

    Returns ``(snapshot_text, ref_map, readings)`` where readings are prominent
    AX values (display text) so agents can verify UI without vision.
    ``compact=True`` omits pixel frames (much smaller prompts).
    """
    ranked: list[dict[str, Any]] = []
    for i, el in enumerate(elements):
        if not isinstance(el, dict):
            continue
        idx = el.get("element_index")
        if idx is None:
            idx = i
        try:
            idx_i = int(idx)
        except (TypeError, ValueError):
            continue
        role = str(el.get("role") or el.get("role_description") or "element")
        ranked.append({**el, "element_index": idx_i, "_role": role})
    ranked.sort(
        key=lambda el: (
            _role_rank(str(el.get("_role") or "")),
            int(el.get("element_index") or 0),
        )
    )

    lines: list[str] = []
    ref_map: dict[str, int] = {}
    readings: list[dict[str, Any]] = []
    for el in ranked[: max(1, limit)]:
        idx_i = int(el["element_index"])
        ref = f"e{idx_i}"
        ref_map[ref] = idx_i
        role = str(el.get("_role") or "element")
        label = str(el.get("label") or el.get("title") or el.get("name") or "")
        value = el.get("value")
        bits = [ref, role]
        if label:
            bits.append(repr(label)[:80])
        if value not in (None, ""):
            bits.append(f"value={str(value)[:60]!r}")
        if el.get("settable"):
            bits.append("settable")
        if el.get("focused"):
            bits.append("focused")
        if not compact:
            frame = el.get("frame") or el.get("bounds") or {}
            if isinstance(frame, dict) and frame.get("w") is not None:
                bits.append(
                    f"@{int(frame.get('x', 0))},{int(frame.get('y', 0))}"
                    f" {int(frame.get('w', 0))}x{int(frame.get('h', 0))}"
                )
        lines.append(" ".join(bits))
        role_l = role.lower()
        if value not in (None, "") or (
            label and ("static" in role_l or "text" in role_l)
        ):
            readings.append(
                {
                    "ref": ref,
                    "role": role,
                    "label": label[:80] if label else "",
                    "value": "" if value in (None, "") else str(value)[:120],
                }
            )
    # Calculator and many AppKit apps expose the display only in tree_markdown.
    if not readings:
        readings = readings_from_tree_markdown(tree_markdown)
    else:
        # Prefer markdown display strings first when present.
        md_reads = readings_from_tree_markdown(tree_markdown)
        if md_reads:
            readings = md_reads + readings
    # Dedupe by value
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in readings:
        key = str(row.get("value") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return (
        ("\n".join(lines) if lines else "(no interactive elements)"),
        ref_map,
        deduped[:24],
    )
