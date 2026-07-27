"""Hermes-style skill curator: usage tracking, pin, soft-archive, restore."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kageha.config import kageha_home


def skills_root() -> Path:
    return kageha_home() / "skills"


def usage_path() -> Path:
    return skills_root() / "usage.json"


def archive_root() -> Path:
    return skills_root() / "archive"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_usage() -> dict[str, dict[str, Any]]:
    path = usage_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            out[str(k)] = dict(v)
    return out


def _save_usage(data: dict[str, dict[str, Any]]) -> None:
    root = skills_root()
    root.mkdir(parents=True, exist_ok=True)
    usage_path().write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def is_user_skill_path(path: Path) -> bool:
    """True when skill lives under ~/.kageha/skills/ (not bundled / archive)."""
    try:
        resolved = path.resolve()
        root = skills_root().resolve()
        if not str(resolved).startswith(str(root) + "/") and resolved != root:
            return False
        # Reject archive tree
        rel = resolved.relative_to(root)
        parts = rel.parts
        if not parts or parts[0] == "archive":
            return False
        return True
    except Exception:  # noqa: BLE001
        return False


def record_skill_use(name: str, *, path: Path | None = None) -> None:
    """Bump load counter for a user-installed skill."""
    if path is not None and not is_user_skill_path(path):
        return
    if path is None:
        candidate = skills_root() / name
        if not (candidate / "SKILL.md").is_file():
            return
        if not is_user_skill_path(candidate):
            return
    data = _load_usage()
    row = data.get(name) or {}
    row["loads"] = int(row.get("loads") or 0) + 1
    row["last_used"] = _now_iso()
    if "created" not in row:
        row["created"] = _now_iso()
    row.setdefault("pinned", bool(row.get("pinned")))
    data[name] = row
    _save_usage(data)


def ensure_created(name: str) -> None:
    data = _load_usage()
    row = data.get(name) or {}
    if "created" not in row:
        row["created"] = _now_iso()
    row.setdefault("loads", 0)
    row.setdefault("pinned", False)
    data[name] = row
    _save_usage(data)


def is_pinned(name: str) -> bool:
    row = _load_usage().get(name) or {}
    return bool(row.get("pinned"))


def set_pinned(name: str, pinned: bool) -> str:
    live = skills_root() / name
    archived = archive_root() / name
    if not (live / "SKILL.md").is_file() and not (archived / "SKILL.md").is_file():
        return f"ERROR: unknown user skill {name}"
    data = _load_usage()
    row = data.get(name) or {"loads": 0, "created": _now_iso()}
    row["pinned"] = bool(pinned)
    data[name] = row
    _save_usage(data)
    return f"{'Pinned' if pinned else 'Unpinned'} skill {name}"


def list_archived() -> list[str]:
    root = archive_root()
    if not root.is_dir():
        return []
    return sorted(
        p.name for p in root.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()
    )


def archive_skill(name: str, *, dry_run: bool = False) -> str:
    if is_pinned(name):
        return f"SKIP: {name} is pinned"
    src = skills_root() / name
    if not (src / "SKILL.md").is_file():
        return f"ERROR: live skill not found: {name}"
    dest = archive_root() / name
    if dry_run:
        return f"DRY-RUN: would archive {name} → {dest}"
    archive_root().mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(src), str(dest))
    return f"Archived skill {name} → {dest}"


def restore_skill(name: str, *, dry_run: bool = False) -> str:
    src = archive_root() / name
    if not (src / "SKILL.md").is_file():
        return f"ERROR: archived skill not found: {name}"
    dest = skills_root() / name
    if dest.exists():
        return f"ERROR: live skill already exists: {name}"
    if dry_run:
        return f"DRY-RUN: would restore {name} → {dest}"
    shutil.move(str(src), str(dest))
    return f"Restored skill {name} → {dest}"


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class SkillUsageRow:
    name: str
    loads: int
    last_used: str
    created: str
    pinned: bool
    stale: bool
    archived: bool


def status_rows(*, stale_days: int = 30) -> list[SkillUsageRow]:
    usage = _load_usage()
    now = datetime.now(timezone.utc)
    rows: list[SkillUsageRow] = []
    live_names = {
        p.name
        for p in skills_root().iterdir()
        if p.is_dir() and p.name != "archive" and (p / "SKILL.md").is_file()
    } if skills_root().is_dir() else set()
    for name in sorted(live_names | set(usage) | set(list_archived())):
        archived = name in list_archived() and name not in live_names
        row = usage.get(name) or {}
        last = str(row.get("last_used") or "")
        created = str(row.get("created") or "")
        loads = int(row.get("loads") or 0)
        pinned = bool(row.get("pinned"))
        stale = False
        if not archived and not pinned:
            ref = _parse_iso(last) or _parse_iso(created)
            if ref is None and name in live_names:
                # Never tracked: use folder mtime
                try:
                    mtime = datetime.fromtimestamp(
                        (skills_root() / name / "SKILL.md").stat().st_mtime,
                        tz=timezone.utc,
                    )
                    ref = mtime
                except OSError:
                    ref = None
            if ref is not None:
                age = (now - ref).days
                stale = age >= stale_days
            elif loads == 0:
                stale = True
        rows.append(
            SkillUsageRow(
                name=name,
                loads=loads,
                last_used=last or "-",
                created=created or "-",
                pinned=pinned,
                stale=stale,
                archived=archived,
            )
        )
    return rows


def run_curator(
    *,
    days: int = 30,
    dry_run: bool = False,
) -> list[str]:
    """Archive stale unpinned user skills. Returns action lines."""
    actions: list[str] = []
    for row in status_rows(stale_days=days):
        if row.archived or row.pinned or not row.stale:
            continue
        actions.append(archive_skill(row.name, dry_run=dry_run))
    if not actions:
        actions.append("(no stale skills to archive)")
    return actions


async def consolidate_skills(*, yes: bool = False) -> str:
    """Optional LLM pass over user skills (capped). Requires --yes for writes."""
    from kageha.config import security_profile
    from kageha.memory.skills import SkillRegistry
    from kageha.runtime import (
        AgentRuntime,
        SecurityProfile,
        TurnRequest,
    )

    reg = SkillRegistry()
    user = [
        s
        for s in reg.skills.values()
        if is_user_skill_path(s.path)
    ]
    if len(user) < 2:
        return "SKIP: need at least 2 user skills to consolidate"
    catalog = "\n".join(f"- {s.name}: {s.description}" for s in user[:40])
    task = (
        "You are the Kageha skill curator. Review these user-created skills and "
        "consolidate overlaps via skill_manage patch/refine (not delete). "
        "Only edit user skills listed below. Be conservative.\n\n"
        f"{catalog}\n\n"
        "If nothing should change, call todo_write with a short note and stop."
    )
    if not yes:
        return (
            "DRY: consolidate would run a capped agent over user skills. "
            "Re-run with --yes and KAGEHA_CURATOR_WRITE=1 to apply (HITL, no auto-approve)."
        )
    if os.environ.get("KAGEHA_CURATOR_WRITE", "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return (
            "SKIP: set KAGEHA_CURATOR_WRITE=1 with --yes to allow skill mutations "
            "(no blanket auto-approve)."
        )
    runtime = AgentRuntime()
    try:
        result = await runtime.execute(
            TurnRequest(
                objective=task,
                auto_approve=False,
                security_profile=SecurityProfile(security_profile()),
                max_steps=8,
                project_root=str(Path.cwd()),
                platform="curator",
                live=False,
                metadata={"model_role": "fast_worker"},
            )
        )
    finally:
        runtime.close()
    return f"consolidate status={result.status} steps={result.steps}: {result.message[:400]}"
