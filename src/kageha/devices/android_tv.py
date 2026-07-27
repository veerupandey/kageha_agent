"""Android / Google TV control via ``adb`` (skill-owned library).

Requires network ADB on the TV and ``adb`` on PATH. Configure:

  KAGEHA_ANDROID_TV_HOST=192.168.1.50
  KAGEHA_ANDROID_TV_PORT=5555   # optional
  KAGEHA_ANDROID_TV_SERIAL=     # optional; overrides host:port

Use via skill_run android_tv scripts/*.py — not harness tools.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

# Friendly remote names → Android keyevent codes
_KEY_MAP: dict[str, int] = {
    "home": 3,
    "back": 4,
    "up": 19,
    "down": 20,
    "left": 21,
    "right": 22,
    "center": 23,
    "ok": 23,
    "enter": 66,
    "volume_up": 24,
    "vol_up": 24,
    "volume_down": 25,
    "vol_down": 25,
    "mute": 164,
    "power": 26,
    "play": 126,
    "pause": 127,
    "play_pause": 85,
    "next": 87,
    "prev": 88,
    "previous": 88,
    "menu": 82,
    "settings": 176,
}

# Common streaming package hints (fuzzy match against installed packages).
_APP_HINTS: dict[str, tuple[str, ...]] = {
    "sonyliv": ("sonyliv", "sony.liv", "sony_liv"),
    "netflix": ("netflix",),
    "youtube": ("youtube", "youtube.tv"),
    "prime": ("amazon.avod", "primevideo", "amazonvideo"),
    "disney": ("disney", "hotstar"),
    "hotstar": ("hotstar", "disney"),
}


def _config() -> dict[str, str]:
    host = (os.environ.get("KAGEHA_ANDROID_TV_HOST") or "").strip()
    port = (os.environ.get("KAGEHA_ANDROID_TV_PORT") or "5555").strip() or "5555"
    serial = (os.environ.get("KAGEHA_ANDROID_TV_SERIAL") or "").strip()
    if not serial and host:
        serial = f"{host}:{port}"
    return {"host": host, "port": port, "serial": serial}


async def _run_adb(*args: str, timeout: float = 20.0) -> tuple[int, str, str]:
    exe = shutil.which("adb")
    if not exe:
        return 127, "", "adb not found on PATH (install Android platform-tools)"
    proc = await asyncio.create_subprocess_exec(
        exe,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", f"adb timed out after {timeout}s"
    return (
        int(proc.returncode or 0),
        out_b.decode(errors="replace"),
        err_b.decode(errors="replace"),
    )


async def _ensure_connected(serial: str) -> str | None:
    """Connect if serial looks like host:port. Return error string or None."""
    if not serial:
        return (
            "ERROR: set KAGEHA_ANDROID_TV_HOST (or KAGEHA_ANDROID_TV_SERIAL). "
            "On the TV: Settings → Device Preferences → About → Build (tap 7×) → "
            "Developer options → Network debugging / ADB over network."
        )
    if ":" in serial and not serial.startswith("emulator-"):
        code, out, err = await _run_adb("connect", serial, timeout=15.0)
        text = (out + err).strip()
        low = text.lower()
        if code != 0 and "connected" not in low and "already connected" not in low:
            return f"ERROR: adb connect {serial} failed: {text[:400]}"
    code, out, err = await _run_adb("devices")
    if code != 0:
        return f"ERROR: adb devices failed: {(out + err)[:300]}"
    lines = [ln for ln in out.splitlines() if "\tdevice" in ln]
    if serial and not any(ln.startswith(serial) for ln in lines):
        # Accept any device if only one is connected.
        if len(lines) == 1:
            return None
        return (
            f"ERROR: device {serial!r} not in `adb devices`.\n"
            f"{out.strip()[:500]}\n"
            "Accept the RSA prompt on the TV if shown."
        )
    if not lines:
        return "ERROR: no adb device connected. Enable network debugging and retry."
    return None


def _adb_prefix(serial: str) -> list[str]:
    if serial:
        return ["-s", serial]
    return []


def _parse_packages(raw: str) -> list[str]:
    pkgs: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            pkgs.append(line.split("package:", 1)[1].strip())
        elif line and "." in line and " " not in line:
            pkgs.append(line)
    return pkgs


def _match_app(query: str, packages: list[str]) -> list[str]:
    q = (query or "").strip().lower()
    if not q:
        return packages[:40]
    hints = _APP_HINTS.get(q, ())
    needles = (q, *hints)
    scored: list[tuple[int, str]] = []
    for pkg in packages:
        low = pkg.lower()
        score = 0
        for n in needles:
            if n and n in low:
                score = max(score, 10 if low.endswith(n) or f".{n}" in low else 5)
        if score:
            scored.append((score, pkg))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [p for _, p in scored]


def _local_ipv4() -> str | None:
    """Best-effort primary LAN IPv4 (same Wi‑Fi subnet)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    for iface_hint in ("en0", "en1", "eth0", "wlan0"):
        # macOS: ipconfig; ignore failures
        try:
            import subprocess

            out = subprocess.check_output(
                ["ipconfig", "getifaddr", iface_hint],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            ).strip()
            if out:
                return out
        except Exception:  # noqa: BLE001
            continue
    return None


def _subnet_hosts(local_ip: str) -> list[str]:
    parts = local_ip.split(".")
    if len(parts) != 4:
        return []
    base = ".".join(parts[:3])
    me = parts[3]
    return [f"{base}.{i}" for i in range(1, 255) if str(i) != me]


def _port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _probe_bravia(host: str, timeout: float = 0.8) -> dict[str, Any] | None:
    """Return Bravia identity if /sony/system answers JSON-RPC."""
    url = f"http://{host}/sony/system"
    headers = {"Content-Type": "application/json"}
    methods = (
        ("getSystemInformation", "1.0"),
        ("getPowerStatus", "1.0"),
    )
    try:
        with httpx.Client(timeout=timeout) as client:
            data: dict[str, Any] | None = None
            for method, ver in methods:
                r = client.post(
                    url,
                    json={
                        "method": method,
                        "id": 1,
                        "params": [],
                        "version": ver,
                    },
                    headers=headers,
                )
                if r.status_code >= 400:
                    continue
                parsed = r.json()
                if isinstance(parsed, dict) and (
                    "result" in parsed or "error" not in parsed
                ):
                    data = parsed
                    break
            if data is None:
                return None
    except Exception:  # noqa: BLE001
        return None
    out: dict[str, Any] = {"host": host, "kind": "bravia_http"}
    result = data.get("result")
    if isinstance(result, list) and result and isinstance(result[0], dict):
        first = result[0]
        if "model" in first or "product" in first:
            out["model"] = str(first.get("model") or "")
            out["product"] = str(first.get("product") or "")
            out["name"] = str(first.get("name") or "")
        if "status" in first:
            out["power"] = first.get("status")
    return out


def discover_tv_candidates(
    *,
    adb_port: int = 5555,
    scan_bravia: bool = True,
    max_workers: int = 64,
) -> dict[str, Any]:
    """Scan the local /24 Wi‑Fi subnet for ADB (5555) and Sony Bravia HTTP."""
    local = _local_ipv4()
    report: dict[str, Any] = {
        "local_ip": local or "",
        "adb_port": adb_port,
        "adb_hosts": [],
        "bravia_hosts": [],
        "hint": "",
    }
    if not local:
        report["hint"] = "Could not determine local Wi‑Fi IP."
        return report
    hosts = _subnet_hosts(local)
    adb_hits: list[str] = []
    http_hits: list[str] = []

    def check_adb(h: str) -> str | None:
        return h if _port_open(h, adb_port) else None

    def check_http(h: str) -> str | None:
        return h if _port_open(h, 80) else None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for h in pool.map(check_adb, hosts):
            if h:
                adb_hits.append(h)
        if scan_bravia:
            for h in pool.map(check_http, hosts):
                if h:
                    http_hits.append(h)

    report["adb_hosts"] = sorted(adb_hits)
    bravia: list[dict[str, Any]] = []
    for h in http_hits:
        info = _probe_bravia(h)
        if info:
            bravia.append(info)
    report["bravia_hosts"] = bravia

    if adb_hits:
        report["hint"] = (
            f"Found ADB on {', '.join(adb_hits)}. "
            f"Set KAGEHA_ANDROID_TV_HOST={adb_hits[0]} (accept RSA prompt on TV)."
        )
    elif bravia:
        hosts_b = ", ".join(b["host"] for b in bravia)
        report["hint"] = (
            f"Found Sony Bravia HTTP API on {hosts_b}, but ADB port {adb_port} is closed. "
            "Enable Developer options → Network debugging, then skill android_tv; "
            "or use skill sony_bravia for IP control."
        )
    else:
        report["hint"] = (
            "No ADB (port 5555) or Bravia API found on this Wi‑Fi subnet. "
            "Wake the TV, join the same network, enable Network debugging, then retry."
        )
    return report


async def send_key(key: str) -> dict[str, Any]:
    cfg = _config()
    name = (key or "").strip().lower().replace("-", "_").replace(" ", "_")
    code = _KEY_MAP.get(name)
    if code is None and name.isdigit():
        code = int(name)
    if code is None:
        return {
            "ok": False,
            "error": "unknown key. Use: " + ", ".join(sorted(_KEY_MAP)),
        }
    err = await _ensure_connected(cfg["serial"])
    if err:
        return {"ok": False, "error": err}
    c, out, e = await _run_adb(
        *_adb_prefix(cfg["serial"]),
        "shell",
        "input",
        "keyevent",
        str(code),
    )
    if c != 0:
        return {"ok": False, "error": f"keyevent failed: {(out + e)[:400]}"}
    return {"ok": True, "key": name, "keyevent": code}


async def launch_package(package: str = "", name: str = "") -> dict[str, Any]:
    cfg = _config()
    target = (package or "").strip()
    friendly = (name or "").strip().lower()
    err = await _ensure_connected(cfg["serial"])
    if err:
        return {"ok": False, "error": err}
    if not target and friendly:
        c, out, e = await _run_adb(
            *_adb_prefix(cfg["serial"]),
            "shell",
            "pm",
            "list",
            "packages",
        )
        if c != 0:
            return {"ok": False, "error": f"cannot list packages: {(out + e)[:300]}"}
        matches = _match_app(friendly, _parse_packages(out))
        if not matches:
            return {"ok": False, "error": f"no package matched {friendly!r}"}
        target = matches[0]
    if not target:
        return {"ok": False, "error": "provide package= or name="}
    c, out, e = await _run_adb(
        *_adb_prefix(cfg["serial"]),
        "shell",
        "monkey",
        "-p",
        target,
        "-c",
        "android.intent.category.LEANBACK_LAUNCHER",
        "1",
    )
    text = (out + e).strip()
    if c != 0 or "No activities" in text or "Monkey aborted" in text:
        c2, out2, e2 = await _run_adb(
            *_adb_prefix(cfg["serial"]),
            "shell",
            "monkey",
            "-p",
            target,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        )
        text2 = (out2 + e2).strip()
        if c2 != 0 or "Monkey aborted" in text2:
            return {
                "ok": False,
                "error": f"launch failed for {target}",
                "detail": f"{text[:300]}\n{text2[:300]}",
            }
        text = text2
    return {"ok": True, "package": target, "detail": text[:400]}
