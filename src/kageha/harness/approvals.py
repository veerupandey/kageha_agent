"""HITL approval policy — deny-by-default classes for risky actions."""

from __future__ import annotations

import asyncio
import contextvars
import os
import re
import select
import shlex
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from kageha.config import kageha_home

# Channel adapters (WhatsApp/Telegram) set this so ask_human / fallbacks use chat.
_channel_asker: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "kageha_channel_asker", default=None
)


def set_channel_asker(asker: Any) -> contextvars.Token:
    """asker: async (prompt: str) -> str | None"""
    return _channel_asker.set(asker)


def reset_channel_asker(token: contextvars.Token) -> None:
    _channel_asker.reset(token)


class ApprovalDecision(str, Enum):
    AUTO = "auto"
    ASK = "ask"
    DENY = "deny"


@dataclass
class ApprovalOutcome:
    """Result of an interactive approval prompt.

    ``feedback`` is set when the human Suggests (deny + steering) instead of
    a bare Approve/Deny — HITL with Suggest.

    ``scope`` (Codex-style):
      - ``once`` — approve this request only
      - ``session`` — auto-approve risky tools for the rest of this chat
      - ``full`` — session + sandbox network (all permissions this process)
    """

    approved: bool
    feedback: str = ""
    scope: str = "once"  # once | session | full

    def __bool__(self) -> bool:
        return self.approved


@dataclass
class ApprovalRequest:
    action: str
    detail: str
    risk_class: str
    default: ApprovalDecision = ApprovalDecision.ASK


Approver = Callable[
    [ApprovalRequest],
    Awaitable[bool | ApprovalOutcome] | bool | ApprovalOutcome,
]


def normalize_approval_result(result: Any) -> ApprovalOutcome:
    if isinstance(result, ApprovalOutcome):
        scope = str(result.scope or "once").strip().lower() or "once"
        if scope not in {"once", "session", "full", "always"}:
            scope = "once"
        if scope == "always":
            scope = "full"
        return ApprovalOutcome(
            approved=bool(result.approved),
            feedback=str(result.feedback or "").strip(),
            scope=scope,
        )
    if isinstance(result, dict):
        scope = str(result.get("scope") or "once").strip().lower() or "once"
        if scope == "always":
            scope = "full"
        if scope not in {"once", "session", "full"}:
            scope = "once"
        return ApprovalOutcome(
            approved=bool(result.get("approved", False)),
            feedback=str(result.get("feedback") or "").strip(),
            scope=scope,
        )
    return ApprovalOutcome(approved=bool(result))


# Process-wide grants so Session/Full stick across turns (gate is recreated).
_PROCESS_PERMISSIONS: dict[str, Any] = {
    "auto_approve": False,
    "sandbox_network": False,
    "scope": "ask",
}


def process_permissions() -> dict[str, Any]:
    """Current Once/Session/Full grants for this process."""
    net = os.environ.get("KAGEHA_SANDBOX_ALLOW_NETWORK", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return {
        "auto_approve": bool(_PROCESS_PERMISSIONS.get("auto_approve")),
        "sandbox_network": bool(_PROCESS_PERMISSIONS.get("sandbox_network") or net),
        "scope": str(_PROCESS_PERMISSIONS.get("scope") or "ask"),
    }


def apply_permission_scope(scope: str) -> dict[str, Any]:
    """Apply session/full grants. Returns a status dict for the UI."""
    scope = (scope or "once").strip().lower()
    if scope == "always":
        scope = "full"
    if scope == "session":
        _PROCESS_PERMISSIONS["auto_approve"] = True
        _PROCESS_PERMISSIONS["scope"] = "session"
        return {
            "scope": "session",
            "auto_approve": True,
            "sandbox_network": False,
            "message": "Session grant: auto-approve risky tools for this chat.",
        }
    if scope == "full":
        os.environ["KAGEHA_SANDBOX_ALLOW_NETWORK"] = "1"
        _PROCESS_PERMISSIONS["auto_approve"] = True
        _PROCESS_PERMISSIONS["sandbox_network"] = True
        _PROCESS_PERMISSIONS["scope"] = "full"
        return {
            "scope": "full",
            "auto_approve": True,
            "sandbox_network": True,
            "message": (
                "Full access: auto-approve + sandbox network for this process. "
                "Elevated host-escape still asks once unless you approve it."
            ),
        }
    return {"scope": "once", "auto_approve": False, "sandbox_network": False, "message": ""}



_NET_SHELL = re.compile(
    r"\b("
    r"curl|wget|fetch|"
    r"pip3?\s+install|(?:python3?|py)\s+-m\s+pip\s+install|"
    r"uv\s+(?:add|lock|sync|pip|tool\s+install|python\s+install)|"
    r"npm\s+install|yarn\s+add|pnpm\s+add|"
    r"apt(?:-get)?|brew|sudo"
    r")\b",
    re.I,
)
_DESTRUCTIVE = re.compile(
    r"(^|\s)(rm|rmdir|shred|mkfs|fdisk|diskutil\s+erase|dd\s+if=|"
    r"git\s+reset\s+--hard|git\s+clean\s+-|:(){:|:&};:)\b",
    re.I,
)


def shell_segments(command: str) -> list[str]:
    """Split shell control-flow segments without executing expansions."""
    try:
        lexer = shlex.shlex(
            command,
            posix=True,
            punctuation_chars="|&;()\n",
        )
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return [command]
    separators = {"|", "||", "&&", ";", "(", ")", "\n", "&"}
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in separators or all(char in "|&;()\n" for char in token):
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append(token)
    return [" ".join(segment) for segment in segments if segment]


def _allowlist_path() -> Path:
    return kageha_home() / "approvals_allowlist.json"


def _load_allowlist() -> set[str]:
    path = _allowlist_path()
    if not path.is_file():
        return set()
    try:
        import json

        data = json.loads(path.read_text())
        if isinstance(data, list):
            return {str(x) for x in data}
        if isinstance(data, dict):
            return {str(x) for x in (data.get("entries") or [])}
    except Exception:  # noqa: BLE001
        return set()
    return set()


def _save_allowlist(entries: set[str]) -> None:
    import json

    path = _allowlist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": sorted(entries)}, indent=2) + "\n")


def _allowlist_key(req: ApprovalRequest) -> str:
    # Stable key for repeated identical shell / tool actions.
    detail = (req.detail or "").strip()
    if len(detail) > 500:
        detail = detail[:500]
    return f"{req.action}|{req.risk_class}|{detail}"


class ApprovalGate:
    def __init__(
        self,
        approver: Approver | None = None,
        *,
        auto_approve: bool = False,
        audit: Callable[[ApprovalRequest, str], None] | None = None,
        on_permission_grant: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.approver = approver
        # Inherit Session/Full grants from earlier turns in this process.
        perms = process_permissions()
        self.auto_approve = bool(auto_approve or perms.get("auto_approve"))
        self.audit = audit
        self.on_permission_grant = on_permission_grant
        self.log: list[ApprovalRequest] = []
        self._lock = asyncio.Lock()
        self._allowlist = _load_allowlist()
        self.last_feedback: str = ""
        self.last_scope: str = str(perms.get("scope") or "once")

    def denial_message(self, what: str) -> str:
        """User-facing denial; includes Suggest steering when present."""
        fb = (self.last_feedback or "").strip()
        if fb:
            return (
                f"DENIED: {what} not approved. "
                f"User suggestion: {fb}. Adjust and retry (do not repeat the same action)."
            )
        return f"DENIED: {what} not approved"

    def _apply_outcome_scope(self, outcome: ApprovalOutcome) -> None:
        self.last_scope = outcome.scope or "once"
        if not outcome.approved or self.last_scope == "once":
            return
        grant = apply_permission_scope(self.last_scope)
        if grant.get("auto_approve"):
            self.auto_approve = True
        if self.on_permission_grant is not None:
            try:
                self.on_permission_grant(grant)
            except Exception:  # noqa: BLE001
                pass

    def classify_shell(self, command: str) -> ApprovalDecision:
        for segment in shell_segments(command):
            if _DESTRUCTIVE.search(segment) or re.search(r"\bsudo\b", segment):
                return ApprovalDecision.ASK
            if _NET_SHELL.search(segment):
                return ApprovalDecision.ASK
        return ApprovalDecision.AUTO

    def classify_forge(self, code: str, description: str = "") -> ApprovalDecision:
        blob = f"{code}\n{description}".lower()
        risky = (
            "requests.",
            "httpx.",
            "urllib",
            "socket.",
            "subprocess",
            "playwright",
            "selenium",
            "os.system",
            "telegram",
            "whatsapp",
        )
        if any(x in blob for x in risky):
            return ApprovalDecision.ASK
        return ApprovalDecision.AUTO

    async def require(self, req: ApprovalRequest) -> bool:
        """Serialize human prompts so parallel tools cannot interleave HITL."""
        async with self._lock:
            self.log.append(req)
            self.last_feedback = ""
            if self.auto_approve or req.default == ApprovalDecision.AUTO:
                if self.audit is not None:
                    self.audit(req, "approved_auto")
                return True
            if req.default == ApprovalDecision.DENY:
                if self.audit is not None:
                    self.audit(req, "denied_policy")
                return False
            key = _allowlist_key(req)
            if key in self._allowlist:
                if self.audit is not None:
                    self.audit(req, "approved_allowlist")
                return True
            outcome = await self._ask(req, persist_allowlist=True)
            return bool(outcome)

    async def require_explicit(self, req: ApprovalRequest) -> ApprovalOutcome:
        """Always ask a human — ignores ``auto_approve`` and allowlist.

        Shared bus for Plan Build, ``request_approval`` tool, and any mode
        gate that must surface Approve/Deny/Suggest even when tool
        auto-approve is on. Audit ``pending`` stamps ``approval_id`` so
        WebUI SSE / CLI can render controls before the approver blocks.
        """
        async with self._lock:
            self.log.append(req)
            self.last_feedback = ""
            if req.default == ApprovalDecision.DENY:
                if self.audit is not None:
                    self.audit(req, "denied_policy")
                return ApprovalOutcome(False)
            return await self._ask(req, persist_allowlist=False)

    async def _ask(
        self, req: ApprovalRequest, *, persist_allowlist: bool
    ) -> ApprovalOutcome:
        if self.approver is None:
            if self.audit is not None:
                self.audit(req, "denied_no_approver")
            return ApprovalOutcome(False)
        if self.audit is not None:
            # Emits approval_required + approval_id (runtime approval_audit).
            self.audit(req, "pending")
        result = self.approver(req)
        if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
            raw = await result  # type: ignore[arg-type]
        else:
            raw = result
        outcome = normalize_approval_result(raw)
        self.last_feedback = outcome.feedback
        self._apply_outcome_scope(outcome)
        if self.audit is not None:
            if outcome.approved:
                label = "approved"
                if outcome.scope in {"session", "full"}:
                    label = f"approved_{outcome.scope}"
                self.audit(req, label)
            elif outcome.feedback:
                self.audit(req, "suggested")
            else:
                self.audit(req, "denied")
        if (
            outcome.approved
            and persist_allowlist
            and outcome.scope == "once"
            and (
                req.action in {"bash", "shell"} or req.action.startswith("tool:")
            )
        ):
            # Persist approved shell / tool patterns for the rest of the process
            # and across sessions (OpenClaw-style once-approved).
            key = _allowlist_key(req)
            self._allowlist.add(key)
            try:
                _save_allowlist(self._allowlist)
            except Exception:  # noqa: BLE001
                pass
        return outcome


def _hitl_dir() -> Path:
    d = kageha_home() / "hitl"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_pending(pending: Path, prompt_lines: list[str], answer_path: Path) -> None:
    body = [
        "# Kageha — waiting for your answer",
        "",
        *prompt_lines,
        "",
        "## How to answer (pick one)",
        "",
        "1. **Primary:** type in the Terminal that launched `kageha` at the `Your answer>` prompt, then Enter.",
        f"2. **Backup:** write a one-line answer to `{answer_path}`",
        "   e.g. `echo y > ~/.kageha/hitl/ANSWER.txt`",
        "",
    ]
    pending.write_text("\n".join(body) + "\n", encoding="utf-8")


def _emit_prompt(prompt_lines: list[str], answer_path: Path, *, tty_path: str | None) -> None:
    """Show prompt on controlling tty (primary); fall back to stdout if tty unavailable."""
    del answer_path  # The backup path remains in PENDING.md and fallback output.
    text = "\n\n" + "\n".join(prompt_lines) + "\nYour answer> "
    try:
        sys.stderr.flush()
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass
    wrote_tty = False
    if tty_path:
        try:
            fd = os.open(tty_path, os.O_WRONLY)
            try:
                os.write(fd, text.encode("utf-8", errors="replace"))
                try:
                    os.fsync(fd)
                except OSError:
                    pass
                wrote_tty = True
            finally:
                os.close(fd)
        except OSError:
            wrote_tty = False
    if wrote_tty:
        return
    else:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass


def _drain_tty(fd: int) -> None:
    """Discard buffered keystrokes so a prior Enter doesn't fake-answer the next prompt."""
    try:
        import fcntl

        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        try:
            while True:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
        except BlockingIOError:
            pass
        finally:
            fcntl.fcntl(fd, fcntl.F_SETFL, flags)
    except Exception:  # noqa: BLE001
        # Best-effort; select drain fallback
        try:
            while True:
                ready, _, _ = select.select([fd], [], [], 0)
                if not ready:
                    break
                if not os.read(fd, 4096):
                    break
        except Exception:  # noqa: BLE001
            pass


def race_tty_and_file(
    prompt_lines: list[str],
    *,
    timeout: float = 600.0,
    tty_path: str | None = "/dev/tty",
    answer_path: Path | None = None,
    pending_path: Path | None = None,
    poll_interval: float = 0.2,
    external_stop: threading.Event | None = None,
) -> str:
    """
    Wait for the first of:
      - a line typed on the controlling Terminal (tty_path, default /dev/tty)
      - a non-empty answer file (~/.kageha/hitl/ANSWER.txt)

    Clears any stale ANSWER.txt before waiting. Safe to call from a worker thread.
    """
    hd = _hitl_dir()
    answer = Path(answer_path) if answer_path is not None else hd / "ANSWER.txt"
    pending = Path(pending_path) if pending_path is not None else hd / "PENDING.md"
    answer.parent.mkdir(parents=True, exist_ok=True)
    pending.parent.mkdir(parents=True, exist_ok=True)

    # Never honor a stale answer from a previous prompt
    if answer.exists():
        try:
            answer.unlink()
        except OSError:
            pass

    _write_pending(pending, prompt_lines, answer)

    result: dict[str, str] = {}
    stop = threading.Event()

    def _stopped() -> bool:
        return stop.is_set() or (external_stop is not None and external_stop.is_set())

    # Create empty stub so the path exists for `echo … > ANSWER.txt` and editors
    answer.write_text("", encoding="utf-8")
    # Bump mtime baseline AFTER truncate so a concurrent writer still wins if newer
    time.sleep(0.02)
    start_mtime = answer.stat().st_mtime_ns

    def read_tty() -> None:
        if not tty_path:
            return
        try:
            fd = os.open(tty_path, os.O_RDONLY)
        except OSError as e:
            try:
                sys.stdout.write(f"[HITL] {tty_path} unavailable ({e}); use ANSWER.txt\n")
                sys.stdout.flush()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            _drain_tty(fd)
            # Emit prompt only after drain so we don't eat the next answer.
            _emit_prompt(prompt_lines, answer, tty_path=tty_path)

            # Line-oriented read is more reliable than select+chunk across prompts
            with os.fdopen(fd, "r", buffering=1, closefd=False) as tty:
                while not _stopped():
                    # Use select so timeout/stop can wake us
                    ready, _, _ = select.select([fd], [], [], poll_interval)
                    if not ready:
                        continue
                    line = tty.readline()
                    if not line:
                        if _stopped():
                            return
                        time.sleep(poll_interval)
                        continue
                    ans = line.strip()
                    if ans:
                        result["answer"] = ans
                        result["source"] = "tty"
                        stop.set()
                        return
        except OSError:
            return
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def read_file() -> None:
        while not _stopped():
            try:
                st = answer.stat()
                text = answer.read_text(encoding="utf-8").strip()
                if text and st.st_mtime_ns >= start_mtime:
                    result["answer"] = text.splitlines()[0].strip()
                    result["source"] = "file"
                    stop.set()
                    return
            except OSError:
                pass
            stop.wait(poll_interval)

    # If no tty, still show prompt on stdout
    if not tty_path:
        _emit_prompt(prompt_lines, answer, tty_path=None)
        print(f"(You can also answer in {answer})", flush=True)

    t1 = threading.Thread(target=read_tty, name="hitl-tty", daemon=True)
    t2 = threading.Thread(target=read_file, name="hitl-file", daemon=True)
    t1.start()
    t2.start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if stop.is_set() or (external_stop is not None and external_stop.is_set()):
            break
        stop.wait(min(poll_interval, max(0.01, deadline - time.time())))
    stop.set()
    # Brief join so file/tty threads can finish writing result
    t1.join(timeout=1.0)
    t2.join(timeout=0.5)

    ans = result.get("answer", "")
    src = result.get("source", "timeout")
    if ans:
        try:
            pending.write_text(
                f"# HITL answered\n\nsource: {src}\nanswer: {ans}\n",
                encoding="utf-8",
            )
        except OSError:
            pass
    return ans


async def cli_approver(req: ApprovalRequest) -> ApprovalOutcome:
    detail = (req.detail or "")[:500]
    is_plan = (req.risk_class or "") == "plan" or req.action == "approve_plan"
    if is_plan:
        lines = [
            "Build/approve this plan?",
            f"  {detail}",
            "[Y] Yes    [N] No    [!] Suggest — type: ! <feedback>",
        ]
    else:
        lines = [
            f"Allow {req.action}?  ({req.risk_class})",
            f"  {detail}",
            "[1] Once     — this action only",
            "[2] Session  — auto-approve risky tools for this chat",
            "[3] Full     — all permissions this process (auto + sandbox network)",
            "[N] Deny     [!] Suggest — type: ! <feedback>",
        ]

    def _ask() -> ApprovalOutcome:
        from kageha.chat.interrupt import hitl_stop_event

        ans = race_tty_and_file(
            lines,
            external_stop=hitl_stop_event(),
        ).strip()
        if hitl_stop_event().is_set() and not ans:
            return ApprovalOutcome(False, feedback="Cancelled by user.")
        low = ans.lower()
        # Suggest (Codex-style freeform steer)
        if low in {"!", "suggest"} or low.startswith("! ") or low.startswith("suggest"):
            if low in {"!", "suggest"}:
                fb = "Please revise with clearer steps and constraints."
            elif low.startswith("!"):
                fb = ans[1:].strip()
            else:
                fb = (
                    ans.split(":", 1)[1].strip()
                    if ":" in low
                    else ans.split(None, 1)[1].strip()
                )
            return ApprovalOutcome(False, feedback=fb or "Please revise.")
        if is_plan:
            if low in {"y", "yes", "approve", "ok", "build", "1"}:
                return ApprovalOutcome(True, scope="once")
            return ApprovalOutcome(False)
        # Codex-like 3-way grant
        if low in {"1", "y", "yes", "approve", "ok", "once"}:
            return ApprovalOutcome(True, scope="once")
        if low in {"2", "session", "s"}:
            return ApprovalOutcome(True, scope="session")
        if low in {"3", "full", "always", "a", "all"}:
            return ApprovalOutcome(True, scope="full")
        return ApprovalOutcome(False)

    return await asyncio.to_thread(_ask)


def _human_question_lines(
    question: str,
    *,
    yes_label: str = "",
    no_label: str = "",
) -> tuple[list[str], bool]:
    """Create a compact terminal question and indicate whether it is binary."""
    q = question.strip() or "Continue?"
    binary = bool(yes_label or no_label) or bool(
        re.search(r"\b(would you like|do you want|should i|shall i|may i)\b", q, re.I)
    )
    lines = [q]
    if binary:
        yes = yes_label.strip() or "Yes"
        no = no_label.strip() or "No"
        lines.append(f"[Y] {yes}    [N] {no}")
    else:
        lines.append("Type your answer and press Enter.")
    return lines, binary


async def cli_ask_human(
    question: str,
    *,
    yes_label: str = "",
    no_label: str = "",
    dest_hint: str = "",
) -> str:
    lines, binary = _human_question_lines(
        question,
        yes_label=yes_label,
        no_label=no_label,
    )
    if dest_hint:
        lines.append(f"(also saves to {dest_hint})")

    asker = _channel_asker.get()
    if asker is not None:
        prompt = "\n".join(lines)
        result = asker(prompt)
        if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
            text = await result  # type: ignore[misc]
        else:
            text = result
        answer = str(text or "").strip()
    else:
        def _ask() -> str:
            from kageha.chat.interrupt import hitl_stop_event

            return race_tty_and_file(
                lines,
                external_stop=hitl_stop_event(),
            ).strip()

        answer = await asyncio.to_thread(_ask)
        from kageha.chat.interrupt import hitl_stop_event

        if hitl_stop_event().is_set() and not answer:
            return "no" if binary else ""
    if binary:
        low = answer.lower()
        if low in {"y", "yes", "approve", "ok"}:
            return "yes"
        if low in {"n", "no", "deny"}:
            return "no"
    return answer
