from __future__ import annotations

from pathlib import Path

from kageha.chat.memory_commands import (
    ChatMemorySettings,
    handle_memory_command,
)
from kageha.memory.models import MemoryQuery
from kageha.memory.service import MemoryService, reset_memory_service_for_tests


def test_chat_memory_command_lifecycle(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    monkeypatch.setenv("KAGEHA_MEMORY_EMBEDDINGS", "off")
    reset_memory_service_for_tests()
    service = MemoryService()
    settings = ChatMemorySettings()
    common = {
        "service": service,
        "settings": settings,
        "session_id": "chat-s1",
        "project_root": "/projects/kageha",
    }

    handled, remembered = handle_memory_command(
        "/memory remember Kageha uses SQLite for canonical memory.",
        **common,
    )
    assert handled is True
    memory_id = remembered.split("Remembered: ", 1)[1].split()[0]

    _, listed = handle_memory_command("/memory list project", **common)
    assert memory_id in listed
    assert "SQLite" in listed

    context = service.recall(
        MemoryQuery(
            query="SQLite canonical memory",
            project_root="/projects/kageha",
            session_id="chat-s1",
        )
    )
    _, why = handle_memory_command("/memory why", **common)
    assert context.trace_id in why

    _, corrected = handle_memory_command(
        f"/memory correct {memory_id} Kageha uses SQLite WAL for canonical memory.",
        **common,
    )
    corrected_id = corrected.split("Corrected: ", 1)[1].split()[0]
    assert corrected_id != memory_id

    _, forgotten = handle_memory_command(
        f"/memory forget {corrected_id}",
        **common,
    )
    assert "[retracted/" in forgotten

    _, off = handle_memory_command("/memory off", **common)
    assert settings.enabled is False
    assert off.startswith("Memory recall off")
    _, learn = handle_memory_command("/memory learn off", **common)
    assert settings.learning is False
    assert learn.startswith("Memory learning off")
