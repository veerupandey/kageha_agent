"""Rich console helpers for interactive chat (shared with TransientProgress).

Modern, colorful terminal chrome — still readline for input.
"""

from __future__ import annotations

import os
import re
from typing import Any

from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# Teal → sky → amber → rose (readable on dark terminals; avoids purple-glow cliché).
_BRAND_COLORS = ("#2dd4bf", "#22d3ee", "#38bdf8", "#fbbf24", "#fb7185")

_THEME = Theme(
    {
        "k.brand": "bold #22d3ee",
        "k.muted": "dim #94a3b8",
        "k.info": "bold #38bdf8",
        "k.ok": "bold #34d399",
        "k.warn": "bold #fbbf24",
        "k.err": "bold #fb7185",
        "k.model": "bold #f472b6",
        "k.chip": "bold #e2e8f0 on #1e293b",
        "k.chip.ok": "bold #052e16 on #34d399",
        "k.chip.warn": "bold #422006 on #fbbf24",
        "k.chip.model": "bold #500724 on #f9a8d4",
        "markdown.h1": "bold #22d3ee",
        "markdown.h2": "bold #38bdf8",
        "markdown.h3": "bold #2dd4bf",
        "markdown.code": "bold #fbbf24",
        "markdown.link": "underline #38bdf8",
        "markdown.item.bullet": "#2dd4bf",
    }
)

_console: Console | None = None


def get_console(*, reset: bool = False) -> Console:
    """Process-wide Console (respects NO_COLOR / non-TTY).

    Width/height are not pinned — Rich re-reads the terminal size on each
    render so panels and tables follow resize (new output; history stays).
    """
    global _console
    if reset or _console is None:
        no_color = bool(os.environ.get("NO_COLOR"))
        _console = Console(
            highlight=True,
            soft_wrap=True,
            no_color=no_color or None,
            theme=_THEME,
        )
    return _console


def set_console(console: Console | None) -> None:
    """Override the shared console (tests)."""
    global _console
    _console = console


def fit_panel_width(console: Console, *, min_width: int = 40) -> int | None:
    """Clamp panel width to the live terminal size (None when non-TTY/fixed)."""
    try:
        # Rich only honors constructor width when height is also set; prefer the
        # pinned _width when present so tests and narrow panes stay accurate.
        pinned = getattr(console, "_width", None)
        width = int(pinned if pinned is not None else console.width)
    except Exception:  # noqa: BLE001
        return None
    if width <= 0:
        return None
    # Leave a column so borders don't wrap onto the next line in tight panes.
    return max(min_width, width - 1)


def _panel(
    renderable: Any,
    *,
    console: Console,
    **kwargs: Any,
) -> Panel:
    """Panel that tracks the current terminal width."""
    width = fit_panel_width(console)
    if width is not None and "width" not in kwargs:
        kwargs["width"] = width
    kwargs.setdefault("expand", True)
    return Panel(renderable, **kwargs)


def _brand_word(word: str = "Kageha") -> Text:
    t = Text(justify="left")
    colors = _BRAND_COLORS
    for i, ch in enumerate(word):
        t.append(ch, style=Style(color=colors[i % len(colors)], bold=True))
    return t


def _chip(label: str, value: str, *, kind: str = "chip") -> Text:
    style = {
        "ok": "k.chip.ok",
        "warn": "k.chip.warn",
        "model": "k.chip.model",
        "chip": "k.chip",
    }.get(kind, "k.chip")
    return Text.assemble(
        (f" {label} ", "bold #94a3b8 on #0f172a"),
        (f" {value} ", style),
        (" ", ""),
    )


def print_status(text: str, *, console: Console | None = None) -> None:
    c = console or get_console()
    c.print(Text.assemble((" · ", "k.muted"), (text, "k.muted")))


def print_info(text: str, *, console: Console | None = None) -> None:
    c = console or get_console()
    c.print(Text.assemble((" › ", "k.info"), (text, "k.info")))


def print_error(text: str, *, console: Console | None = None) -> None:
    c = console or get_console()
    c.print(
        _panel(
            Text(text, style="k.err"),
            console=c,
            title="[bold #fb7185]error[/]",
            border_style="#fb7185",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def print_ok(text: str, *, console: Console | None = None) -> None:
    c = console or get_console()
    c.print(Text.assemble((" ✓ ", "k.ok"), (text, "k.ok")))


def _approval_chip(approvals: str) -> Text | None:
    a = (approvals or "").lower()
    if not approvals:
        return None
    if "auto" in a:
        return _chip("approvals", "auto", kind="ok")
    return _chip("approvals", "ask", kind="warn")


def _model_chips(model_line: str) -> list[Text]:
    """Parse model status / ladder line into colorful chips."""
    line = (model_line or "").strip()
    if not line:
        return []
    chips: list[Text] = []
    # planner=x, executor=y, default=z, session=…
    for key in ("default", "session", "planner", "executor", "once"):
        m = re.search(rf"(?:^|[\s,]){key}=([^\s,]+)", line, re.I)
        if m:
            chips.append(_chip(key, m.group(1), kind="model"))
    if chips:
        return chips
    # Fallback: whole line as one chip
    short = line.replace("Models (from setup ladders): ", "").replace(
        "Session model: ", ""
    )
    if len(short) > 48:
        short = short[:45] + "…"
    return [_chip("model", short, kind="model")]


def print_banner(
    *,
    resumed: str | None = None,
    approvals: str = "",
    model_line: str = "",
    voice: bool = False,
    console: Console | None = None,
) -> None:
    """Startup / resume banner with gradient brand + status chips."""
    c = console or get_console()
    if resumed:
        subtitle = Text.assemble(
            ("resumed  ", "k.muted"),
            (resumed, "bold #fbbf24"),
        )
    else:
        subtitle = Text("type a request · /help for commands", style="k.muted")

    chips: list[Text] = []
    ap = _approval_chip(approvals)
    if ap:
        chips.append(ap)
    chips.extend(_model_chips(model_line))
    if voice:
        chips.append(_chip("voice", "on", kind="ok"))

    header = Group(
        _brand_word("Kageha"),
        subtitle,
        *([Columns(chips, padding=(0, 1))] if chips else []),
    )
    c.print()
    c.print(
        _panel(
            header,
            console=c,
            border_style="#22d3ee",
            box=box.DOUBLE,
            padding=(0, 2),
            subtitle="[k.muted]agent chat[/]",
            subtitle_align="right",
        )
    )
    c.print()


def print_help(text: str, *, console: Console | None = None) -> None:
    c = console or get_console()
    c.print(
        _panel(
            Markdown(text.strip()),
            console=c,
            title="[bold #22d3ee]✦ help[/]",
            border_style="#38bdf8",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )


def print_sessions(
    rows: list[dict[str, Any]],
    *,
    console: Console | None = None,
) -> None:
    c = console or get_console()
    if not rows:
        print_status("No sessions yet.", console=c)
        return
    table = Table(
        show_header=True,
        header_style="bold #22d3ee",
        box=box.SIMPLE_HEAD,
        border_style="#334155",
        pad_edge=False,
        expand=True,
    )
    table.add_column("id", style="bold #38bdf8", no_wrap=True)
    table.add_column("when", style="dim #94a3b8", no_wrap=True)
    table.add_column("status", justify="center", no_wrap=True)
    # Grow/shrink with the pane instead of a fixed 56-col task column.
    task_max = max(16, int(c.width) - 36)
    table.add_column("task", overflow="ellipsis", max_width=task_max)
    for r in rows:
        status = str(r.get("status", "") or "")
        st_style = "k.ok"
        low = status.lower()
        if any(x in low for x in ("fail", "error", "cancel")):
            st_style = "k.err"
        elif any(x in low for x in ("run", "pend", "wait", "active")):
            st_style = "k.warn"
        table.add_row(
            str(r.get("run_id", "")),
            str(r.get("mtime", "")),
            Text(status, style=st_style),
            str(r.get("task", "")),
        )
    c.print(
        _panel(
            table,
            console=c,
            title="[bold #fbbf24]sessions[/]",
            border_style="#fbbf24",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def print_model_block(text: str, *, console: Console | None = None) -> None:
    c = console or get_console()
    body = (text or "").strip()
    if not body:
        return
    chips = _model_chips(body)
    if "\n" in body:
        # Colorize ★ / • lines lightly
        colored = Text()
        for i, line in enumerate(body.splitlines()):
            if i:
                colored.append("\n")
            if "★" in line:
                colored.append(line, style="bold #f9a8d4")
            elif line.strip().startswith("•") or "•" in line[:3]:
                colored.append(line, style="#34d399")
            elif line.strip().startswith("○"):
                colored.append(line, style="k.muted")
            else:
                colored.append(line, style="#e2e8f0")
        c.print(
            _panel(
                Group(Columns(chips, padding=(0, 1)) if chips else Text(""), colored),
                console=c,
                title="[bold #f472b6]models[/]",
                border_style="#f472b6",
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )
    elif chips:
        c.print(Columns(chips, padding=(0, 1)))
    else:
        c.print(Text(body, style="k.model"))


def print_assistant(text: str, *, console: Console | None = None) -> None:
    """Render assistant reply (Markdown on TTY, plain otherwise)."""
    c = console or get_console()
    body = (text or "").rstrip() or "Done."
    c.print()
    if c.is_terminal and not c.no_color:
        title = Text.assemble(
            ("✦ ", "bold #2dd4bf"),
            ("kageha", "bold #22d3ee"),
        )
        c.print(
            _panel(
                Markdown(body),
                console=c,
                title=title,
                title_align="left",
                border_style="#34d399",
                box=box.HEAVY,
                padding=(1, 2),
            )
        )
    else:
        c.print(f"kageha> {body}")
    c.print()


def ladder_model_summary() -> str:
    """Compact setup/ladder pins when session has no explicit /model pins."""
    try:
        from kageha.config import kageha_home
        import yaml

        path = kageha_home() / "models.yaml"
        if not path.is_file():
            return ""
        data = yaml.safe_load(path.read_text()) or {}
        if not isinstance(data, dict):
            return ""
        pins = data.get("setup_pins") if isinstance(data.get("setup_pins"), dict) else {}
        planner = str(pins.get("planner") or "").strip()
        executor = str(pins.get("executor") or "").strip()
        session = str(
            pins.get("session_default") or data.get("session_default_model") or ""
        ).strip()
        roles = data.get("roles") if isinstance(data.get("roles"), dict) else {}
        if not planner:
            ladder = roles.get("planning") or []
            if isinstance(ladder, list) and ladder:
                planner = str(ladder[0])
        if not executor:
            ladder = roles.get("tool_calling") or roles.get("fast_worker") or []
            if isinstance(ladder, list) and ladder:
                executor = str(ladder[0])
        parts: list[str] = []
        if session:
            parts.append(f"default={session}")
        if planner:
            parts.append(f"planner={planner}")
        if executor and executor != planner:
            parts.append(f"executor={executor}")
        if not parts:
            return ""
        return "Models (from setup ladders): " + ", ".join(parts)
    except Exception:  # noqa: BLE001
        return ""
