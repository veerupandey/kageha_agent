"""prompt_toolkit input with live slash-command completion (complete while typing)."""

from __future__ import annotations


from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style

from kageha.chat.line_edit import _SLASH_COMMANDS, completion_matches, history_path

_STYLE = Style.from_dict(
    {
        "prompt": "ansicyan bold",
        "completion-menu": "bg:#0f172a #e2e8f0",
        "completion-menu.completion": "bg:#0f172a #e2e8f0",
        "completion-menu.completion.current": "bg:#22d3ee #0f172a bold",
        "completion-menu.meta.completion": "bg:#0f172a #94a3b8",
        "completion-menu.meta.completion.current": "bg:#22d3ee #0f172a",
        "scrollbar.background": "bg:#1e293b",
        "scrollbar.button": "bg:#38bdf8",
        "auto-suggest": "#64748b",
    }
)

_META: dict[str, str] = {
    "/help": "commands",
    "/plan": "clarify → plan.md → /build",
    "/goal": "execute with HITL",
    "/normal": "default chat",
    "/build": "run approved plan",
    "/model": "list / pin models",
    "/model list": "show models",
    "/model reset": "clear pins",
    "/sessions": "recent runs",
    "/resume": "continue a session",
    "/new": "fresh task",
    "/browser": "browser pack",
    "/computer": "macOS computer-use",
    "/research": "web research",
    "/permissions": "ask | auto | full",
    "/memory": "memory controls",
    "/verbose": "show routing",
    "/quiet": "compact status",
    "/quit": "exit",
    "/exit": "exit",
}


class SlashCompleter(Completer):
    """Completions for ``/`` commands, models, sessions, and ``@`` paths."""

    def __init__(self, *, commands: tuple[str, ...] | None = None) -> None:
        self.commands = tuple(commands or _SLASH_COMMANDS)

    def get_completions(self, document, complete_event):  # noqa: ANN001
        text = document.text_before_cursor
        # Token under cursor (last whitespace-separated piece).
        token = text[text.rfind(" ") + 1 :] if " " in text else text
        # Only auto-complete slash / @ paths — not free prose.
        if not (text.startswith("/") or token.startswith("/") or token.startswith("@")):
            return
        matches = completion_matches(text, token, commands=self.commands)
        for m in matches:
            # Replacement should be the completion token; for multi-word recipes
            # when still on the first token, use the full match string.
            display = m
            meta = _META.get(m, "")
            if m.startswith("/") and " " not in token and " " in m:
                # Completing from `/` or `/mo` → offer `/model` and recipes.
                start = -len(token) if token else 0
            elif token.startswith("@") or token.startswith("/"):
                start = -len(token)
            else:
                start = -len(token) if token else 0
            yield Completion(
                m,
                start_position=start,
                display=display,
                display_meta=meta,
            )


def _session() -> PromptSession[str]:
    hist = history_path()
    hist.parent.mkdir(parents=True, exist_ok=True)
    kb = KeyBindings()

    @kb.add(Keys.Escape)
    def _(event) -> None:  # noqa: ANN001
        # Esc closes the completion menu if open; otherwise ignore.
        buff = event.app.current_buffer
        if buff.complete_state:
            buff.cancel_completion()

    return PromptSession(
        history=FileHistory(str(hist)),
        completer=SlashCompleter(),
        complete_while_typing=True,
        complete_in_thread=True,
        auto_suggest=AutoSuggestFromHistory(),
        style=_STYLE,
        key_bindings=kb,
        mouse_support=False,
    )


_SESSION: PromptSession[str] | None = None


def get_prompt_session() -> PromptSession[str]:
    global _SESSION
    if _SESSION is None:
        _SESSION = _session()
    return _SESSION


def _prompt_message(prompt: str) -> HTML:
    label = prompt.rstrip()
    if label.endswith(">"):
        label = label[:-1].rstrip()
    return HTML(f"<prompt>{label}</prompt><prompt>&gt; </prompt>")


async def read_chat_line(prompt: str = "you> ") -> str:
    """Read a line with live ``/`` completion. Raises EOFError on Ctrl-D.

    Must be awaited — the chat REPL already owns an asyncio loop, so the
    sync ``session.prompt()`` path (which calls ``asyncio.run()``) fails with
    "cannot be called from a running event loop".
    """
    session = get_prompt_session()
    return await session.prompt_async(_prompt_message(prompt))


def read_chat_line_sync(prompt: str = "you> ") -> str:
    """Blocking variant safe to call from a running event loop (threaded UI)."""
    session = get_prompt_session()
    return session.prompt(_prompt_message(prompt), in_thread=True)
