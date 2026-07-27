"""Deterministic chat micro-helpers — where / status / greetings (no agent loop)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from kageha.harness.sandbox import SessionWorkspace
from kageha.loop.artifacts import classify_artifacts

# Instant micro-greetings / acks / check-ins — no LoopController / model call.
_QUICK_GREETING_RE = re.compile(
    r"^(hi+|hello|hey+|yo|sup|howdy|"
    r"good\s+(morning|afternoon|evening)|"
    r"thanks|thank\s+you|thx|ok|okay|cool|great|nice|"
    r"test|ping|pong|"
    r"how'?s\s+it\s+going|how\s+are\s+you(?:\s+doing)?|"
    r"(?:are\s+)?you\s+there|just\s+checking(?:\s+in)?|"
    r"checking\s+(?:in|you'?re\s+there)|"
    r"hi\s*[—\-–,]?\s*just\s+checking(?:\s+you'?re\s+there)?)"
    r"[\s!.?…—\-–🙂😊👋]*$",
    re.I,
)


def quick_chat_reply(text: str, *, channel: str = "") -> str | None:
    """Return an instant reply for trivial greetings/acks, else None.

    Only matches short, unambiguous micro-messages. Real tasks must fall through
    to the agent loop.
    """
    t = (text or "").strip()
    if not t or len(t) > 80:
        return None
    low = t.lower().rstrip("!.? ")
    if low in {"who are you", "what are you", "who is this"}:
        return (
            "I'm Kageha, your AI agent. I can research, use tools, create files, "
            "and continue work across this session."
        )
    if low in {
        "what can you do",
        "what do you do",
        "help",
        "capabilities",
        "what are your capabilities",
    }:
        return (
            "I can chat, research the web (with citations), browse, edit files, "
            "run shell, use skills/MCP, and run Plan/Goal loops for bigger work. "
            "Ask a task, or pick Plan / Goal in the WebUI."
        )
    if not _QUICK_GREETING_RE.match(t):
        return None
    if low in {"thanks", "thank you", "thx"}:
        return "You're welcome — send another message anytime."
    if low in {"test", "ping"}:
        return "pong — I'm here."
    if re.search(
        r"how'?s\s+it\s+going|how\s+are\s+you|you\s+there|just\s+checking|checking\s+in",
        low,
    ):
        return "All good — I'm here and ready. What do you want to work on?"
    return "Hey! I'm here — what do you want to work on?"


# "Where did you save it?" / "show the video" / "what's the path?"
_WHERE_RE = re.compile(
    r"\b("
    r"where\b.*\b(save|saved|put|wrote|output|file|video|deck|slides?|artifact)|"
    r"(save|saved|output|path|location|folder|directory)\b.*\b(where|what)|"
    r"show\s+(me\s+)?(the\s+)?(files?|paths?|artifacts?|outputs?)|"
    r"(open|find)\s+(the\s+)?(file|video|deck|output)|"
    r"which\s+folder"
    r")\b",
    re.I,
)


def is_where_question(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _WHERE_RE.search(t):
        return True
    low = t.lower().rstrip("?.!")
    return low in {
        "where",
        "where is it",
        "where is that",
        "path",
        "paths",
        "location",
        "folder",
        "files",
        "artifacts",
    }


def answer_before_workspace(command: str) -> str:
    """Explain pre-task state for workspace-oriented slash commands."""
    low = (command or "").strip().lower()
    if low == "/status":
        return (
            "Chat is ready. No task workspace has been created yet; "
            "one will be created automatically when you request work."
        )
    if low == "/files":
        return (
            "No files yet. Kageha creates a workspace and saves artifacts "
            "when you request work."
        )
    return (
        "There's no task workspace yet. Kageha will create one automatically "
        "when you request work."
    )


def answer_status(workspace: SessionWorkspace) -> str:
    """Lightweight progress snapshot — no LLM."""
    lines: list[str] = []
    goal_path = workspace.path("goal_card.json")
    state_path = workspace.path("task_state.json")
    task = ""
    progress = None
    if goal_path.is_file():
        try:
            data = json.loads(goal_path.read_text(encoding="utf-8"))
            task = str(data.get("task") or "").strip()
            items = data.get("items") or []
            if items:
                done = sum(1 for i in items if i.get("passes"))
                progress = (done, len(items))
                lines.append(f"Goal: {task[:200]}" if task else "Goal checklist:")
                for item in items[:8]:
                    box = "x" if item.get("passes") else " "
                    desc = str(item.get("description") or item.get("id") or "")[:120]
                    lines.append(f"- [{box}] {desc}")
                if len(items) > 8:
                    lines.append(f"…and {len(items) - 8} more checklist items.")
        except Exception:  # noqa: BLE001
            pass
    if state_path.is_file() and progress is None:
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            task = task or str(data.get("objective") or "").strip()
            if task:
                lines.append(f"Objective: {task[:200]}")
            stages = data.get("stages") or []
            if stages:
                lines.append(f"Stages recorded: {len(stages)}")
        except Exception:  # noqa: BLE001
            pass
    files = classify_artifacts(workspace.list_files())
    if files:
        lines.append("")
        lines.append(f"Artifacts: {len(files)} file(s) in session.")
        for rel in files[:5]:
            lines.append(f"• {rel}")
        if len(files) > 5:
            lines.append(f"…and {len(files) - 5} more.")
    if not lines:
        return (
            "Session is active but there's no goal checklist yet.\n"
            f"Folder: {workspace.root}"
        )
    if progress:
        done, total = progress
        lines.insert(0, f"Progress: {done}/{total} goals complete.")
        lines.insert(1, "")
    lines.append("")
    lines.append(f"Session: {workspace.root}")
    return "\n".join(lines)


def answer_where(workspace: SessionWorkspace) -> str:
    files = classify_artifacts(workspace.list_files())
    if not files:
        return (
            f"I don't see deliverables yet in this session.\n"
            f"Session folder: {workspace.root}"
        )
    mains = [
        f
        for f in files
        if Path(f).suffix.lower()
        in {".mp4", ".mov", ".pptx", ".pdf", ".png", ".jpg", ".jpeg", ".html", ".md"}
        and not f.startswith("artifacts/video_frames/")
        and "audio_" not in Path(f).name
    ]
    lines = ["Here's where things landed:", ""]
    show = mains or files[:12]
    for rel in show[:12]:
        abs_path = (workspace.root / rel).resolve()
        lines.append(f"• {rel}")
        lines.append(f"  {abs_path}")
    if len(files) > len(show):
        lines.append(f"…and {len(files) - len(show)} more under the session folder.")
    lines.append("")
    lines.append(f"Session: {workspace.root}")
    if any(f.endswith(".mp4") for f in files):
        vid = next(f for f in files if f.endswith(".mp4"))
        lines.append("")
        lines.append(f"Open the video:  open {workspace.root / vid}")
    return "\n".join(lines)
