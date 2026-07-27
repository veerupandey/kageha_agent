from pathlib import Path

from kageha.eval.cassettes import CassetteStore
from kageha.models.base import ChatMessage, ChatResponse, ChatUsage


def test_cassette_roundtrip(tmp_path: Path):
    store = CassetteStore(tmp_path)
    msgs = [ChatMessage(role="user", content="hi")]
    key = store.key(msgs, "m1")
    resp = ChatResponse(
        message=ChatMessage(role="assistant", content="ok"),
        usage=ChatUsage(prompt_tokens=1, completion_tokens=1),
        model="m1",
    )
    store.save(key, resp)
    loaded = store.load(key)
    assert loaded is not None
    assert loaded.message.content == "ok"
