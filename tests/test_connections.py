"""Unit tests for OAuth connections store + registry (no real OAuth)."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from kageha.connections.base import ConnectionProvider, ConnectionStatus
from kageha.connections.registry import (
    get_provider,
    list_providers,
    provider_ids,
    register_provider,
    reset_registry,
)
from kageha.connections.store import ConnectionStore


@pytest.fixture(autouse=True)
def _iso_registry(monkeypatch, tmp_path):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.delenv("KAGEHA_CONNECTIONS_ALLOWLIST", raising=False)
    reset_registry()
    yield
    reset_registry()


def test_store_saves_with_0600(tmp_path):
    store = ConnectionStore(root=tmp_path / "connections")
    path = store.save(
        "gmail",
        {"account": "a@example.com", "token": {"access_token": "secret"}},
    )
    assert path.is_file()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    loaded = store.load("gmail")
    assert loaded is not None
    assert loaded["account"] == "a@example.com"
    assert loaded["provider"] == "gmail"
    assert "updated_at" in loaded
    assert store.file_mode("gmail") == 0o600


def test_store_rejects_unsafe_ids(tmp_path):
    store = ConnectionStore(root=tmp_path / "connections")
    with pytest.raises(ValueError):
        store.path_for("../evil")
    with pytest.raises(ValueError):
        store.path_for("gmail/foo")


def test_store_delete_and_list(tmp_path):
    store = ConnectionStore(root=tmp_path / "connections")
    store.save("github", {"token": {"access_token": "t"}})
    store.save("gmail", {"token": {"access_token": "t2"}})
    assert set(store.list_stored()) == {"gmail", "github"}
    assert store.delete("github") is True
    assert store.delete("github") is False
    assert store.list_stored() == ["gmail"]


def test_provider_registry_default_allowlist():
    ids = provider_ids()
    assert ids == ["gcal", "gdrive", "github", "gmail"]
    gmail = get_provider("gmail")
    assert gmail.id == "gmail"
    assert "gmail.modify" in " ".join(gmail.scopes)


def test_provider_allowlist_env(monkeypatch):
    monkeypatch.setenv("KAGEHA_CONNECTIONS_ALLOWLIST", "gmail,github")
    reset_registry()
    assert provider_ids() == ["github", "gmail"]
    with pytest.raises(KeyError):
        get_provider("gcal")


def test_provider_allowlist_star(monkeypatch):
    monkeypatch.setenv("KAGEHA_CONNECTIONS_ALLOWLIST", "*")
    reset_registry()
    assert "gdrive" in provider_ids()


def test_require_connected_fail_closed(tmp_path):
    store = ConnectionStore(root=tmp_path / "connections")
    gmail = get_provider("gmail")
    st = gmail.status(store=store)
    assert st.connected is False
    with pytest.raises(RuntimeError, match="kageha connect login gmail"):
        gmail.require_connected(store=store)


def test_logout_clears_store(tmp_path):
    store = ConnectionStore(root=tmp_path / "connections")
    store.save("gmail", {"account": "x", "token": {"access_token": "t"}})
    st = get_provider("gmail").logout(store=store)
    assert st.connected is False
    assert store.load("gmail") is None


class _FakeProvider(ConnectionProvider):
    id = "fake"
    label = "Fake"
    scopes = ["read"]
    description = "test only"

    def login(self, *, store: Any, open_browser: bool = True) -> ConnectionStatus:
        store.save(self.id, {"account": "u", "token": {"access_token": "t"}})
        return ConnectionStatus(provider=self.id, connected=True, account="u")

    def status(self, *, store: Any) -> ConnectionStatus:
        data = store.load(self.id)
        if not data:
            return ConnectionStatus(provider=self.id, connected=False)
        return ConnectionStatus(
            provider=self.id, connected=True, account=str(data.get("account") or "")
        )


def test_register_custom_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("KAGEHA_CONNECTIONS_ALLOWLIST", "*")
    reset_registry()
    register_provider(_FakeProvider(), force=True)
    store = ConnectionStore(root=tmp_path / "c")
    p = get_provider("fake")
    st = p.login(store=store)
    assert st.connected and st.account == "u"
    assert p.status(store=store).connected


def test_gmail_api_not_connected_message(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    from kageha.connections import gmail_api

    with pytest.raises(RuntimeError, match="kageha connect login gmail"):
        gmail_api.get_gmail_token()


def test_xoauth2_string_format():
    from kageha.connections.gmail_api import xoauth2_string

    s = xoauth2_string("user@example.com", "tok123")
    assert s.startswith("user=user@example.com\x01auth=Bearer tok123")
    assert s.endswith("\x01\x01")


def test_email_channel_uses_xoauth2_when_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.delenv("EMAIL_IMAP_PASSWORD", raising=False)
    monkeypatch.delenv("EMAIL_IMAP_HOST", raising=False)
    monkeypatch.delenv("EMAIL_SMTP_HOST", raising=False)
    monkeypatch.delenv("EMAIL_IMAP_USER", raising=False)
    monkeypatch.delenv("EMAIL_FROM", raising=False)

    store = ConnectionStore()
    store.save(
        "gmail",
        {
            "account": "bot@gmail.com",
            "token": {
                "token": "access",
                "refresh_token": "refresh",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "cid",
                "client_secret": "sec",
                "scopes": [],
            },
        },
    )

    # Bypass real Google refresh by stubbing status/access_token path.
    from kageha.connections.providers import gmail as gmail_mod

    class _StubStatus:
        connected = True
        account = "bot@gmail.com"
        error = ""

    monkeypatch.setattr(
        gmail_mod.GmailProvider,
        "status",
        lambda self, *, store: _StubStatus(),
    )

    from kageha.channels.email import EmailChannel

    ch = EmailChannel()
    assert ch.use_xoauth2 is True
    assert ch.imap_host == "imap.gmail.com"
    assert ch.smtp_host == "smtp.gmail.com"
    assert ch.imap_user == "bot@gmail.com"
    assert ch.available is True


def test_connect_cli_list(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    from typer.testing import CliRunner

    from kageha.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["connect", "list"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    ids = {r["id"] for r in rows}
    assert {"gmail", "gcal", "gdrive", "github"} <= ids
    assert all(r["connected"] is False for r in rows)


def test_list_providers_labels():
    labels = {p.id: p.label for p in list_providers()}
    assert labels["gmail"] == "Gmail"
    assert labels["github"] == "GitHub"


def test_google_oauth_client_configured(monkeypatch):
    from kageha.connections.google_oauth import google_oauth_client_configured
    from kageha.connections.setup import ensure_google_oauth_client

    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GMAIL_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GMAIL_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_JSON", raising=False)
    assert google_oauth_client_configured() is False
    with pytest.raises(RuntimeError, match="connect credentials"):
        ensure_google_oauth_client(interactive=False)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csec")
    assert google_oauth_client_configured() is True
    ensure_google_oauth_client(interactive=False)  # no-op


def test_install_google_client_json_like_gog(tmp_path, monkeypatch):
    from kageha.connections.google_oauth import (
        google_client_config,
        google_oauth_client_configured,
    )
    from kageha.connections.setup import install_google_client_json

    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_JSON", raising=False)

    src = tmp_path / "client_secret_test.json"
    src.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "abc.apps.googleusercontent.com",
                    "client_secret": "s3cret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
        ),
        encoding="utf-8",
    )
    result = install_google_client_json(src)
    dest = Path(result["path"])
    assert dest.is_file()
    assert dest.name == "google-client.json"
    mode = stat.S_IMODE(dest.stat().st_mode)
    assert mode == 0o600
    assert google_oauth_client_configured() is True
    cfg = google_client_config()
    assert cfg["installed"]["client_id"] == "abc.apps.googleusercontent.com"
    assert cfg["installed"]["client_secret"] == "s3cret"


def test_connect_credentials_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_JSON", raising=False)
    src = tmp_path / "client_secret.json"
    src.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "x.apps.googleusercontent.com",
                    "client_secret": "y",
                    "redirect_uris": ["http://localhost"],
                }
            }
        ),
        encoding="utf-8",
    )
    from typer.testing import CliRunner

    from kageha.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["connect", "credentials", str(src)])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert (tmp_path / "connections" / "google-client.json").is_file()
