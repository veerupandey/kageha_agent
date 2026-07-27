"""WhatsApp setup wizard — env upsert (non-interactive parts)."""

from pathlib import Path

from kageha.channels.whatsapp_setup import (
    needs_whatsapp_setup,
    read_env_value,
    upsert_env_key,
)


def test_upsert_env_key(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("FOO=1\nWHATSAPP_ALLOWED_USERS=old\nBAR=2\n", encoding="utf-8")
    monkeypatch.setattr(
        "kageha.channels.whatsapp_setup.resolve_env_file",
        lambda: env,
    )
    path = upsert_env_key("WHATSAPP_ALLOWED_USERS", "15551234567", env)
    text = path.read_text(encoding="utf-8")
    assert "WHATSAPP_ALLOWED_USERS=15551234567" in text
    assert "WHATSAPP_ALLOWED_USERS=old" not in text
    assert "FOO=1" in text
    assert read_env_value("WHATSAPP_ALLOWED_USERS", env) == "15551234567"


def test_upsert_appends_missing(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("FOO=1\n", encoding="utf-8")
    upsert_env_key("WHATSAPP_ALLOWED_USERS", "19998887777", env)
    assert read_env_value("WHATSAPP_ALLOWED_USERS", env) == "19998887777"


def test_needs_setup(monkeypatch, tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.setattr("kageha.channels.whatsapp_setup.resolve_env_file", lambda: env)
    monkeypatch.delenv("WHATSAPP_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("WHATSAPP_QR_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("WHATSAPP_ALLOW_ALL_USERS", raising=False)
    assert needs_whatsapp_setup() is True
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "15551234567")
    assert needs_whatsapp_setup() is False
