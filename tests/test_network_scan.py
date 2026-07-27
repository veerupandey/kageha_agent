"""LAN network_scan helpers + skill ownership."""

from __future__ import annotations

from kageha.devices.network_scan import scan_lan
from kageha.memory.skills import SkillRegistry


def test_scan_lan_tv_focus(monkeypatch):
    monkeypatch.setattr(
        "kageha.devices.network_scan.discover_tv_candidates",
        lambda: {
            "local_ip": "10.0.0.40",
            "adb_hosts": [],
            "bravia_hosts": [
                {"host": "10.0.0.14", "kind": "bravia_http", "power": "active"}
            ],
            "hint": "found",
        },
    )
    report = scan_lan(focus="tv")
    assert report["focus"] == "tv"
    assert any(d.get("host") == "10.0.0.14" for d in report["devices"])


def test_network_scan_skill_owned():
    skill = SkillRegistry().skills["network_scan"]
    assert (skill.path / "scripts" / "scan.py").is_file()


def test_network_scan_skill_matches_lan_query():
    matched = SkillRegistry().match("check what tv is available in network", limit=3)
    names = [s.name for s in matched]
    assert "network_scan" in names
    assert names[0] == "network_scan"
