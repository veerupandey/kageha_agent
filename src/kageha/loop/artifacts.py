"""Summarize generated session artifacts for terminal / chat display."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from kageha.harness.sandbox import SessionWorkspace
from kageha.config import sessions_dir

_ARTIFACT_PATH_RE = re.compile(
    r"(?:(?:^|[\s`\"'(])((?:artifacts|outputs|diagrams|carousel|research|slides|deck)"
    r"/[A-Za-z0-9._\-/]+\.[A-Za-z0-9]+))",
    re.M,
)

# Session bookkeeping — hide from the user-facing deliverable list.
_INTERNAL_FILES = frozenset({
    "events.jsonl",
    "goal_card.json",
    "plan.json",
    "result.md",
    "todo.md",
    "chat.jsonl",
    "task_state.json",
    "inputs/README.md",
    ".DS_Store",
})
_INTERNAL_ROOTS = frozenset({"_memory", "_turns", "checkpoints"})
_SCRATCH_ROOTS = frozenset({"inputs", "scripts", "tmp", "temp"})
_ROOT_SCRATCH_SUFFIXES = frozenset({
    ".py", ".pyc", ".js", ".mjs", ".cjs", ".ts", ".sh", ".bash", ".zsh",
})
# Dependency / build trees — never treat as session deliverables.
_NOISE_DIR_NAMES = frozenset({
    "node_modules",
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    ".turbo",
    ".next",
    "dist",
    "build",
    "target",
    ".kageha-tmp",
})
# Intermediate build debris — hide unless the user asks for them.
_NOISE_PREFIXES = (
    "artifacts/video_frames/",
)

# Prefer showing these first when present.
_PRIORITY_PREFIXES = (
    "artifacts/",
    "outputs/",
    "diagrams/",
    "carousel/",
    "research/",
    "slides/",
    "deck/",
)


def is_user_artifact(rel: str) -> bool:
    rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    if not rel or rel in _INTERNAL_FILES:
        return False
    if Path(rel).name in _INTERNAL_FILES or Path(rel).name == ".DS_Store":
        return False
    parts = Path(rel).parts
    if parts and parts[0] in _INTERNAL_ROOTS:
        return False
    if parts and parts[0] in _SCRATCH_ROOTS:
        return False
    if any(part in _NOISE_DIR_NAMES for part in parts):
        return False
    # Source files placed at the session root are normally agent scaffolding,
    # not the result the user requested. Code deliverables belong in an
    # explicit output directory (artifacts/, outputs/, etc.).
    if len(parts) == 1 and Path(rel).suffix.lower() in _ROOT_SCRATCH_SUFFIXES:
        return False
    if any(rel.startswith(p) for p in _NOISE_PREFIXES):
        return False
    return True


def classify_artifacts(paths: list[str]) -> list[str]:
    """Filter + stable sort: priority dirs first, then alpha."""
    user = [p.replace("\\", "/") for p in paths if is_user_artifact(p)]

    def sort_key(p: str) -> tuple[int, str]:
        for i, pref in enumerate(_PRIORITY_PREFIXES):
            if p.startswith(pref):
                return (i, p)
        return (len(_PRIORITY_PREFIXES), p)

    return sorted(set(user), key=sort_key)


def artifact_delta(before: list[str] | set[str], after: list[str]) -> list[str]:
    """Artifacts that appeared this turn (stable classify order)."""
    prev = {p.replace("\\", "/") for p in before}
    new = [p for p in classify_artifacts(after) if p not in prev]
    return new


def snapshot_artifact_mtimes(
    workspace_root: Path | str, paths: list[str]
) -> dict[str, tuple[float, int]]:
    """Fingerprint map (mtime, size) for classified artifacts."""
    root = Path(workspace_root)
    out: dict[str, tuple[float, int]] = {}
    for rel in classify_artifacts(paths):
        try:
            st = (root / rel).stat()
            out[rel] = (st.st_mtime, int(st.st_size))
        except OSError:
            continue
    return out


def artifacts_touched_since(
    workspace_root: Path | str,
    after_paths: list[str],
    before_mtimes: dict[str, tuple[float, int]],
    *,
    also_mention: str = "",
) -> list[str]:
    """New/changed paths, plus any artifact paths mentioned in the reply text."""
    root = Path(workspace_root)
    touched: list[str] = []
    for rel in classify_artifacts(after_paths):
        try:
            st = (root / rel).stat()
            fp: tuple[float, int] = (st.st_mtime, int(st.st_size))
        except OSError:
            continue
        prev = before_mtimes.get(rel)
        if prev is None:
            touched.append(rel)
            continue
        prev_m, prev_s = prev
        if fp[0] > prev_m + 1e-3 or fp[1] != prev_s:
            touched.append(rel)
    # Paths the model claims it wrote — show them even if overwrite was same size
    for rel in artifacts_mentioned_in_text(also_mention):
        if (root / rel).is_file() and rel not in touched:
            touched.append(rel)
    return classify_artifacts(touched)


def artifacts_mentioned_in_text(text: str) -> list[str]:
    """Pull deliverable-looking relative paths out of model/chat text."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for m in _ARTIFACT_PATH_RE.finditer(text):
        rel = (m.group(1) or "").strip().strip("`\"'")
        rel = rel.replace("\\", "/")
        if not rel or rel in seen:
            continue
        if is_user_artifact(rel):
            seen.add(rel)
            found.append(rel)
    return found


def format_artifacts_report(
    *,
    run_id: str,
    artifacts: list[str],
    workspace_root: Path | str | None = None,
    exported: list[str] | None = None,
    max_items: int = 40,
    highlight: list[str] | None = None,
    older_count: int = 0,
) -> str:
    """Human-readable block for terminal / WhatsApp / chat."""
    root = Path(workspace_root) if workspace_root else sessions_dir() / run_id
    if highlight is not None:
        items = classify_artifacts(highlight)
        title = "── New this turn ──" if items else "── Artifacts ──"
    else:
        items = classify_artifacts(artifacts)
        title = "── Artifacts ──"
    lines = ["", title]
    if not items:
        lines.append("(no new user artifacts this turn)")
        if older_count:
            lines.append(f"({older_count} earlier files still in the session folder)")
    else:
        shown = items[:max_items]
        for rel in shown:
            abs_path = (root / rel).resolve()
            lines.append(f"  • {rel}")
            lines.append(f"      {abs_path}")
        if len(items) > max_items:
            lines.append(f"  … +{len(items) - max_items} more")
        if older_count:
            lines.append(f"  ({older_count} earlier files in session — /files to list)")
    lines.append(f"session: {root}")
    if exported:
        lines.append(f"exported ({len(exported)}):")
        for rel in exported[:max_items]:
            lines.append(f"  • {rel}")
    lines.append("")
    return "\n".join(lines)


def _is_stop_jargon(message: str) -> bool:
    low = (message or "").strip().lower()
    if not low:
        return True
    if low in {
        "goals validated with evidence",
        "goals met",
        "init",
        "loop exhausted",
        "hit max steps",
    }:
        return True
    if low.startswith("hit max steps") or low.startswith("hit budget"):
        return True
    if low.startswith("no progress for"):
        return True
    return False


def humanize_turn_reply(
    *,
    message: str,
    status: str,
    user_line: str,
    new_artifacts: list[str],
    workspace_root: Path | str,
    result_evidence: str = "",
) -> str:
    """Chat-facing summary — never lead with bare stop-rule jargon."""
    root = Path(workspace_root)
    msg = (message or "").strip()
    normalized_status = (status or "").strip().lower()
    if normalized_status != "success":
        if normalized_status in {"ask_user", "hitl"} and msg:
            return msg
        # When the run stalled but we already composed a real answer (grace
        # summary), show that answer — hiding it made chat look broken even
        # though earlier tools (e.g. skill_run) had already succeeded.
        if (
            normalized_status
            in {"no_progress", "max_steps", "budget", "budget_exceeded", "max_cost"}
            and msg
            and not _is_stop_jargon(msg)
            and len(msg) >= 80
        ):
            if new_artifacts and not any(a in msg for a in new_artifacts[:3]):
                paths = "\n".join(f"  {(root / a).resolve()}" for a in new_artifacts[:4])
                return f"{msg}\n\nFiles from this attempt:\n{paths}"
            return msg
        friendly = {
            "max_steps": "I couldn't complete that request before the step limit.",
            "max_cost": "I couldn't complete that request before the cost limit.",
            "budget": "I couldn't complete that request before the cost limit.",
            "budget_exceeded": "I couldn't complete that request before the cost limit.",
            "no_progress": "I couldn't complete that request because the available approach stopped making progress.",
            "cancelled": "The request was cancelled before completion.",
            "error": "I couldn't complete that request because the run failed.",
            "hitl": "I need your input before I can complete that request.",
            "ask_user": "I need your input before I can complete that request.",
        }.get(normalized_status, "I couldn't verify that the request was completed.")
        # Surface the real cause (e.g. model routing) so chat doesn't invent
        # "no tools configured" after a failed run that did use tools.
        if normalized_status == "error" and msg and not _is_stop_jargon(msg):
            detail = msg.strip()
            # Flattened cross-provider tool history leaks as "[called tools: bash] …"
            # — that is not a useful user-facing cause.
            if "[called tools:" in detail.lower() or detail.startswith("bash(["):
                detail = (
                    "The language model failed while tools were in progress "
                    "(provider/routing error). Please try again."
                )
            if len(detail) > 400:
                detail = detail[:400] + "…"
            friendly += f"\n\nCause: {detail}"
        # A failed run may have useful partial output, but it must never be
        # presented as the requested completed deliverable.
        if new_artifacts:
            paths = "\n".join(f"  {(root / a).resolve()}" for a in new_artifacts[:4])
            friendly += f"\n\nPartial files (not verified as the requested result):\n{paths}"
        return friendly

    stop_jargon = {
        "goals validated with evidence",
        "goals met",
        "init",
        "loop exhausted",
        "hit max steps",
    }
    if msg and msg.lower() not in stop_jargon and not msg.lower().startswith("hit "):
        # Still prepend new paths if the model forgot them
        if new_artifacts and not any(a in msg for a in new_artifacts[:3]):
            paths = "\n".join(f"  {root / a}" for a in new_artifacts[:4])
            return f"{msg}\n\nSaved:\n{paths}"
        return msg
    if new_artifacts:
        primary = new_artifacts[0]
        return (
            f"Done — created {Path(primary).name}.\n"
            f"{(root / primary).resolve()}"
        )
    if normalized_status == "success":
        evidence = (result_evidence or "").strip()
        if evidence:
            return f"Found and verified:\n{evidence}"
        return f"Done for: {user_line.strip()[:160]}"
    return msg or status or "Finished."


def format_artifacts_compact(
    *,
    run_id: str,
    artifacts: list[str],
    max_items: int = 12,
) -> str:
    """Shorter block for WhatsApp / chat replies."""
    items = classify_artifacts(artifacts)
    if not items:
        return "Artifacts: (none)"
    root = sessions_dir() / run_id
    lines = ["Artifacts:"]
    for rel in items[:max_items]:
        lines.append(f"• {rel}")
    if len(items) > max_items:
        lines.append(f"… +{len(items) - max_items} more")
    lines.append(f"Folder: {root}")
    return "\n".join(lines)


def artifacts_from_workspace(ws: SessionWorkspace) -> list[str]:
    return classify_artifacts(ws.list_files())


def _mirror_dest_rel(source_rel: str) -> str:
    rel = source_rel.replace("\\", "/").lstrip("./")
    if rel.startswith("artifacts/"):
        return rel
    if "/" in rel:
        return f"artifacts/{rel}"
    return f"artifacts/{Path(rel).name}"


def mirror_deliverables_into_session(
    workspace: SessionWorkspace,
    *,
    source_root: Path,
    relative_paths: list[str] | set[str],
) -> list[str]:
    """Copy user-facing project files into the session workspace for WebUI download.

    When ``project_root`` is bound, tools write under the repo; the WebUI serves
    ``~/.kageha/sessions/{id}/`` only. Mirroring keeps Artifacts + /files/ working.
    """
    root = source_root.expanduser().resolve()
    if not root.is_dir():
        return []
    mirrored: list[str] = []
    seen: set[str] = set()
    for raw in relative_paths:
        rel = str(raw or "").replace("\\", "/").lstrip("./")
        if not rel or rel in seen or not is_user_artifact(rel):
            continue
        seen.add(rel)
        src = (root / rel).resolve()
        if not src.is_file() or not str(src).startswith(str(root)):
            continue
        dest_rel = _mirror_dest_rel(rel)
        try:
            dest = workspace.path(dest_rel)
        except ValueError:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if dest.is_file():
                st_src = src.stat()
                st_dest = dest.stat()
                if (
                    st_src.st_size == st_dest.st_size
                    and int(st_src.st_mtime) == int(st_dest.st_mtime)
                ):
                    mirrored.append(dest_rel)
                    continue
            shutil.copy2(src, dest)
        except OSError:
            continue
        mirrored.append(dest_rel)
    return classify_artifacts(mirrored)
