"""Terminal line editing for chat — arrow history, cursor keys, tab complete."""

from __future__ import annotations

import atexit
from pathlib import Path

from kageha.config import kageha_home

_SLASH_COMMANDS = (
    "/help",
    "/where",
    "/status",
    "/files",
    "/sessions",
    "/resume",
    "/new",
    "/model",
    "/model list",
    "/model reset",
    "/model gemini-flash",
    "/model gemini-flash --once",
    "/model gemini-pro --session",
    "/model gemini-pro --global",
    "/model kimi-plan",
    "/comet",
    "/comet status",
    "/browser",
    "/browser list",
    "/browser status",
    "/browser use",
    "/browser comet",
    "/browser comet start",
    "/browser lightpanda",
    "/browser chromium",
    "/browser headless",
    "/browser docker",
    "/browser http",
    "/browser cdp",
    "/browser pack on",
    "/browser pack off",
    "/browser depth flash",
    "/computer",
    "/computer status",
    "/computer doctor",
    "/computer pack on",
    "/computer pack off",
    "/computer pack auto",
    "/computer allowlist",
    "/computer allow",
    "/computer deny",
    "/computer clear",
    "/computer help",
    "/research",
    "/research flash",
    "/research standard",
    "/research deep",
    "/permissions",
    "/permissions auto",
    "/permissions ask",
    "/memory status",
    "/memory list",
    "/memory why",
    "/memory on",
    "/memory off",
    "/memory learn on",
    "/memory learn off",
    "/memory remember",
    "/memory correct",
    "/memory forget",
    "/verbose",
    "/quiet",
    "/quit",
    "/exit",
)


def history_path() -> Path:
    return kageha_home() / "chat_history"


def setup_line_editing(*, commands: tuple[str, ...] | None = None) -> bool:
    """Enable readline history + editing for input(). Returns True if active."""
    try:
        import readline
    except ImportError:
        return False

    cmds = list(commands or _SLASH_COMMANDS)
    path = history_path()
    try:
        if path.is_file():
            readline.read_history_file(str(path))
    except OSError:
        pass

    try:
        readline.set_history_length(2000)
    except Exception:  # noqa: BLE001
        pass

    # macOS ships libedit; GNU readline uses different bind syntax.
    doc = getattr(readline, "__doc__", "") or ""
    try:
        if "libedit" in doc.lower():
            readline.parse_and_bind("bind ^I rl_complete")
            # Emacs-style editing (arrows, Ctrl-A/E, etc.)
            readline.parse_and_bind("bind -e")
        else:
            readline.parse_and_bind("tab: complete")
            readline.parse_and_bind("set editing-mode emacs")
            readline.parse_and_bind("set horizontal-scroll-mode on")
    except Exception:  # noqa: BLE001
        pass

    def completer(text: str, state: int) -> str | None:
        if not text.startswith("/"):
            return None
        matches = [c for c in cmds if c.startswith(text)]
        if state < len(matches):
            return matches[state]
        return None

    try:
        readline.set_completer(completer)
        readline.set_completer_delims(" \t\n")
    except Exception:  # noqa: BLE001
        pass

    def _save() -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            readline.write_history_file(str(path))
        except OSError:
            pass

    atexit.register(_save)
    return True


def remember(line: str) -> None:
    """Append a non-empty line to in-memory history (persisted on exit)."""
    text = (line or "").strip()
    if not text:
        return
    try:
        import readline
    except ImportError:
        return
    try:
        # Avoid stacking identical consecutive entries.
        length = readline.get_current_history_length()
        if length > 0 and readline.get_history_item(length) == text:
            return
        readline.add_history(text)
    except Exception:  # noqa: BLE001
        pass


def read_line(prompt: str = "you> ") -> str:
    """Read one line with editing; raises EOFError on Ctrl-D."""
    return input(prompt)
