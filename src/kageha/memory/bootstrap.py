"""Turn-start memory bootstrap: hash-gated rule sync + recall digest."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kageha.memory.import_rules import (
    auto_sync_rules_enabled,
    discover_rule_files,
    rule_files_fingerprint,
)
from kageha.memory.models import MemoryQuery
from kageha.memory.util import memory_enabled, project_key

# Re-export for callers/tests.
__all__ = [
    "maybe_sync_project_rules",
    "prepare_turn_memory",
]


def maybe_sync_project_rules(
    service: Any,
    project_root: str,
    *,
    session_id: str = "",
    user_id: str = "local",
    agent_id: str = "main",
    channel_key: str = "",
    force: bool = False,
) -> dict[str, Any] | None:
    """Import/sync AGENTS.md / CLAUDE.md / .cursor/rules when fingerprint changes."""
    if not memory_enabled() or not auto_sync_rules_enabled():
        return None
    root = str(Path(project_root or "").expanduser().resolve()) if project_root else ""
    if not root or not Path(root).is_dir():
        return None
    files = discover_rule_files(root)
    fingerprint = rule_files_fingerprint(root)
    if not files and not fingerprint:
        # No rule files and nothing previously tracked → idle.
        if not force and not _prior_import_for_project(service, root):
            return None

    if not force:
        prior = _prior_import_for_project(service, root)
        if prior and str(prior.get("fingerprint") or "") == fingerprint:
            return {
                "skipped": True,
                "reason": "fingerprint_match",
                "project_root": root,
                "fingerprint": fingerprint,
            }

    try:
        report = service.import_project_rules(
            root,
            session_id=session_id or "auto-sync-rules",
            user_id=user_id or "local",
            agent_id=agent_id or "main",
            channel_key=channel_key,
            sync=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {"skipped": True, "reason": "import_failed", "error": str(exc)}
    report = dict(report)
    report["auto"] = True
    return report


def prepare_turn_memory(
    service: Any,
    *,
    query: str,
    project_root: str = "",
    session_id: str = "",
    user_id: str = "local",
    agent_id: str = "main",
    channel_key: str = "",
    trace_root: str = "",
    max_results: int | None = None,
    sync_rules: bool = True,
) -> str:
    """Hash-gated rule sync (optional) + compact recall digest for system_extra."""
    if sync_rules and project_root:
        maybe_sync_project_rules(
            service,
            project_root,
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            channel_key=channel_key,
        )
    if not memory_enabled():
        return ""
    q = MemoryQuery(
        query=query,
        project_root=project_root,
        session_id=session_id,
        user_id=user_id,
        agent_id=agent_id,
        channel_key=channel_key,
        trace_root=trace_root,
    )
    if max_results is not None:
        q.max_results = max_results
    return service.recall(q).render()


def _prior_import_for_project(service: Any, project_root: str) -> dict[str, Any] | None:
    root = str(Path(project_root).expanduser().resolve())
    key = project_key(root)
    for event in service.store.list_events("import_rules", limit=50):
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if str(payload.get("project_root") or "") == root:
            return payload
        if str(payload.get("project_key") or "") == key:
            return payload
    return None
