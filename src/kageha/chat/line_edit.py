"""Terminal line editing for chat — arrow history, cursor keys, tab complete.

Tab completions (readline, no extra deps):
  - ``/`` slash commands + nested tokens (``/model``, ``/browser use``, …)
  - ``/model <id>`` from the model registry
  - ``/resume <session>`` from recent runtime sessions
  - ``@path`` workspace / cwd file paths
"""

from __future__ import annotations

import atexit
import os
from pathlib import Path

from kageha.config import kageha_home

_SLASH_COMMANDS = (
    "/help",
    "/plan",
    "/goal",
    "/normal",
    "/build",
    "/where",
    "/status",
    "/files",
    "/sessions",
    "/resume",
    "/new",
    "/model",
    "/model list",
    "/model reset",
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

# First-token → static second-token hints (dynamic sources layered on top).
_SUBCOMMANDS: dict[str, tuple[str, ...]] = {
    "/model": ("list", "reset"),
    "/permissions": ("auto", "ask"),
    "/browser": (
        "list",
        "status",
        "use",
        "comet",
        "lightpanda",
        "chromium",
        "headless",
        "docker",
        "http",
        "cdp",
        "pack",
        "depth",
    ),
    "/computer": (
        "status",
        "doctor",
        "pack",
        "allowlist",
        "allow",
        "deny",
        "clear",
        "help",
    ),
    "/research": ("flash", "standard", "deep"),
    "/memory": (
        "status",
        "list",
        "why",
        "on",
        "off",
        "learn",
        "remember",
        "correct",
        "forget",
    ),
    "/comet": ("status", "start"),
}


def history_path() -> Path:
    return kageha_home() / "chat_history"


def _primary_slash_tokens(commands: tuple[str, ...] | list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for cmd in commands:
        token = cmd.split(maxsplit=1)[0]
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _model_ids() -> list[str]:
    try:
        from kageha.models.registry import ModelRegistry

        reg = ModelRegistry.load()
        ids = [m.id for m in reg.available_models()] or list(reg.models.keys())
        return sorted(ids)
    except Exception:  # noqa: BLE001
        return ["azure-mini", "gemini-flash", "gemini-pro", "kimi-plan"]


def _session_ids(limit: int = 20) -> list[str]:
    try:
        from kageha.runtime.store import RuntimeStore

        store = RuntimeStore()
        try:
            rows = store.list_sessions(limit)
            return [str(r.get("id") or "") for r in rows if r.get("id")]
        finally:
            store.close()
    except Exception:  # noqa: BLE001
        return []


def _path_completions(prefix: str, *, cwd: Path | None = None) -> list[str]:
    """Complete filesystem paths; keep leading ``@`` if present."""
    at = prefix.startswith("@")
    raw = prefix[1:] if at else prefix
    base = cwd or Path.cwd()
    if raw in {"", "."}:
        parent, stub = base, ""
    elif raw.endswith(("/", os.sep)):
        parent, stub = (base / raw).resolve(), ""
        if not parent.is_dir():
            return []
    else:
        p = Path(raw)
        if p.is_absolute():
            parent, stub = p.parent, p.name
        else:
            parent, stub = (base / p.parent), p.name
            parent = parent.resolve()
    try:
        if not parent.is_dir():
            return []
        names = sorted(parent.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except OSError:
        return []
    out: list[str] = []
    for entry in names:
        if stub and not entry.name.startswith(stub):
            continue
        if entry.name.startswith(".") and not stub.startswith("."):
            continue
        rel = entry.relative_to(base) if not Path(raw).is_absolute() else entry
        text = str(rel)
        if entry.is_dir():
            text += "/"
        out.append("@" + text if at else text)
        if len(out) >= 40:
            break
    return out


def completion_matches(
    line: str,
    text: str,
    *,
    commands: tuple[str, ...] | list[str] | None = None,
    cwd: Path | None = None,
) -> list[str]:
    """Return completion candidates for ``text`` given full ``line`` before cursor."""
    cmds = tuple(commands or _SLASH_COMMANDS)
    buf = line or text or ""
    # Prefer the token being completed; fall back to buffer end.
    token = text if text else (buf[buf.rfind(" ") + 1 :] if " " in buf else buf)

    # @file / path completion
    if token.startswith("@") or (
        token and not token.startswith("/") and ("/" in token or token.endswith("."))
    ):
        return _path_completions(token, cwd=cwd)

    # Slash command family
    if buf.startswith("/") or token.startswith("/"):
        parts = buf.split()
        # Still typing the first token: /mo → /model
        if len(parts) <= 1 and not buf.endswith(" "):
            primaries = _primary_slash_tokens(cmds)
            # Also offer full multi-word recipes that share the prefix
            full = [c for c in cmds if c.startswith(token)]
            # Prefer short primary tokens first for Tab cycling
            merged: list[str] = []
            for c in primaries:
                if c.startswith(token) and c not in merged:
                    merged.append(c)
            for c in full:
                if c not in merged:
                    merged.append(c)
            return merged

        head = parts[0]
        # Second (or later) token after a known slash command
        stub = "" if buf.endswith(" ") else parts[-1]
        if head == "/model":
            static = list(_SUBCOMMANDS.get("/model", ()))
            return [m for m in static + _model_ids() if m.startswith(stub)]
        if head == "/resume":
            return [s for s in _session_ids() if s.startswith(stub)]
        # Generic: static subcommands + full recipes that extend the line so far
        prefix_line = buf if buf.endswith(" ") else buf[: buf.rfind(" ") + 1]
        from_cmds = [
            c[len(prefix_line) :]
            for c in cmds
            if c.startswith(prefix_line) and c[len(prefix_line) :]
        ]
        # Only the next token from each recipe
        next_tokens: list[str] = []
        seen: set[str] = set()
        for rest in from_cmds:
            nxt = rest.split(maxsplit=1)[0]
            if nxt.startswith(stub) and nxt not in seen:
                seen.add(nxt)
                next_tokens.append(nxt)
        for sub in _SUBCOMMANDS.get(head, ()):
            if sub.startswith(stub) and sub not in seen:
                seen.add(sub)
                next_tokens.append(sub)
        return next_tokens

    return []


def setup_line_editing(*, commands: tuple[str, ...] | None = None) -> bool:
    """Enable readline history + editing for input(). Returns True if active."""
    try:
        import readline
    except ImportError:
        return False

    cmds = tuple(commands or _SLASH_COMMANDS)
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
            readline.parse_and_bind("bind -e")
            # Show matches when Tab on an ambiguous prefix (e.g. bare `/`).
            try:
                readline.parse_and_bind("bind ^I rl_complete")
            except Exception:  # noqa: BLE001
                pass
        else:
            readline.parse_and_bind("tab: complete")
            readline.parse_and_bind("set editing-mode emacs")
            readline.parse_and_bind("set horizontal-scroll-mode on")
            readline.parse_and_bind("set show-all-if-ambiguous on")
            readline.parse_and_bind("set show-all-if-unmodified on")
            readline.parse_and_bind("set menu-complete-display-prefix on")
            readline.parse_and_bind("set completion-ignore-case on")
    except Exception:  # noqa: BLE001
        pass

    def completer(text: str, state: int) -> str | None:
        try:
            buf = readline.get_line_buffer()
            # Complete only up to the cursor.
            end = readline.get_endidx()
            line = buf[:end]
        except Exception:  # noqa: BLE001
            line = text
        matches = completion_matches(line, text, commands=cmds)
        if state < len(matches):
            return matches[state]
        return None

    try:
        # Keep / and @ attached to the token being completed.
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
        length = readline.get_current_history_length()
        if length > 0 and readline.get_history_item(length) == text:
            return
        readline.add_history(text)
    except Exception:  # noqa: BLE001
        pass


def read_line(prompt: str = "you> ") -> str:
    """Read one line with editing; raises EOFError on Ctrl-D."""
    return input(prompt)
