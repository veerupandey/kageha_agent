from __future__ import annotations

import json

import pytest

from kageha.channels.models import ChannelMedia, ChannelMessage
from kageha.channels.runtime import chunk_text
from kageha.channels.runtime import ChannelRuntime
from kageha.channels.telegram import TelegramAdapter, markdown_to_telegram_html
from kageha.channels.whatsapp_qr import WhatsAppQrAdapter
from kageha.runtime.channels import DurableChannelQueue
from kageha.runtime.store import RuntimeStore


def test_channel_message_identity_includes_account_peer_and_thread():
    message = ChannelMessage(
        channel="telegram",
        account_id="default",
        peer_id="42",
        thread_id="7",
        message_id="m1",
        media=(ChannelMedia(kind="image", filename="x.png"),),
    )
    assert message.identity == "default:42:7"


def test_chunk_text_prefers_paragraph_boundaries():
    chunks = chunk_text("a" * 20 + "\n" + "b" * 20, limit=25)
    assert chunks == ["a" * 20, "b" * 20]


def test_markdown_to_telegram_html_covers_assistant_formatting():
    rendered = markdown_to_telegram_html(
        "# Title\n\n**bold** and `code`\n\n- item\n\n```python\nprint('<ok>')\n```"
    )
    assert "<b>Title</b>" in rendered
    assert "<b>bold</b>" in rendered
    assert "<code>code</code>" in rendered
    assert "• item" in rendered
    assert "&lt;ok&gt;" in rendered


def test_internal_runtime_artifacts_never_leave_channel_replies():
    from kageha.loop.artifacts import classify_artifacts

    assert classify_artifacts(["goal_card_prior.json", "artifacts/report.pdf"]) == [
        "artifacts/report.pdf"
    ]


def test_channel_queue_accepts_rich_payload_and_deduplicates(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.db")
    try:
        queue = DurableChannelQueue("telegram", store)
        first = queue.register_inbound(
            identity="default:42",
            external_id="update-1",
            text="look",
            payload={"text": "look", "media": [{"kind": "image"}]},
        )
        duplicate = queue.register_inbound(
            identity="default:42",
            external_id="update-1",
            text="look",
            payload={"text": "look", "media": [{"kind": "image"}]},
        )
        assert first.accepted is True
        assert duplicate.accepted is False
        row = store._conn.execute(
            "SELECT payload_json FROM channel_messages WHERE id=?", (first.message_id,)
        ).fetchone()
        assert "image" in row[0]
    finally:
        store.close()


class FakeAppServer:
    def __init__(self):
        self.requests = []

    async def handle(self, request):
        self.requests.append(request)
        return {
            "result": {
                "run_id": "session-e2e",
                "turn_id": "turn-e2e",
                "status": "success",
                "message": "reply from runtime",
                "artifacts": [],
            }
        }

    def close(self):
        pass


class FakeTelegram(TelegramAdapter):
    def __init__(self, runtime):
        super().__init__(runtime, token="test-token", client=object())
        self.sent = []
        self.allowed_users = {"42"}

    async def call(self, method, **params):
        self.sent.append((method, params))
        return []


class FakeStdin:
    def __init__(self):
        self.lines = []

    def write(self, value):
        self.lines.append(value)

    async def drain(self):
        return None


class FakeProcess:
    def __init__(self):
        self.stdin = FakeStdin()


@pytest.mark.asyncio
async def test_telegram_end_to_end_normalizes_deduplicates_and_replies(tmp_path):
    server = FakeAppServer()
    from kageha.runtime.store import RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.db")
    runtime = ChannelRuntime(server=server, store=store)
    adapter = FakeTelegram(runtime)
    try:
        update = {
            "update_id": 10,
            "message": {
                "message_id": 11,
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 42},
                "text": "hello",
            },
        }
        first = await adapter.handle_update(update)
        duplicate = await adapter.handle_update(update)
        assert first and first.text == "reply from runtime"
        assert duplicate is None
        assert len(server.requests) == 1
        assert adapter.sent == [
            (
                "sendMessage",
                {"chat_id": "42", "text": "reply from runtime", "parse_mode": "HTML"},
            )
        ]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_whatsapp_qr_sidecar_event_end_to_end(tmp_path):
    server = FakeAppServer()
    from kageha.runtime.store import RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.db")
    runtime = ChannelRuntime(server=server, store=store)
    adapter = WhatsAppQrAdapter(runtime)
    adapter.allowed_users = {"15551234567"}
    adapter.process = FakeProcess()
    try:
        await adapter.handle_event(
            {
                "type": "message",
                "id": "wa-message-1",
                "from": "15551234567",
                "text": "hello",
            }
        )
        assert len(server.requests) == 1
        command = json.loads(adapter.process.stdin.lines[0])
        assert command["type"] == "send"
        assert command["to"] == "15551234567"
        assert command["text"] == "reply from runtime"
    finally:
        await runtime.close()
