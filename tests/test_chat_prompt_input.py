"""Live slash completion via prompt_toolkit."""

from __future__ import annotations

import asyncio
import inspect

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from kageha.chat.line_edit import completion_matches
from kageha.chat.prompt_input import SlashCompleter, read_chat_line


def test_read_chat_line_is_async_for_nested_loop_safety():
    # Sync prompt_toolkit.prompt() calls asyncio.run(), which crashes inside
    # run_chat_repl's event loop — the public API must be awaitable.
    assert inspect.iscoroutinefunction(read_chat_line)
    assert asyncio.iscoroutinefunction(read_chat_line)


def test_slash_alone_lists_primary_commands():
    hits = completion_matches("/", "/")
    assert "/help" in hits
    assert "/model" in hits
    assert "/plan" in hits
    assert all(h.startswith("/") for h in hits)


def test_completer_yields_on_slash():
    comp = SlashCompleter()
    docs = Document("/", cursor_position=1)
    items = list(comp.get_completions(docs, CompleteEvent(text_inserted=True)))
    texts = [c.text for c in items]
    assert "/help" in texts
    assert "/model" in texts
    # Meta hints present for common commands
    metas = {c.text: c.display_meta_text for c in items}
    assert metas.get("/plan")


def test_completer_filters_prefix():
    comp = SlashCompleter()
    docs = Document("/mo", cursor_position=3)
    items = list(comp.get_completions(docs, CompleteEvent(text_inserted=True)))
    texts = [c.text for c in items]
    assert "/model" in texts
    assert all(t.startswith("/mo") for t in texts)


def test_completer_ignores_plain_prose():
    comp = SlashCompleter()
    docs = Document("hello world", cursor_position=11)
    items = list(comp.get_completions(docs, CompleteEvent(text_inserted=True)))
    assert items == []
