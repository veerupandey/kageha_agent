"""Paths, env loading, and runtime config."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def expand_home(raw: str) -> Path:
    return Path(os.path.expanduser(raw)).resolve()


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_env() -> None:
    """Load .env from project root and cwd (idempotent)."""
    for candidate in (project_root() / ".env", Path.cwd() / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)


load_env()


def kageha_home() -> Path:
    home = expand_home(os.environ.get("KAGEHA_HOME", "~/.kageha"))
    home.mkdir(parents=True, exist_ok=True)
    return home


def sessions_dir() -> Path:
    """Durable session workspaces (``~/.kageha/sessions``)."""
    path = kageha_home() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def security_profile(raw: str | None = None) -> str:
    """Execution security profile for sandbox fallback behavior.

    Default ``approval_fallback``: run shell when OS isolation is unavailable;
    risky tools still go through HITL. ``strict`` fails closed unless
    seatbelt/bwrap/docker wraps the command.
    """
    value = (
        raw
        or os.environ.get("KAGEHA_SECURITY_PROFILE")
        or "approval_fallback"
    ).strip().lower()
    if value == "permissive":
        value = "approval_fallback"
    if value not in {"strict", "approval_fallback"}:
        raise ValueError("security profile must be strict or approval_fallback")
    return value


def otlp_endpoint() -> str:
    return (os.environ.get("KAGEHA_OTLP_ENDPOINT") or "").strip()


def bundled_skills_dir() -> Path:
    """Skills shipped inside the installed package (wheel-safe)."""
    return Path(__file__).resolve().parent / "bundled_skills"


def skills_dirs() -> list[Path]:
    """Skill roots in override order (later wins by skill name).

    1. Package ``bundled_skills/`` (core pack ships with the agent)
    2. Repo ``skills/`` only if it is a *distinct* directory (checkout
       usually symlinks ``skills`` → ``bundled_skills``; that is skipped)
    3. ``~/.kageha/skills`` (user; curator-managed)
    4. ``.kageha/skills`` in cwd (project-local)
    5. ``KAGEHA_SKILLS_PATH`` colon-separated extras
    """
    dirs: list[Path] = []
    bundled = bundled_skills_dir()
    if bundled.is_dir():
        dirs.append(bundled)
    # Distinct checkout override only (symlink to bundled is ignored).
    checkout = project_root() / "skills"
    if checkout.is_dir() and checkout.resolve() != bundled.resolve():
        dirs.append(checkout)
    user = kageha_home() / "skills"
    user.mkdir(parents=True, exist_ok=True)
    dirs.append(user)
    project = Path.cwd() / ".kageha" / "skills"
    if project.is_dir():
        dirs.append(project)
    extra = os.environ.get("KAGEHA_SKILLS_PATH", "")
    for part in extra.split(":"):
        part = part.strip()
        if part:
            p = expand_home(part)
            if p.is_dir():
                dirs.append(p)
    return dirs


def tools_dirs() -> list[Path]:
    """User/project custom tool pack directories (later loads override by tool name).

    Each directory may contain ``*.py`` modules or packages that export
    ``register(ctx) -> ToolRegistry``. Also scanned: ``KAGEHA_TOOLS_PATH``.
    """
    dirs: list[Path] = []
    user = kageha_home() / "tools"
    user.mkdir(parents=True, exist_ok=True)
    dirs.append(user)
    project = Path.cwd() / ".kageha" / "tools"
    if project.is_dir():
        dirs.append(project)
    extra = os.environ.get("KAGEHA_TOOLS_PATH", "")
    for part in extra.split(":"):
        part = part.strip()
        if part:
            p = expand_home(part)
            if p.is_dir():
                dirs.append(p)
    return dirs


def kb_root() -> Path:
    d = kageha_home() / "kb"
    d.mkdir(parents=True, exist_ok=True)
    return d


def models_yaml_paths() -> list[Path]:
    paths = [
        project_root() / "models.yaml",
        kageha_home() / "models.yaml",
        Path.cwd() / ".kageha" / "models.yaml",
    ]
    return [p for p in paths if p.is_file()]


def max_steps() -> int:
    return int(os.environ.get("KAGEHA_MAX_STEPS", "80"))


def max_usd() -> float:
    return float(os.environ.get("KAGEHA_MAX_USD", "2.0"))


def max_tool_parallel() -> int:
    """Independent tool calls per step (web_search, subagents, etc.)."""
    return int(os.environ.get("KAGEHA_MAX_TOOL_PARALLEL", "8"))


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def monitor_enabled() -> bool:
    """Stage-gate critic that checks plan drift mid-run."""
    return _truthy("KAGEHA_MONITOR", True)


def checkpoint_enabled() -> bool:
    """Mid-run history compaction + checkpoint files."""
    return _truthy("KAGEHA_CHECKPOINT", True)


def monitor_every() -> int:
    """Run monitor every N loop steps (also on stage/progress events)."""
    return max(1, int(os.environ.get("KAGEHA_MONITOR_EVERY", "3")))


def checkpoint_history_tokens() -> int:
    """Compact history when estimated history tokens exceed this."""
    return max(2000, int(os.environ.get("KAGEHA_CHECKPOINT_HISTORY_TOKENS", "8000")))


def web_search_brave_key() -> str | None:
    """Brave Search API key (``BRAVE_API_KEY`` or ``BRAVE_SEARCH_API_KEY``)."""
    return (
        env_key("BRAVE_API_KEY")
        or (os.environ.get("BRAVE_SEARCH_API_KEY") or "").strip()
        or None
    )


def web_search_tavily_key() -> str | None:
    """Tavily Search API key."""
    return env_key("TAVILY_API_KEY")


def web_search_perplexity_key() -> str | None:
    """Perplexity Search API key."""
    return env_key("PERPLEXITY_API_KEY")


def web_search_keyed_providers() -> list[str]:
    """Keyed search APIs available, in auto-preference order."""
    out: list[str] = []
    if web_search_brave_key():
        out.append("brave")
    if web_search_tavily_key():
        out.append("tavily")
    if web_search_perplexity_key():
        out.append("perplexity")
    return out


def web_search_backend() -> str:
    """Web search backend selection.

    Values: ``auto`` (default) | ``brave`` | ``tavily`` | ``perplexity`` |
    ``gemini`` | ``ddg`` | ``gemini_only``.

    ``auto`` prefers the first keyed search API present
    (Brave → Tavily → Perplexity), else Gemini (+ DDG fallback).
    """
    raw = (os.environ.get("KAGEHA_WEB_SEARCH") or "auto").strip().lower()
    if raw in {"ddg", "duckduckgo"}:
        return "ddg"
    if raw in {"brave", "brave_search", "brave-search"}:
        return "brave"
    if raw in {"tavily"}:
        return "tavily"
    if raw in {"perplexity", "pplx"}:
        return "perplexity"
    if raw in {"gemini_only", "gemini-only", "grounding"}:
        return "gemini_only"
    if raw in {"gemini", "google"}:
        return "gemini"
    # auto (default): first available keyed search API, else Gemini
    keyed = web_search_keyed_providers()
    if keyed:
        return keyed[0]
    return "gemini"


def web_search_gemini_model() -> str:
    """Model id used for Gemini Google Search grounding."""
    return (
        os.environ.get("KAGEHA_WEB_SEARCH_MODEL")
        or os.environ.get("KAGEHA_CAROUSEL_PROMPT_MODEL")
        or "gemini-3.6-flash"
    ).strip()


def tool_guardrails_enabled() -> bool:
    """Hermes/OpenClaw-style tool-call loop warn-then-halt."""
    return _truthy("KAGEHA_TOOL_GUARDRAILS", True)


def post_checkpoint_guard_enabled() -> bool:
    """Abort if identical tool+args+result repeats right after compaction."""
    return _truthy("KAGEHA_POST_CHECKPOINT_GUARD", True)


# Interactive surfaces: Hermes soft-warn default unless hard-stop forced.
_INTERACTIVE_PLATFORMS = frozenset({"cli", "repl", "tui", "desktop", "acp", "interactive"})
_CHANNEL_PLATFORMS = frozenset(
    {
        "cron",
        "mcp",
        "gateway",
    }
)


def normalize_platform(platform: str | None) -> str:
    value = (platform or "cli").strip().lower() or "cli"
    if value in {"chat", "tty", "terminal"}:
        return "cli"
    return value


def tool_guardrails_hard_stop(platform: str | None = None) -> bool:
    """Resolve hard-stop for a platform (Hermes split + kageha chat safety).

    - Explicit ``KAGEHA_TOOL_GUARDRAILS_HARD_STOP`` wins for all platforms.
    - Channels / gateways: hard-stop ON.
    - Interactive CLI: hard-stop ON by default (chat loops); set
      ``KAGEHA_INTERACTIVE_SOFT_GUARD=1`` for Hermes-style warn-only CLI.
    """
    raw = os.environ.get("KAGEHA_TOOL_GUARDRAILS_HARD_STOP")
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    plat = normalize_platform(platform)
    if plat in _CHANNEL_PLATFORMS:
        return True
    if plat in _INTERACTIVE_PLATFORMS:
        if _truthy("KAGEHA_INTERACTIVE_SOFT_GUARD", False):
            return False
        return True
    return True


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(1, int(str(raw).strip()))
    except ValueError:
        return default


def tool_output_limit() -> int:
    """Max chars returned from a tool call before router truncation.

    Override with ``KAGEHA_TOOL_OUTPUT_LIMIT`` (default 12000).
    """
    return _env_int("KAGEHA_TOOL_OUTPUT_LIMIT", 12_000)


def computer_tool_output_limit() -> int:
    """Higher envelope for ``computer_*`` tools (screenshots/AX JSON).

    Override with ``KAGEHA_COMPUTER_TOOL_OUTPUT_LIMIT`` (default 100000).
    """
    return _env_int("KAGEHA_COMPUTER_TOOL_OUTPUT_LIMIT", 100_000)


def read_file_line_limit() -> int:
    """Default max lines for ``read_file`` when ``limit`` is omitted/0.

    Override with ``KAGEHA_READ_FILE_LINE_LIMIT`` (default 400).
    """
    return _env_int("KAGEHA_READ_FILE_LINE_LIMIT", 400)


def list_dir_max_entries() -> int:
    """Cap on paths returned by ``list_dir`` (recursive or shallow).

    Override with ``KAGEHA_LIST_DIR_MAX`` (default 200).
    """
    return _env_int("KAGEHA_LIST_DIR_MAX", 200)


def tool_guardrails_thresholds() -> dict[str, int]:
    """Hermes/OpenClaw thresholds (env-overridable)."""
    return {
        "exact_failure_warn": _env_int("KAGEHA_GUARD_EXACT_FAIL_WARN", 2),
        "exact_failure_block": _env_int("KAGEHA_GUARD_EXACT_FAIL_BLOCK", 4),
        "same_tool_failure_warn": _env_int("KAGEHA_GUARD_SAME_TOOL_WARN", 3),
        "same_tool_failure_halt": _env_int("KAGEHA_GUARD_SAME_TOOL_HALT", 6),
        "no_progress_warn": _env_int("KAGEHA_GUARD_NO_PROGRESS_WARN", 2),
        "no_progress_block": _env_int("KAGEHA_GUARD_NO_PROGRESS_BLOCK", 4),
        "history_size": _env_int("KAGEHA_GUARD_HISTORY_SIZE", 20),
        "ping_pong_warn": _env_int("KAGEHA_GUARD_PINGPONG_WARN", 4),
        "ping_pong_halt": _env_int("KAGEHA_GUARD_PINGPONG_HALT", 6),
        "global_breaker_warn": _env_int("KAGEHA_GUARD_GLOBAL_WARN", 8),
        "global_breaker_halt": _env_int("KAGEHA_GUARD_GLOBAL_HALT", 12),
        "unknown_tool_warn": _env_int("KAGEHA_GUARD_UNKNOWN_TOOL_WARN", 2),
        "unknown_tool_block": _env_int("KAGEHA_GUARD_UNKNOWN_TOOL_BLOCK", 4),
        "stagnant_tools_warn": _env_int("KAGEHA_GUARD_STAGNANT_TOOLS_WARN", 6),
        "stagnant_tools_halt": _env_int("KAGEHA_GUARD_STAGNANT_TOOLS_HALT", 10),
    }


def sandbox_profile() -> str:
    """Shell isolation: off | seatbelt | bwrap | docker | ssh | modal.

    Default ``auto``: seatbelt (macOS) → bwrap (Linux) → off.
    SSH/Modal are never auto-selected (require explicit ``KAGEHA_SANDBOX``).
    """
    raw = (os.environ.get("KAGEHA_SANDBOX") or "").strip().lower()
    if raw in {"off", "0", "false", "none", "cwd"}:
        return "off"
    if raw in {"seatbelt", "sandbox-exec", "macos"}:
        return "seatbelt"
    if raw in {"bwrap", "bubblewrap"}:
        return "bwrap"
    if raw in {"docker", "container"}:
        return "docker"
    if raw in {"ssh", "remote"}:
        return "ssh"
    if raw in {"modal", "serverless"}:
        return "modal"
    # Auto: strongest available local jail.
    if raw in {"", "auto"}:
        import shutil

        if shutil.which("sandbox-exec"):
            return "seatbelt"
        if shutil.which("bwrap"):
            return "bwrap"
        return "off"
    return raw


_SANDBOX_CLI_VALUES = frozenset(
    {"auto", "off", "seatbelt", "bwrap", "docker", "ssh", "modal"}
)


def apply_sandbox_cli(raw: str | None) -> str:
    """Apply ``--sandbox`` for this process; returns the resolved profile.

    Sets ``KAGEHA_SANDBOX`` when ``raw`` is non-empty so shell wraps use it.
    Empty/None leaves the existing env / auto selection unchanged.
    """
    text = (raw or "").strip().lower()
    if not text:
        return sandbox_profile()
    aliases = {
        "container": "docker",
        "none": "off",
        "cwd": "off",
        "0": "off",
        "sandbox-exec": "seatbelt",
        "macos": "seatbelt",
        "bubblewrap": "bwrap",
        "remote": "ssh",
        "serverless": "modal",
    }
    value = aliases.get(text, text)
    if value not in _SANDBOX_CLI_VALUES:
        raise ValueError(
            "sandbox must be auto|off|seatbelt|bwrap|docker|ssh|modal "
            f"(got {raw!r})"
        )
    os.environ["KAGEHA_SANDBOX"] = value
    return sandbox_profile()


def tools_policy_paths() -> list[Path]:
    """Candidate tools.yaml locations (home then project)."""
    return [
        kageha_home() / "tools.yaml",
        Path.cwd() / ".kageha" / "tools.yaml",
        project_root() / "tools.yaml",
    ]


def env_key(name: str) -> str | None:
    v = os.environ.get(name, "").strip()
    return v or None


def env_file_candidates() -> list[Path]:
    """Prefer project .env, then cwd .env."""
    paths = [project_root() / ".env", Path.cwd() / ".env"]
    seen: set[Path] = set()
    out: list[Path] = []
    for p in paths:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def resolve_env_file() -> Path:
    """Return an existing .env path, or create project-root ``.env``."""
    for p in env_file_candidates():
        if p.is_file():
            return p
    target = project_root() / ".env"
    if not target.is_file():
        target.write_text("# Kageha env\n", encoding="utf-8")
    return target


def read_env_value(key: str, path: Path | None = None) -> str:
    """Read KEY from a dotenv file (does not require process env)."""
    path = path or resolve_env_file()
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return ""


def upsert_env_key(key: str, value: str, path: Path | None = None) -> Path:
    """Set KEY=value in .env (replace existing line or append)."""
    import re

    path = path or resolve_env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    lines = text.splitlines()
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    replaced = False
    new_lines: list[str] = []
    for line in lines:
        if pattern.match(line):
            new_lines.append(f"{key}={value}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(f"{key}={value}")
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    os.environ[key] = value
    return path
