"""Turn manager — micro-paths or one self-depth agent turn.

Design (Codex/Claude-style):
  - Remotes / cancel / where / status → zero-LLM micro-paths
  - Everything else → agent with tools in act (followup) mode
  - The acting model decides 0 / 1 / N tool steps and stops when done
  - Full plan→verify only for ``/plan`` (or escalate_plan flag)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from kageha.chat.quick import is_where_question
from kageha.harness.sandbox import SessionWorkspace
from kageha.loop.resume_text import unwrap_objective
from kageha.obs.events import EventLog

Intent = Literal[
    "new_task",
    "continue_task",
    "modify_artifact",
    "status",
    "cancel",
    "micro_action",
]

RouteKind = Literal[
    "quick_where",
    "quick_status",
    "quick_remote",
    "resume",
    "new_run",
    "cancel",
    "first_run",
]

INTENTS: frozenset[str] = frozenset(
    {
        "new_task",
        "continue_task",
        "modify_artifact",
        "status",
        "cancel",
        "micro_action",
    }
)

_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "of",
        "in",
        "on",
        "for",
        "with",
        "is",
        "it",
        "this",
        "that",
        "these",
        "those",
        "me",
        "my",
        "your",
        "you",
        "we",
        "our",
        "please",
        "just",
        "about",
        "from",
        "into",
        "can",
        "could",
        "would",
        "should",
        "will",
        "want",
        "need",
        "like",
        "also",
        "more",
        "some",
        "any",
        "all",
        "how",
        "what",
        "when",
        "where",
        "why",
        "who",
        "which",
        "make",
        "create",
        "build",
        "write",
        "add",
        "change",
        "update",
        "fix",
        "edit",
        "help",
        "task",
        "new",
        "old",
        "now",
        "then",
        "than",
        "too",
        "very",
        "really",
        "using",
        "use",
        "get",
        "got",
        "do",
        "did",
        "does",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "not",
        "no",
        "yes",
        "ok",
        "okay",
    }
)

_CANCEL_RE = re.compile(
    r"^(cancel|stop|abort|never\s*mind|forget\s+it|quit\s+this|"
    r"don't\s+bother|do\s+not\s+bother|halt|scratch\s+that)\b",
    re.I,
)

_EXPLICIT_NEW_RE = re.compile(
    r"\b("
    r"new\s+task|start\s+over|start\s+fresh|different\s+topic|"
    r"unrelated\s+task|switch\s+(topics?|tasks?)|forget\s+(the\s+)?"
    r"(previous|prior|old)\b|ignore\s+(the\s+)?(previous|prior)|"
    r"scrap\s+(the\s+)?(plan|previous)|from\s+scratch"
    r")\b",
    re.I,
)

_STATUS_RE = re.compile(
    r"^(status|progress|how's\s+it\s+going|hows\s+it\s+going|"
    r"where\s+are\s+we|what's\s+the\s+status|whats\s+the\s+status|"
    r"any\s+update|current\s+status)\b",
    re.I,
)

_CONTINUE_RE = re.compile(
    r"\b("
    r"continue|keep\s+going|resume|carry\s+on|go\s+on|"
    r"make\s+it\s+(shorter|longer|better|nicer|cleaner|darker|brighter)|"
    r"finish\s+(it|up|this)|same\s+(task|session|plan)|"
    r"tweak\s+it|polish\s+it|improve\s+it"
    r")\b",
    re.I,
)

_MODIFY_RE = re.compile(
    r"\b("
    r"add\s+(a\s+)?(slide|page|section|image|photo|clip|scene)|"
    r"change\s+(the\s+)?(color|colour|title|font|background|layout)|"
    r"make\s+the\s+\w+\s+(image|photo|slide|video|deck)\s+\w+|"
    r"(darker|brighter|smaller|larger|bigger|shorter|longer)|"
    r"remove\s+(the\s+)?(slide|image|page|section)|"
    r"edit\s+(the\s+)?(slide|image|deck|video|file|ppt)|"
    r"update\s+(the\s+)?(slide|image|deck|presentation)"
    r")\b",
    re.I,
)

_ARTIFACT_HINT_RE = re.compile(
    r"\b("
    r"slide|slides|deck|pptx|presentation|image|photo|png|jpg|jpeg|"
    r"video|mp4|pdf|diagram|chart|report|doc|document|html|"
    r"artifact|output|file|files"
    r")\b",
    re.I,
)

_TASKISH_RE = re.compile(
    r"\b("
    r"teach|learn|explain|create|make|build|generate|write|research|"
    r"implement|design|plan|prepare|draft|produce|record|film|"
    r"download|install|deploy|analyze|summarise|summarize"
    r")\b",
    re.I,
)

_ARTIFACT_FEEDBACK_RE = re.compile(
    r"\b(boring|plain|generic|bland|ugly|amateur|unprofessional|"
    r"not professional|too simple|too basic|don'?t like|do not like)\b",
    re.I,
)

# "make it polished" / typo "make itb polished" → revise current artifacts.
_POLISH_RE = re.compile(
    r"\b(polish\w*|prettier|more\s+(beautiful|stunning|polished)|"
    r"make\s+it\w*\s+polish\w*)\b",
    re.I,
)

_RETRY_RE = re.compile(
    r"^(try|try\s+(again|it|that|once)|do\s+it|do\s+that|go\s+ahead|"
    r"please\s+try|yes\s+try|just\s+try|retry|again|"
    r"go\s+for\s+it|please\s+do|yes\s+please|yep|yeah)\s*[.!]?\s*$",
    re.I,
)

_BARE_BROWSER_RE = re.compile(
    r"^(can\s+you\s+|could\s+you\s+|please\s+|just\s+)?("
    r"open|launch|start|use"
    r")(\s+the)?\s+browser\s*\??$",
    re.I,
)

_URLISH_RE = re.compile(r"(https?://|\bwww\.|\.[a-z]{2,}/)", re.I)

# "open comet and browse to kageha.ca" / "browse to https://…"
_COMET_OR_BROWSE_RE = re.compile(
    r"\b("
    r"open\s+(the\s+)?(comet|chrome|browser)|"
    r"launch\s+(the\s+)?(comet|chrome|browser)|"
    r"browse\s+to\b|"
    r"go\s+to\s+(https?://|www\.|[a-z0-9.-]+\.[a-z]{2,})|"
    r"visit\s+(https?://|www\.|[a-z0-9.-]+\.[a-z]{2,})|"
    r"browser_connect|browser_open"
    r")\b",
    re.I,
)

_HOST_RE = re.compile(
    r"\b("
    r"https?://[^\s<>\"']+|"
    r"www\.[a-z0-9.-]+\.[a-z]{2,}|"
    r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+"
    r")\b",
    re.I,
)

_PLAN_CMD_RE = re.compile(r"^/(plan|spec|goal)\b", re.I)

ESCALATE_PLAN_FLAG = "escalate_plan.flag"


@dataclass
class TurnDecision:
    intent: Intent
    related_to_current_task: bool = False
    requires_tools: bool = True
    reuse_artifacts: list[str] = field(default_factory=list)
    discard_old_plan: bool = False
    reason: str = ""
    source: str = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "related_to_current_task": bool(self.related_to_current_task),
            "requires_tools": bool(self.requires_tools),
            "reuse_artifacts": list(self.reuse_artifacts),
            "discard_old_plan": bool(self.discard_old_plan),
            "reason": self.reason,
            "source": self.source,
        }


@dataclass
class TurnContext:
    """Lightweight session snapshot for classification."""

    run_id: str | None = None
    objective: str = ""
    artifacts: list[str] = field(default_factory=list)
    plan_summary: str = ""
    recent_user_messages: list[str] = field(default_factory=list)
    recent_artifacts: list[str] = field(default_factory=list)
    pending_question: str = ""
    pending_yes_label: str = ""
    pending_no_label: str = ""
    pending_request: str = ""

    @property
    def has_session(self) -> bool:
        return bool(self.run_id)

    @property
    def has_artifacts(self) -> bool:
        return bool(self.artifacts)


def _recent_user_messages(workspace: SessionWorkspace, *, limit: int = 6) -> list[str]:
    path = workspace.root / "chat.jsonl"
    if not path.is_file():
        return []
    out: list[str] = []
    try:
        for line in path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("role") != "user":
                continue
            text = str(rec.get("text") or "").strip()
            if text and not text.startswith("/"):
                out.append(text)
    except OSError:
        return []
    return out[-limit:]


def _recent_referenced_artifacts(workspace: SessionWorkspace) -> list[str]:
    path = workspace.root / "chat.jsonl"
    if not path.is_file():
        return []
    records: list[dict[str, str]] = []
    try:
        for line in path.read_text(errors="replace").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = str(item.get("role") or "")
            text = str(item.get("text") or "")
            if role in {"user", "assistant"} and text:
                records.append({"role": role, "text": text})
    except OSError:
        return []
    artifact_re = re.compile(
        r"(?<![\w.-])((?:artifacts|outputs|slides|carousel|diagrams)/"
        r"[A-Za-z0-9_./-]+\.(?:png|jpe?g|webp|gif|pptx|pdf|html|md|mp4))",
        re.I,
    )
    for index in range(len(records) - 1, -1, -1):
        item = records[index]
        if item["role"] != "user":
            continue
        low = item["text"].lower()
        if not (
            _ARTIFACT_FEEDBACK_RE.search(low)
            or re.search(r"\b(this|these|those|it|them)\b", low)
        ):
            continue
        for prior in reversed(records[max(0, index - 3) : index]):
            if prior["role"] != "assistant":
                continue
            matches = artifact_re.findall(prior["text"])
            if matches:
                return list(dict.fromkeys(matches))[:20]
    return []


def build_turn_context(workspace: SessionWorkspace | None) -> TurnContext:
    if workspace is None:
        return TurnContext()
    objective = ""
    plan_summary = ""
    pending_question = ""
    pending_yes_label = ""
    pending_no_label = ""
    pending_request = ""
    goal_path = workspace.root / "goal_card.json"
    state_path = workspace.root / "task_state.json"
    plan_path = workspace.root / "plan.json"
    if state_path.is_file():
        try:
            data = json.loads(state_path.read_text())
            objective = unwrap_objective(str(data.get("objective") or ""))
            pending_question = str(data.get("pending_question") or "")
            pending_yes_label = str(data.get("pending_yes_label") or "")
            pending_no_label = str(data.get("pending_no_label") or "")
            pending_request = str(data.get("pending_request") or "")
        except Exception:  # noqa: BLE001
            pass
    if not objective and goal_path.is_file():
        try:
            data = json.loads(goal_path.read_text())
            objective = unwrap_objective(str(data.get("task") or ""))
        except Exception:  # noqa: BLE001
            pass
    if plan_path.is_file():
        try:
            pdata = json.loads(plan_path.read_text())
            plan_summary = str(pdata.get("summary") or "")[:400]
        except Exception:  # noqa: BLE001
            pass
    arts: list[str] = []
    try:
        arts = [
            f
            for f in workspace.list_files()
            if not f.startswith(("events", "chat.", "result.", "goal_", "plan.", "task_"))
        ][:40]
    except Exception:  # noqa: BLE001
        arts = []
    recent_artifacts: list[str] = _recent_referenced_artifacts(workspace)
    turns_dir = workspace.root / "_turns"
    if not recent_artifacts and turns_dir.is_dir():
        try:
            records = sorted(
                (p for p in turns_dir.glob("*.json") if p.is_file()),
                key=lambda p: p.stat().st_mtime_ns,
                reverse=True,
            )
            for path in records[:8]:
                payload = json.loads(path.read_text(errors="replace"))
                candidates = [
                    str(item)
                    for item in (payload.get("artifacts") or [])
                    if str(item).strip()
                ]
                if candidates:
                    recent_artifacts = candidates[:20]
                    break
        except Exception:  # noqa: BLE001
            recent_artifacts = []
    return TurnContext(
        run_id=workspace.run_id,
        objective=objective[:2000],
        artifacts=arts,
        plan_summary=plan_summary,
        recent_user_messages=_recent_user_messages(workspace),
        recent_artifacts=recent_artifacts,
        pending_question=pending_question,
        pending_yes_label=pending_yes_label,
        pending_no_label=pending_no_label,
        pending_request=pending_request,
    )


def _tokens(text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower())
        if t not in _STOPWORDS
    }


def topics_related(message: str, objective: str) -> bool:
    """True when message and current objective share meaningful topic tokens."""
    a, b = _tokens(message), _tokens(objective)
    if not a or not b:
        return False
    inter = a & b
    if not inter:
        return False
    ratio = len(inter) / min(len(a), len(b))
    if ratio >= 0.22:
        return True
    return any(len(t) >= 5 for t in inter)


def _artifact_matches(message: str, artifacts: list[str]) -> list[str]:
    low = (message or "").lower()
    hits: list[str] = []
    for rel in artifacts:
        stem = Path(rel).stem.lower()
        parts = [p for p in re.split(r"[_\-\s.]+", stem) if len(p) >= 3]
        if any(p in low for p in parts):
            hits.append(rel)
            continue
        ext = Path(rel).suffix.lower().lstrip(".")
        if ext and ext in low and _ARTIFACT_HINT_RE.search(low):
            hits.append(rel)
    if not hits and _ARTIFACT_HINT_RE.search(low):
        type_map = {
            "slide": {".pptx", ".html", ".md", ".png", ".jpg", ".jpeg", ".webp"},
            "deck": {".pptx"},
            "presentation": {".pptx", ".html"},
            "image": {".png", ".jpg", ".jpeg", ".webp", ".gif"},
            "photo": {".png", ".jpg", ".jpeg", ".webp"},
            "video": {".mp4", ".mov", ".webm"},
            "pdf": {".pdf"},
            "diagram": {".png", ".svg", ".mmd"},
        }
        for key, exts in type_map.items():
            if key in low:
                for rel in artifacts:
                    if Path(rel).suffix.lower() in exts:
                        hits.append(rel)
                break
    seen: set[str] = set()
    out: list[str] = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out[:12]


def resolve_artifact_references(
    message: str,
    ctx: TurnContext,
    *,
    preferred: list[str] | None = None,
) -> list[str]:
    if preferred:
        return list(dict.fromkeys(preferred))[:20]
    hits = _artifact_matches(message, ctx.artifacts)
    if hits:
        return hits
    low = (message or "").lower()
    deictic = bool(
        re.search(r"\b(this|these|those|it|them|current|latest|slides?)\b", low)
    )
    if deictic and ctx.recent_artifacts:
        return list(dict.fromkeys(ctx.recent_artifacts))[:20]
    return []


def ground_artifact_followup(message: str, artifacts: list[str]) -> str:
    refs = [str(path) for path in artifacts if str(path).strip()]
    if not refs:
        return message
    listing = "\n".join(f"- `{path}`" for path in refs[:20])
    return (
        f"{message.strip()}\n\n"
        "The user's words this/these/it/them refer to these existing session artifacts:\n"
        f"{listing}\n\n"
        "Stay on this task and modify/review these artifacts. Do not switch to an "
        "unrelated website, example task, or prior result. Make reasonable visual "
        "design assumptions instead of asking what 'these' means."
    )


def wants_full_plan(message: str, workspace: SessionWorkspace | None = None) -> bool:
    """True for ``/plan|/spec|/goal`` or a prior escalate_plan / agent_mode flag."""
    from kageha.loop.mode_policy import parse_mode_slash, read_agent_mode_flag

    if parse_mode_slash(message) in {"plan", "spec", "goal"}:
        return True
    if _PLAN_CMD_RE.search((message or "").strip()):
        return True
    if workspace is not None and (workspace.root / ESCALATE_PLAN_FLAG).is_file():
        return True
    if workspace is not None and read_agent_mode_flag(workspace.root) in {
        "plan",
        "spec",
        "goal",
    }:
        return True
    return False


def prefer_agent_mode(
    message: str,
    *,
    workspace: SessionWorkspace | None = None,
    explicit: str | None = None,
) -> str:
    """Resolve normal|plan|spec|goal for this turn (does not consume flags)."""
    from kageha.loop.mode_policy import resolve_agent_mode

    root = workspace.root if workspace is not None else None
    return resolve_agent_mode(message, explicit=explicit, workspace_root=root)


def prefer_loop_mode(
    message: str,
    decision: TurnDecision,
    *,
    route: str = "",
    workspace: SessionWorkspace | None = None,
    agent_mode: str | None = None,
) -> str:
    """Act (followup) by default; full for plan/spec/goal."""
    from kageha.loop.mode_policy import loop_mode_for

    del decision  # unused — depth is the model's job
    if route in {"quick_where", "quick_remote", "quick_status", "cancel"}:
        return "n/a"
    mode = agent_mode or prefer_agent_mode(message, workspace=workspace)
    return loop_mode_for(mode)


def classify_deterministic(message: str, ctx: TurnContext) -> TurnDecision:
    """Micro-paths + session continuity. Everything else → agent with tools."""
    text = (message or "").strip()
    if not text:
        return TurnDecision(
            intent="new_task" if not ctx.has_session else "continue_task",
            related_to_current_task=bool(ctx.has_session),
            requires_tools=True,
            discard_old_plan=not ctx.has_session,
            reason="empty message",
        )

    from kageha.chat.quick_remote import should_quick_remote

    remote = should_quick_remote(text, ctx)
    if remote:
        return TurnDecision(
            intent="micro_action",
            related_to_current_task=bool(ctx.has_session),
            requires_tools=False,
            discard_old_plan=False,
            reason=f"one-shot device remote → {remote}",
        )

    if _CANCEL_RE.search(text) and len(text) < 80:
        return TurnDecision(
            intent="cancel",
            related_to_current_task=bool(ctx.has_session),
            requires_tools=False,
            discard_old_plan=False,
            reason="explicit cancel/stop",
        )

    if is_where_question(text):
        return TurnDecision(
            intent="status",
            related_to_current_task=bool(ctx.has_session),
            requires_tools=False,
            reason="where/path question",
        )

    if _STATUS_RE.search(text) and len(text) < 120:
        return TurnDecision(
            intent="status",
            related_to_current_task=bool(ctx.has_session),
            requires_tools=False,
            reason="status/progress check",
        )

    if ctx.pending_question:
        return TurnDecision(
            intent="continue_task",
            related_to_current_task=True,
            requires_tools=True,
            discard_old_plan=False,
            reason="answer to pending clarification",
        )

    if not ctx.has_session:
        return TurnDecision(
            intent="new_task",
            related_to_current_task=False,
            requires_tools=True,
            discard_old_plan=True,
            reason="agent turn — model chooses depth",
        )

    if _EXPLICIT_NEW_RE.search(text):
        return TurnDecision(
            intent="new_task",
            related_to_current_task=False,
            requires_tools=True,
            discard_old_plan=True,
            reason="explicit new-task / start-over",
        )

    if _BARE_BROWSER_RE.search(text) and not _URLISH_RE.search(text):
        return TurnDecision(
            intent="new_task",
            related_to_current_task=False,
            requires_tools=True,
            discard_old_plan=True,
            reason="standalone browser launch — isolate from prior task",
        )

    related = topics_related(text, ctx.objective)
    art_hits = _artifact_matches(text, ctx.artifacts) if ctx.artifacts else []

    if ctx.artifacts and (
        _ARTIFACT_FEEDBACK_RE.search(text) or _POLISH_RE.search(text)
    ):
        refs = ctx.recent_artifacts or art_hits or list(ctx.artifacts[:12])
        return TurnDecision(
            intent="modify_artifact",
            related_to_current_task=True,
            requires_tools=True,
            reuse_artifacts=list(refs)[:20],
            discard_old_plan=False,
            reason="polish/revise current artifacts",
        )

    if ctx.artifacts and (_MODIFY_RE.search(text) or art_hits) and (
        related or art_hits or _CONTINUE_RE.search(text)
    ):
        return TurnDecision(
            intent="modify_artifact",
            related_to_current_task=True,
            requires_tools=True,
            reuse_artifacts=art_hits or list(ctx.recent_artifacts or [])[:20],
            discard_old_plan=False,
            reason="edit/tweak of existing artifact",
        )

    if _RETRY_RE.search(text) or _CONTINUE_RE.search(text):
        return TurnDecision(
            intent="continue_task",
            related_to_current_task=True,
            requires_tools=True,
            discard_old_plan=False,
            reason="retry/continue current work",
        )

    if ctx.objective and not related and _TASKISH_RE.search(text):
        return TurnDecision(
            intent="new_task",
            related_to_current_task=False,
            requires_tools=True,
            discard_old_plan=True,
            reason="new topic unrelated to current objective",
        )

    if (
        ctx.objective
        and not related
        and len(text) >= 12
        and not _ARTIFACT_HINT_RE.search(text)
        and (
            re.search(r"\b(teach|learn|about|how to)\b", text, re.I)
            or len(_tokens(text)) >= 3
        )
    ):
        return TurnDecision(
            intent="new_task",
            related_to_current_task=False,
            requires_tools=True,
            discard_old_plan=True,
            reason="unrelated request — start fresh plan",
        )

    return TurnDecision(
        intent="continue_task" if related or ctx.has_session else "new_task",
        related_to_current_task=bool(related or ctx.has_session),
        requires_tools=True,
        reuse_artifacts=art_hits,
        discard_old_plan=False,
        reason="agent turn — model chooses depth",
    )


async def classify_turn(message: str, ctx: TurnContext) -> TurnDecision:
    """No LLM router — micro-paths or agent. Depth is the acting model's job."""
    return classify_deterministic(message, ctx)


def _prior_actionable_message(recent: list[str], *, exclude: str = "") -> str:
    excl = (exclude or "").strip().lower()
    for msg in reversed(recent or []):
        t = (msg or "").strip()
        if not t or t.lower() == excl:
            continue
        if _RETRY_RE.search(t) or _CANCEL_RE.search(t):
            continue
        if len(t) >= 8:
            return t
    return ""


def _extract_browse_url(text: str) -> str:
    """Best-effort URL/host from a browse ask (adds https:// when missing)."""
    for match in _HOST_RE.finditer(text or ""):
        raw = match.group(1).rstrip(".,);]!?'\"")
        low = raw.lower()
        # Skip common false positives.
        if low in {"comet", "chrome", "browser", "please", "kageha"}:
            continue
        if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".md", ".json", ".py")):
            continue
        if raw.startswith(("http://", "https://")):
            return raw
        if raw.lower().startswith("www."):
            return "https://" + raw
        # Require a plausible TLD (kageha.ca, example.com).
        if re.search(r"\.[a-z]{2,}$", raw, re.I):
            return "https://" + raw
    return ""


def expand_user_message(message: str, ctx: TurnContext) -> str:
    """Expand bare/short tool asks so the agent is not hijacked by stale goals."""
    text = (message or "").strip()
    if not text:
        return text
    # /plan|/spec|/goal are mode signals — strip before the agent sees the ask.
    from kageha.loop.mode_policy import strip_mode_slash

    text = strip_mode_slash(text) or text

    if ctx.pending_question:
        request = ctx.pending_request or ctx.objective
        option_context = ""
        if ctx.pending_yes_label or ctx.pending_no_label:
            option_context = (
                f"\nYes means: {ctx.pending_yes_label or 'yes'}."
                f"\nNo means: {ctx.pending_no_label or 'no'}."
            )
        return (
            f"Continue the interrupted request: {request}\n\n"
            f"Clarification question: {ctx.pending_question}"
            f"{option_context}\n"
            f"User's answer: {text}\n\n"
            "Use this answer and continue the work now. Do not ask the same "
            "question again."
        )

    # Comet / browse-to-URL — force tools; never answer with a capability list.
    if _COMET_OR_BROWSE_RE.search(text):
        stamp = datetime.now(tz=timezone.utc).strftime("%H%M%S")
        shot = f"artifacts/browse_{stamp}.png"
        url = _extract_browse_url(text) or "https://example.com"
        use_comet = bool(re.search(r"\bcomet\b", text, re.I))
        connect = (
            "First call browser_connect(target='comet'). "
            if use_comet
            else "Use browser_* tools (browser_connect(target='comet') if a logged-in session is needed). "
        )
        return (
            f"Do this now with tools (do NOT list capabilities or ask whether you can):\n"
            f"{connect}"
            f"Then browser_open('{url}') and take a screenshot saved to `{shot}`.\n"
            f"Confirm with the exact path `{shot}` and a one-line summary of the page.\n"
            f"Original ask: {text}"
        )

    if _BARE_BROWSER_RE.search(text) and not _URLISH_RE.search(text):
        stamp = datetime.now(tz=timezone.utc).strftime("%H%M%S")
        shot = f"artifacts/browser_open_{stamp}.png"
        return (
            "Open a browser session now with browser_* tools "
            "(browser_open to https://example.com is fine if no URL was given). "
            f"Save a NEW screenshot to `{shot}` (do not reuse artifacts/browse.png). "
            "Do NOT continue prior LinkedIn, profile, research, or presentation work — "
            "this message is only about opening the browser. "
            "Do NOT list capabilities. "
            f"Confirm with the exact path `{shot}` when done."
        )

    if not _RETRY_RE.search(text):
        return text
    prior = _prior_actionable_message(ctx.recent_user_messages, exclude=text)
    if not prior:
        return text
    if _BARE_BROWSER_RE.search(prior) and not _URLISH_RE.search(prior):
        return expand_user_message(prior, ctx)
    return (
        f"{text.capitalize()} again — do this now with tools:\n{prior}\n\n"
        "Use the tools appropriate to that exact request. Stay on the same "
        "artifacts and topic; do not switch to an unrelated website, browsing "
        "demo, or example task."
    )


def route_for_decision(
    decision: TurnDecision,
    *,
    has_session: bool,
    message: str = "",
    turn_ctx: TurnContext | None = None,
) -> RouteKind:
    """Map a TurnDecision to the chat REPL action."""
    if message and turn_ctx is not None:
        from kageha.chat.quick_remote import should_quick_remote

        if should_quick_remote(message, turn_ctx):
            return "quick_remote"

    if decision.intent == "cancel":
        return "cancel"
    if decision.intent == "micro_action":
        return "quick_remote"
    if decision.intent == "status":
        if message and is_where_question(message):
            return "quick_where"
        if message and _STATUS_RE.search(message.strip()):
            return "quick_status"
        return "quick_where" if (message and is_where_question(message)) else "quick_status"

    if decision.intent == "new_task" and (decision.discard_old_plan or not has_session):
        return "new_run" if has_session else "first_run"
    if decision.intent in {"continue_task", "modify_artifact"}:
        return "resume" if has_session else "first_run"
    if decision.intent == "new_task":
        return "new_run" if has_session else "first_run"
    return "resume" if has_session else "first_run"


def persist_turn_decision(
    workspace: SessionWorkspace | None,
    decision: TurnDecision,
    *,
    message: str = "",
    route: str = "",
) -> None:
    payload = {
        **decision.to_dict(),
        "message_preview": (message or "")[:240],
        "route": route,
    }
    if workspace is None:
        return
    try:
        EventLog(path=workspace.root / "events.jsonl").emit("turn_decision", payload)
    except Exception:  # noqa: BLE001
        pass
    try:
        path = workspace.root / "chat.jsonl"
        rec = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "role": "system",
            "text": f"turn_decision: {json.dumps(payload)[:4000]}",
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001
        pass


def new_task_prompt(
    message: str,
    *,
    prior_run_id: str | None = None,
    reuse_artifacts: list[str] | None = None,
) -> str:
    from kageha.loop.mode_policy import strip_mode_slash

    text = strip_mode_slash(message or "")
    if not prior_run_id:
        return text
    note_parts = [
        text,
        "",
        f"(Prior session id: {prior_run_id}. "
        "Start a new plan for this request; do not continue the old plan. "
        "You may reuse files from that session only if explicitly useful.)",
    ]
    if reuse_artifacts:
        note_parts.append("Suggested prior artifacts: " + ", ".join(reuse_artifacts[:8]))
    return "\n".join(note_parts)
