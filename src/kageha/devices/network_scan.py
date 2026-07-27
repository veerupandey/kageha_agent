"""LAN discovery library (skill-owned) — same Wi‑Fi subnet port/service scan.

Finds TVs (Sony Bravia / Android ADB), printers, SSH, HTTP, Chromecast-ish
ports, etc. Read-only; does not send control commands.
Use via skill_run network_scan scripts/scan.py.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from kageha.devices.android_tv import (
    _local_ipv4,
    _port_open,
    _probe_bravia,
    _subnet_hosts,
    discover_tv_candidates,
)

# Port → friendly label (keep list small; full Nmap is out of scope).
_SERVICE_PORTS: dict[int, str] = {
    22: "ssh",
    80: "http",
    443: "https",
    554: "rtsp",
    8008: "chromecast_alt",
    8009: "chromecast",
    8080: "http_alt",
    8443: "https_alt",
    9100: "printer_jetdirect",
    32400: "plex",
    5555: "android_adb",
    62078: "apple_lockdown",
}


def _open_ports(host: str, ports: list[int], *, timeout: float = 0.18) -> list[int]:
    open_p: list[int] = []
    for p in ports:
        if _port_open(host, p, timeout=timeout):
            open_p.append(p)
    return open_p


def _arp_names() -> dict[str, str]:
    """Best-effort IP → hostname from ARP / dns cache (macOS/Linux)."""
    import subprocess

    out: dict[str, str] = {}
    try:
        text = subprocess.check_output(
            ["arp", "-a"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except Exception:  # noqa: BLE001
        return out
    # ? (10.0.0.1) at aa:bb:… on en0
    # hostname.local (10.0.0.14) at …
    import re

    for line in text.splitlines():
        m = re.search(r"^(\S+)\s+\((\d+\.\d+\.\d+\.\d+)\)", line)
        if not m:
            continue
        name, ip = m.group(1), m.group(2)
        if name and name != "?":
            out[ip] = name.rstrip(".")
    return out


def scan_lan(
    *,
    focus: str = "all",
    max_workers: int = 48,
) -> dict[str, Any]:
    """Scan local /24 for open ports of interest.

    ``focus``: ``all`` | ``tv`` (TV-oriented ports only) | ``quick`` (TV + ssh + http).
    """
    focus = (focus or "all").strip().lower()
    local = _local_ipv4()
    report: dict[str, Any] = {
        "local_ip": local or "",
        "focus": focus,
        "devices": [],
        "tv": {},
        "hint": "",
    }
    if not local:
        report["hint"] = "Could not determine local Wi‑Fi IP."
        return report

    if focus == "tv":
        tv = discover_tv_candidates()
        report["tv"] = tv
        devices = []
        for h in tv.get("adb_hosts") or []:
            devices.append(
                {
                    "host": h,
                    "name": "",
                    "ports": [5555],
                    "services": ["android_adb"],
                    "kind": "android_tv_adb",
                }
            )
        for b in tv.get("bravia_hosts") or []:
            devices.append(
                {
                    "host": b.get("host"),
                    "name": b.get("name") or b.get("model") or "",
                    "ports": [80],
                    "services": ["sony_bravia"],
                    "kind": "sony_bravia",
                    "detail": b,
                }
            )
        report["devices"] = devices
        report["hint"] = tv.get("hint") or (
            f"Found {len(devices)} TV-related device(s)."
            if devices
            else "No TV services found on this Wi‑Fi."
        )
        return report

    if focus == "quick":
        ports = [22, 80, 443, 5555, 8008, 8009]
    else:
        ports = sorted(_SERVICE_PORTS)

    hosts = _subnet_hosts(local)
    arp = _arp_names()
    hits: list[dict[str, Any]] = []

    def probe(h: str) -> dict[str, Any] | None:
        open_p = _open_ports(h, ports)
        if not open_p:
            return None
        services = [_SERVICE_PORTS.get(p, str(p)) for p in open_p]
        row: dict[str, Any] = {
            "host": h,
            "name": arp.get(h, ""),
            "ports": open_p,
            "services": services,
            "kind": "host",
        }
        if 80 in open_p or 8080 in open_p:
            bravia = _probe_bravia(h)
            if bravia:
                row["kind"] = "sony_bravia"
                row["detail"] = bravia
                if not row["name"]:
                    row["name"] = str(bravia.get("model") or bravia.get("name") or "")
        if 5555 in open_p and row["kind"] == "host":
            row["kind"] = "android_adb"
        return row

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for row in pool.map(probe, hosts):
            if row:
                hits.append(row)

    hits.sort(key=lambda r: tuple(int(x) for x in str(r["host"]).split(".")))
    report["devices"] = hits
    # Compact TV summary for agents
    report["tv"] = {
        "bravia": [d for d in hits if d.get("kind") == "sony_bravia"],
        "android_adb": [d for d in hits if d.get("kind") == "android_adb"],
    }
    if hits:
        report["hint"] = (
            f"Found {len(hits)} device(s) with open services on {local.rsplit('.', 1)[0]}.0/24. "
            "For Sony TV control use skill sony_bravia; for ADB use skill android_tv."
        )
    else:
        report["hint"] = (
            "No matching open ports on this Wi‑Fi subnet. "
            "Devices may be asleep, on another VLAN, or firewalled."
        )
    return report
