"""Channel allowlists + identity keys for Discord/Slack."""

from __future__ import annotations

from kageha.channels.discord import parse_discord_allowlist
from kageha.channels.email import parse_email_allowlist
from kageha.channels.imessage import parse_imessage_allowlist
from kageha.channels.irc import parse_irc_allowlist
from kageha.channels.matrix import parse_matrix_allowlist
from kageha.channels.session_memory import ChannelSessionStore
from kageha.channels.signal import parse_signal_allowlist
from kageha.channels.slack import SlackChannel, parse_slack_allowlist
from kageha.channels.telegram import parse_telegram_allowlist


def test_telegram_allowlist_fail_closed(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ALLOW_ALL_USERS", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "")
    assert parse_telegram_allowlist() == set()


def test_telegram_allowlist_star(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "*")
    assert parse_telegram_allowlist() is None


def test_discord_slack_allowlists(monkeypatch):
    monkeypatch.setenv("DISCORD_ALLOWED_USERS", "111,222")
    monkeypatch.setenv("SLACK_ALLOWED_USERS", "U1,U2")
    assert parse_discord_allowlist() == {"111", "222"}
    assert parse_slack_allowlist() == {"U1", "U2"}


def test_session_store_preserves_slack_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    store = ChannelSessionStore(channel="slack")
    assert store.identity_key("U01ABC") == "U01ABC"
    store.set("U01ABC", "run123")
    assert store.get("U01ABC") == "run123"


def test_whatsapp_store_still_normalizes_phone(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    store = ChannelSessionStore(channel="whatsapp")
    assert store.identity_key("+1 (555) 123-4567") == "15551234567"


def test_irc_allowlist(monkeypatch):
    monkeypatch.delenv("IRC_ALLOW_ALL_USERS", raising=False)
    monkeypatch.setenv("IRC_ALLOWED_USERS", "Alice,Bob")
    assert parse_irc_allowlist() == {"alice", "bob"}


def test_slack_hitl_pending(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    monkeypatch.setenv("SLACK_ALLOW_ALL_USERS", "1")
    ch = SlackChannel()
    assert ch.available
    # consume with nothing pending
    assert ch.consume_if_pending_human("C1", "U1", "y") is False


def test_new_channel_allowlists_fail_closed(monkeypatch):
    for key in (
        "SIGNAL_ALLOW_ALL_USERS",
        "MATRIX_ALLOW_ALL_USERS",
        "EMAIL_ALLOW_ALL_USERS",
        "IMESSAGE_ALLOW_ALL_USERS",
        "BLUEBUBBLES_ALLOW_ALL_USERS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SIGNAL_ALLOWED_USERS", "")
    monkeypatch.setenv("MATRIX_ALLOWED_USERS", "")
    monkeypatch.setenv("EMAIL_ALLOWED_USERS", "")
    monkeypatch.setenv("IMESSAGE_ALLOWED_USERS", "")
    assert parse_signal_allowlist() == set()
    assert parse_matrix_allowlist() == set()
    assert parse_email_allowlist() == set()
    assert parse_imessage_allowlist() == set()
