"""Android TV adb helpers + skill ownership."""

from __future__ import annotations

import pytest

from kageha.devices.android_tv import (
    _KEY_MAP,
    _match_app,
    _parse_packages,
    _subnet_hosts,
    discover_tv_candidates,
    launch_package,
    send_key,
)
from kageha.memory.skills import SkillRegistry


def test_parse_and_match_sonyliv():
    raw = """
package:com.android.vending
package:com.sonyliv
package:com.netflix.ninja
package:com.google.android.youtube.tv
"""
    pkgs = _parse_packages(raw)
    assert "com.sonyliv" in pkgs
    assert _match_app("sonyliv", pkgs)[0] == "com.sonyliv"
    assert any("netflix" in p for p in _match_app("netflix", pkgs))


def test_key_map_has_remote_basics():
    for k in ("home", "back", "up", "ok", "volume_up", "power", "play_pause"):
        assert k in _KEY_MAP


def test_subnet_hosts_skips_self():
    hosts = _subnet_hosts("10.0.0.40")
    assert "10.0.0.40" not in hosts
    assert "10.0.0.1" in hosts
    assert len(hosts) == 253


def test_discover_tv_candidates_structure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "kageha.devices.android_tv._local_ipv4", lambda: "10.0.0.40"
    )
    monkeypatch.setattr(
        "kageha.devices.android_tv._port_open", lambda h, p, timeout=0.2: False
    )
    report = discover_tv_candidates(scan_bravia=False)
    assert report["local_ip"] == "10.0.0.40"
    assert report["adb_hosts"] == []
    assert "No ADB" in report["hint"] or "not found" in report["hint"].lower()


@pytest.mark.asyncio
async def test_android_tv_key_unknown():
    out = await send_key("nope")
    assert out.get("ok") is False
    assert "unknown key" in str(out.get("error") or "")


@pytest.mark.asyncio
async def test_android_tv_launch_flow(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KAGEHA_ANDROID_TV_HOST", "10.0.0.9")
    monkeypatch.setattr(
        "kageha.devices.android_tv.shutil.which", lambda _: "/usr/bin/adb"
    )

    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str, timeout: float = 20.0):
        del timeout
        calls.append(args)
        if args[:1] == ("connect",) or (args and args[0] == "connect"):
            return 0, "connected to 10.0.0.9:5555\n", ""
        if "devices" in args:
            return 0, "List of devices\n10.0.0.9:5555\tdevice\n", ""
        if "list" in args and "packages" in args:
            return 0, "package:com.sonyliv\npackage:com.netflix.ninja\n", ""
        if "monkey" in args:
            return 0, "Events injected: 1\n", ""
        return 0, "", ""

    monkeypatch.setattr("kageha.devices.android_tv._run_adb", fake_run)
    out = await launch_package(name="sonyliv")
    assert out.get("ok") is True
    assert out.get("package") == "com.sonyliv"
    assert any("monkey" in c for c in calls)


def test_android_tv_skill_scripts():
    skill = SkillRegistry().skills["android_tv"]
    assert (skill.path / "scripts" / "key.py").is_file()
    assert (skill.path / "scripts" / "discover.py").is_file()
