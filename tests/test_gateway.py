"""Channel gateway config + supervisor glue."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kageha.gateway.config import (
    CHANNEL_CLI,
    ensure_default_gateway_yaml,
    load_gateway_config,
)
from kageha.gateway.supervisor import ChannelGateway
from kageha.runtime.store import RuntimeStore


def test_known_channels_cover_existing_adapters():
    expected = {
        "telegram",
        "discord",
        "slack",
        "whatsapp",
        "whatsapp-qr",
        "signal",
        "matrix",
        "email",
        "imessage",
        "irc",
        "mattermost",
        "teams",
    }
    assert set(CHANNEL_CLI) == expected


def test_load_gateway_yaml_and_env_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    cfg_path = tmp_path / "gateway.yaml"
    cfg_path.write_text(
        """
channels:
  telegram:
    enabled: true
  discord:
    enabled: false
  whatsapp:
    enabled: true
    args: ["--port", "8787"]
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("KAGEHA_GATEWAY_CONFIG", str(cfg_path))
    monkeypatch.setenv("KAGEHA_GATEWAY_CHANNELS", "discord,signal")
    monkeypatch.delenv("KAGEHA_GATEWAY_AUTO_APPROVE", raising=False)

    cfg = load_gateway_config()
    assert cfg.channels["telegram"].enabled is True
    assert cfg.channels["discord"].enabled is True  # env enables
    assert cfg.channels["signal"].enabled is True
    assert cfg.channels["whatsapp"].args == ["--port", "8787"]
    assert "--runtime" not in cfg.channels["whatsapp"].cli_argv()


def test_auto_approve_env_injects_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KAGEHA_GATEWAY_CHANNELS", "telegram")
    monkeypatch.setenv("KAGEHA_GATEWAY_AUTO_APPROVE", "1")
    monkeypatch.delenv("KAGEHA_GATEWAY_CONFIG", raising=False)

    cfg = load_gateway_config()
    assert "--auto-approve-tasks" in cfg.channels["telegram"].args


def test_ensure_default_gateway_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    path = ensure_default_gateway_yaml()
    assert path.is_file()
    assert "channels:" in path.read_text(encoding="utf-8")
    # idempotent
    ensure_default_gateway_yaml()
    assert path.is_file()


def test_channel_gateway_builds_cli_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KAGEHA_GATEWAY_CHANNELS", "telegram,whatsapp-qr")
    monkeypatch.delenv("KAGEHA_GATEWAY_CONFIG", raising=False)
    store = RuntimeStore(tmp_path / "runtime.db")
    gateway = ChannelGateway(store=store, service_root=tmp_path / "services")
    try:
        defs = {d.name: d for d in gateway.supervisor.definitions()}
        assert set(defs) == {"telegram", "whatsapp-qr"}
        assert defs["telegram"].command[-1] == "telegram"
        assert "--runtime" not in defs["telegram"].command
        assert "telegram" in defs["telegram"].command
        status = gateway.status()
        assert status["gateway"] is True
        assert status["enabled_channels"] == ["telegram", "whatsapp-qr"]  # sorted
    finally:
        gateway.close()
        store.close()


def test_channel_gateway_start_stop_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KAGEHA_GATEWAY_CHANNELS", "telegram")
    monkeypatch.delenv("KAGEHA_GATEWAY_CONFIG", raising=False)
    store = RuntimeStore(tmp_path / "runtime.db")
    gateway = ChannelGateway(store=store, service_root=tmp_path / "services")
    try:
        fake = MagicMock()
        fake.pid = 4242
        with (
            patch("kageha.runtime.supervisor.subprocess.Popen", return_value=fake),
            patch("kageha.runtime.supervisor._pid_alive", return_value=False),
        ):
            started = gateway.start("telegram")
        assert started[0]["name"] == "telegram"
        assert started[0]["pid"] == 4242

        with (
            patch(
                "kageha.runtime.supervisor._pid_alive",
                side_effect=[True, False, False],
            ),
            patch("kageha.runtime.supervisor.os.kill") as kill,
        ):
            stopped = gateway.stop("telegram")
        assert stopped[0]["stopped"] is True
        kill.assert_called()
    finally:
        gateway.close()
        store.close()


def test_channel_gateway_start_all_empty_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("KAGEHA_GATEWAY_CHANNELS", raising=False)
    monkeypatch.delenv("KAGEHA_GATEWAY_CONFIG", raising=False)
    # no yaml → no enabled channels
    store = RuntimeStore(tmp_path / "runtime.db")
    gateway = ChannelGateway(store=store)
    try:
        assert gateway.start("all") == []
        assert gateway.status()["enabled_channels"] == []
    finally:
        gateway.close()
        store.close()


def test_gateway_install_linux_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KAGEHA_GATEWAY_CHANNELS", "telegram")
    store = RuntimeStore(tmp_path / "runtime.db")
    gateway = ChannelGateway(store=store, service_root=tmp_path / "services")
    gateway.supervisor.platform = "Linux"
    try:
        paths = gateway.install()
        assert len(paths) == 1
        assert paths[0].name == "kageha-gateway-telegram.service"
        text = paths[0].read_text(encoding="utf-8")
        assert "kageha.cli" in text
        assert "telegram" in text
    finally:
        gateway.close()
        store.close()
