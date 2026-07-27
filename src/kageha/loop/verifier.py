"""Verifier — grades goals AND emits actionable defects for repair."""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from kageha.loop.goal_card import GoalCard
from kageha.loop.task_state import Defect, ValidationSnapshot
from kageha.models.base import ChatMessage
from kageha.models.router import ModelRouter


_TEXT_SUFFIXES = {
    ".csv",
    ".html",
    ".json",
    ".md",
    ".py",
    ".rst",
    ".text",
    ".txt",
    ".yaml",
    ".yml",
}
_INTERNAL_FILES = {
    "events.jsonl",
    "goal_card.json",
    "plan.json",
    "result.md",
    "task_state.json",
    "todo.md",
    "chat.jsonl",
}

# Tools that never prove a lookup/status outcome on their own.
_META_TOOLS = frozenset({"todo_write", "todo_read", "read_file", "ask_human"})

# Informational / status goals — safe for deterministic pass when evidence is clear.
_LOOKUP_STATUS_RE = re.compile(
    r"\b("
    r"lookup|look\s*up|status|scan|find|list|check|show|what|where|who|"
    r"report|browse|search|available|discover|query|inspect|"
    r"how\s+many|is\s+the|are\s+there|obtain|summarize"
    r")\b",
    re.I,
)

# Heavy artifact/build goals — never short-circuit these as lookup passes.
_HEAVY_DELIVERABLE_RE = re.compile(
    r"\b("
    r"pptx|powerpoint|pdf|slide|deck|video|reel|carousel|diagram|"
    r"implement|compile|refactor|"
    r"(?:create|build|write|generate|make|produce|render)\b[^.]{0,40}\b"
    r"(?:app|code|script|module|service|image|poster)"
    r")\b",
    re.I,
)

_INTERNAL_ANSWER_RE = re.compile(
    r"(?i)^(goals?\s+(validated|met)|init|loop exhausted|hit max steps|"
    r"produced the requested deliverable|verified the new deliverable)"
)


@dataclass
class VerifyResult:
    goal: GoalCard
    snapshot: ValidationSnapshot = field(default_factory=ValidationSnapshot)


def is_lookup_status_text(text: str) -> bool:
    """True when *text* is primarily informational (lookup/status), not a build."""
    blob = (text or "").strip()
    if not blob:
        return False
    if _HEAVY_DELIVERABLE_RE.search(blob):
        return False
    return bool(_LOOKUP_STATUS_RE.search(blob))


def is_lookup_status_goal(goal: GoalCard) -> bool:
    """True when the goal is primarily informational (lookup/status), not a build."""
    text = " ".join([goal.task or ""] + [i.description for i in goal.items])
    return is_lookup_status_text(text)


def try_deterministic_lookup_verify(
    goal: GoalCard,
    *,
    successful_tools: list[str] | None = None,
    turn_artifacts: list[str] | None = None,
    answer_text: str = "",
    workspace_summary: str = "",
) -> VerifyResult | None:
    """Pass lookup/status goals when turn evidence is already conclusive.

    Returns None when the short-circuit does not apply (caller keeps LLM result).
    Requires a successful non-meta tool plus at least one of: turn artifacts,
    substantive answer text, or non-empty workspace evidence.
    """
    if not is_lookup_status_goal(goal):
        return None
    tools = [
        t
        for t in (successful_tools or [])
        if t and t not in _META_TOOLS
    ]
    if not tools:
        return None
    artifacts = [a for a in (turn_artifacts or []) if a]
    answer = (answer_text or "").strip()
    has_answer = (
        len(answer) >= 40
        and not _INTERNAL_ANSWER_RE.search(answer)
        and not answer.lower().startswith("error:")
    )
    summary = (workspace_summary or "").strip()
    has_workspace = bool(summary) and not summary.startswith("(no ")
    if not (artifacts or has_answer or has_workspace):
        return None

    evidence_bits: list[str] = list(tools[:4])
    if artifacts:
        evidence_bits.extend(artifacts[:4])
    elif has_answer:
        evidence_bits.append("answer_text")
    evidence = ", ".join(evidence_bits) or "lookup_tool_success"
    for item in goal.items:
        if not item.passes:
            goal.mark(item.id, passes=True, evidence=evidence)
    return VerifyResult(
        goal=goal,
        snapshot=ValidationSnapshot(
            status="pass",
            notes=f"deterministic lookup/status verify ({evidence})"[:400],
        ),
    )


def _maybe_deterministic_lookup_pass(
    goal: GoalCard,
    *,
    model_said_done: bool,
    successful_tools: list[str] | None,
    turn_artifacts: list[str] | None,
    answer_text: str,
    workspace_summary: str,
) -> VerifyResult | None:
    if not model_said_done:
        return None
    return try_deterministic_lookup_verify(
        goal,
        successful_tools=successful_tools,
        turn_artifacts=turn_artifacts,
        answer_text=answer_text,
        workspace_summary=workspace_summary,
    )


def build_workspace_evidence(
    root: Path,
    *,
    max_files: int = 80,
    max_chars: int = 14_000,
    include_paths: set[str] | None = None,
) -> str:
    """Build bounded, content-aware evidence for the goal verifier."""
    lines: list[str] = []
    used = 0
    files = sorted(p for p in root.rglob("*") if p.is_file())
    for path in files[:max_files]:
        rel = str(path.relative_to(root))
        if include_paths is not None and rel not in include_paths:
            continue
        if rel in _INTERNAL_FILES or rel.startswith(("_memory/", "checkpoints/")):
            continue
        try:
            size = path.stat().st_size
            detail = _file_evidence(path)
        except OSError as exc:
            size = 0
            detail = f"unreadable={exc}"
        entry = f"- {rel} | bytes={size}"
        if detail:
            entry += f" | {detail}"
        if used + len(entry) + 1 > max_chars:
            lines.append("- ... evidence truncated ...")
            break
        lines.append(entry)
        used += len(entry) + 1
    if lines:
        return "\n".join(lines)
    if include_paths is not None:
        return "(no files created or modified during this turn)"
    return "(no generated files)"


def _file_evidence(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        text = path.read_text(errors="replace").strip()
        preview = re.sub(r"\s+", " ", text)[:700]
        return f"text_preview={json.dumps(preview)}"
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        try:
            from PIL import Image

            with Image.open(path) as image:
                return f"image={image.width}x{image.height} mode={image.mode}"
        except Exception:  # noqa: BLE001
            return "image=unverified"
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader

            return f"pdf_pages={len(PdfReader(str(path)).pages)}"
        except Exception:  # noqa: BLE001
            return "pdf_pages=unverified"
    if suffix == ".pptx":
        try:
            with zipfile.ZipFile(path) as archive:
                count = sum(
                    1
                    for name in archive.namelist()
                    if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
                )
            return f"pptx_slides={count}"
        except Exception:  # noqa: BLE001
            return "pptx_slides=unverified"
    if suffix == ".mp4":
        return f"video_bytes={path.stat().st_size}"
    return ""


async def verify_with_defects(
    goal: GoalCard,
    *,
    router: ModelRouter,
    workspace_summary: str,
    transcript_tail: str,
    task_state_projection: str = "",
    execution_provider: str = "",
    task_id: str = "",
    model_said_done: bool = False,
    successful_tools: list[str] | None = None,
    turn_artifacts: list[str] | None = None,
    answer_text: str = "",
) -> VerifyResult:
    """Strict verify: update goals + emit repairable defects.

    When the LLM returns unknown (JSON blip / empty) after the model claims done,
    lookup/status goals with clear tool+artifact/answer evidence short-circuit to
    pass so the loop does not grind CONTINUE/repair until no_progress.
    """
    det_kwargs = dict(
        model_said_done=model_said_done,
        successful_tools=successful_tools,
        turn_artifacts=turn_artifacts,
        answer_text=answer_text,
        workspace_summary=workspace_summary,
    )

    prompt = (
        "You are a strict verifier for an autonomous agent.\n"
        "Return ONLY JSON with this schema:\n"
        "{\n"
        '  "status": "pass"|"repair"|"fail",\n'
        '  "updates": [{"id": str, "passes": bool, "evidence": str}],\n'
        '  "defects": [{\n'
        '     "artifact": str, "severity": "critical"|"major"|"minor",\n'
        '     "problem": str, "evidence": str, "repair": str, "stage_id": str\n'
        "  }],\n"
        '  "next_action": str,\n'
        '  "notes": str\n'
        "}\n"
        "Rules:\n"
        "- Only mark passes=true with concrete workspace evidence (sizes, counts, previews).\n"
        "- Workspace evidence is scoped to files created or modified in the current turn. "
        "Do not use older files or prior-turn claims as proof of new work.\n"
        "- For browse/show/answer tasks, tool output in the recent transcript can prove "
        "the lookup, but the requested information must be present there.\n"
        "- If a requested deliverable is missing/incomplete, status=repair and add a defect "
        "with a specific repair instruction (not vague advice).\n"
        "- status=pass only when every goal has evidence and no critical/major defects.\n"
        "- status=fail only when the approach is fundamentally wrong (not a small fix).\n"
        "- next_action examples: repair_artifact | continue | replan_stage | ask_user.\n\n"
        f"Goal card:\n{goal.to_markdown()}\n\n"
        f"TaskState projection:\n{(task_state_projection or '(none)')[:3500]}\n\n"
        f"Workspace evidence:\n{workspace_summary[:14000]}\n\n"
        f"Recent transcript:\n{transcript_tail[:3000]}"
    )
    snapshot = ValidationSnapshot(status="unknown")
    try:
        _, resp = await router.chat(
            [ChatMessage(role="user", content=prompt)],
            role="fast_worker",
            max_tokens=1400,
            task_id=task_id,
            exclude_providers=(
                {execution_provider}
                if execution_provider and router.provider_control is not None
                else set()
            ),
        )
        text = resp.message.content or ""
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            det = _maybe_deterministic_lookup_pass(goal, **det_kwargs)
            if det is not None:
                return det
            return VerifyResult(goal=goal, snapshot=snapshot)
        data = json.loads(match.group(0))
        for upd in data.get("updates") or []:
            if upd.get("passes"):
                goal.mark(
                    upd["id"],
                    passes=True,
                    evidence=str(upd.get("evidence") or ""),
                )
            elif upd.get("passes") is False:
                # explicit fail — clear pass if verifier retracts
                for item in goal.items:
                    if item.id == upd.get("id") and item.passes:
                        item.passes = False
                        item.evidence = str(upd.get("evidence") or item.evidence)
        defects: list[Defect] = []
        for d in data.get("defects") or []:
            if not isinstance(d, dict):
                continue
            problem = str(d.get("problem") or "").strip()
            if not problem:
                continue
            defects.append(
                Defect(
                    artifact=str(d.get("artifact") or "unknown")[:200],
                    severity=str(d.get("severity") or "major")[:20],
                    problem=problem[:400],
                    evidence=str(d.get("evidence") or "")[:400],
                    repair=str(d.get("repair") or "")[:400],
                    stage_id=str(d.get("stage_id") or "")[:40],
                )
            )
        status = str(data.get("status") or "unknown").lower()
        if status not in {"pass", "repair", "fail"}:
            if defects:
                status = "repair"
            elif goal.all_passed():
                status = "pass"
            else:
                status = "repair" if not goal.all_passed() else "pass"
        # Calibrated: cannot pass with critical defects
        if any(d.severity == "critical" for d in defects):
            status = "repair"
        snapshot = ValidationSnapshot(
            status=status,
            defects=defects,
            next_action=str(data.get("next_action") or "")[:80],
            notes=str(data.get("notes") or "")[:400],
        )
    except Exception:  # noqa: BLE001
        det = _maybe_deterministic_lookup_pass(goal, **det_kwargs)
        if det is not None:
            return det
        return VerifyResult(goal=goal, snapshot=snapshot)

    # LLM blipped into unknown, or remapped unknown→repair with no defects.
    # Lookup/status goals with clear evidence should not grind CONTINUE/repair.
    if snapshot.status in {"", "unknown"} or (
        snapshot.status == "repair"
        and not snapshot.defects
        and not goal.all_passed()
    ):
        det = _maybe_deterministic_lookup_pass(goal, **det_kwargs)
        if det is not None:
            return det
    return VerifyResult(goal=goal, snapshot=snapshot)
