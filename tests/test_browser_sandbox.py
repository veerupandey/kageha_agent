from kageha.harness.browser_sandbox import (
    browser_docker_enabled,
    browser_novnc_enabled,
    browser_sandbox_status,
)
from kageha.harness.tools.browser import resolve_browser_mode


def test_resolve_browser_mode_docker(monkeypatch):
    monkeypatch.setenv("KAGEHA_BROWSER_MODE", "docker")
    assert resolve_browser_mode() == "docker"
    assert browser_docker_enabled() is True
    monkeypatch.setenv("KAGEHA_BROWSER_MODE", "comet")
    assert resolve_browser_mode() == "cdp"
    assert browser_docker_enabled() is False


def test_browser_sandbox_status():
    st = browser_sandbox_status()
    assert "docker_available" in st
    assert "image" in st
    assert "novnc" in st


def test_browser_novnc_enabled(monkeypatch):
    monkeypatch.delenv("KAGEHA_BROWSER_NOVNC", raising=False)
    assert browser_novnc_enabled() is True
    monkeypatch.setenv("KAGEHA_BROWSER_NOVNC", "0")
    assert browser_novnc_enabled() is False
