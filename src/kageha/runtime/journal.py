"""Adapter that journals tool execution into RuntimeStore."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from kageha.harness.tool_deadlines import tool_deadline_s
from kageha.runtime.store import RuntimeStore
from kageha.runtime.types import RunEventKind, ToolReconciliation


_READ_PREFIXES = (
    "read",
    "list",
    "search",
    "get",
    "inspect",
    "snapshot",
    "status",
    "memory_recall",
    "memory_inspect",
    "memory_explain",
    "pdf_extract",
    "pdf_meta",
)

_ARGS_PREVIEW_KEYS = (
    "path",
    "command",
    "query",
    "app",
    "text",
    "url",
    "name",
    "ref",
    "refs",
    "labels",
    "file",
    "prompt",
    "to",
    "action",
)
_ARTIFACT_PATH_KEYS = (
    "path",
    "screenshot",
    "thumb",
    "thumb_path",
    "file",
    "output",
    "dest",
)
_ARTIFACT_LIST_KEYS = ("artifacts", "files", "paths", "artifact_refs")
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
_ARTIFACT_PATH_RE = re.compile(
    r"(?:artifacts|outputs|diagrams|carousel|research|slides)/[^\s\"']+",
    re.I,
)


def classify_side_effect(tool_name: str, risk_class: str) -> str:
    name = (tool_name or "").lower()
    if name.startswith(_READ_PREFIXES):
        return "read"
    if risk_class in {"safe"} and name in {"web_search", "parallel_web_search"}:
        return "read"
    if risk_class in {"messaging", "computer_input", "memory_mutation"}:
        return "external_mutation"
    return "mutation"


def _clip_preview(text: str, *, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


def args_preview(arguments: dict[str, Any] | None, *, limit: int = 120) -> str:
    """One-line tool args for WebUI tool cards (never dump full payloads)."""
    data = arguments if isinstance(arguments, dict) else {}
    if not data:
        return ""
    parts: list[str] = []
    used: set[str] = set()

    def _add(key: str, raw: Any) -> None:
        if key in used or raw in (None, "", [], {}):
            return
        if isinstance(raw, (list, dict)):
            rendered = json.dumps(raw, default=str, ensure_ascii=False)
        else:
            rendered = str(raw)
        rendered = _clip_preview(rendered, limit=56)
        if not rendered:
            return
        parts.append(f"{key}={rendered}")
        used.add(key)

    for key in _ARGS_PREVIEW_KEYS:
        if key in data:
            _add(key, data.get(key))
        if len(parts) >= 3:
            break
    if not parts:
        for key, value in list(data.items())[:3]:
            _add(str(key), value)
            if len(parts) >= 3:
                break
    return _clip_preview(", ".join(parts), limit=limit)


def _looks_like_artifact_path(value: str) -> bool:
    rel = str(value or "").replace("\\", "/").strip()
    if not rel or len(rel) > 260:
        return False
    if rel.startswith(("artifacts/", "outputs/", "diagrams/", "carousel/", "research/", "slides/")):
        return True
    return bool(_ARTIFACT_PATH_RE.search(rel))


def artifact_refs_from_result(result: str | None, *, limit: int = 8) -> list[str]:
    """Extract compact artifact path refs from a tool result string."""
    refs: list[str] = []
    text = str(result or "")
    if not text:
        return refs

    def _push(raw: Any) -> None:
        if not isinstance(raw, str):
            return
        rel = raw.replace("\\", "/").strip()
        if _looks_like_artifact_path(rel) and rel not in refs:
            refs.append(rel)

    try:
        detail = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        detail = None
    if isinstance(detail, dict):
        for key in _ARTIFACT_PATH_KEYS:
            _push(detail.get(key))
        for key in _ARTIFACT_LIST_KEYS:
            rows = detail.get(key)
            if isinstance(rows, list):
                for item in rows[:limit]:
                    _push(item)
            elif isinstance(rows, str):
                _push(rows)
    for match in _ARTIFACT_PATH_RE.findall(text):
        _push(match)
        if len(refs) >= limit:
            break
    return refs[:limit]


def tool_status_from_result(result: str | None, state: str | None = None) -> str:
    text = str(result or "")
    st = str(state or "").strip().lower()
    if text.startswith("DENIED:"):
        return "denied"
    if text.startswith("ERROR") or st in {"failed", "uncertain"}:
        return "error"
    if st in {"completed", "ok", "success"} or text:
        return "ok"
    return "running"


def computer_frame_from_result(
    tool_name: str,
    result: str | None,
    *,
    artifact_refs: list[str] | None = None,
) -> dict[str, Any] | None:
    """Build a computer observer frame ref (path only — never AX dumps or base64).

    Only returns a frame when the tool result already references an image artifact
    (e.g. computer_screenshot / get_state with include_screenshot). Clicks never
    invent screenshots.
    """
    name = str(tool_name or "")
    if not name.startswith("computer_"):
        return None
    refs = list(artifact_refs or artifact_refs_from_result(result))
    image_refs = [rel for rel in refs if rel.lower().endswith(_IMAGE_SUFFIXES)]
    if not image_refs:
        return None
    thumb = next(
        (rel for rel in image_refs if "/thumbs/" in rel or "thumb" in rel.lower()),
        "",
    )
    path = image_refs[0]
    app = ""
    try:
        detail = json.loads(str(result or ""))
    except (TypeError, json.JSONDecodeError):
        detail = None
    if isinstance(detail, dict):
        app = str(detail.get("app") or detail.get("bundle_id") or "").strip()
        for key in ("thumb", "thumb_path"):
            candidate = detail.get(key)
            if isinstance(candidate, str) and _looks_like_artifact_path(candidate):
                thumb = candidate.replace("\\", "/")
                break
        shot = detail.get("screenshot") or detail.get("path")
        if isinstance(shot, str) and _looks_like_artifact_path(shot):
            path = shot.replace("\\", "/")
    # Prefer thumb for strip display; full shot remains in artifact_refs.
    display = thumb or path
    return {
        "path": display,
        "thumb_path": thumb or "",
        "app": app,
        "action": name,
    }


class ToolJournal:
    def __init__(
        self,
        store: RuntimeStore,
        *,
        session_id: str,
        turn_id: str,
        timeout_s: float = 120.0,
    ) -> None:
        self.store = store
        self.session_id = session_id
        self.turn_id = turn_id
        self.timeout_s = timeout_s

    def before(
        self,
        *,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        risk_class: str,
        policy_grant: str = "",
    ) -> tuple[str, str | None]:
        side_effect = classify_side_effect(tool_name, risk_class)
        if policy_grant:
            try:
                grant = json.loads(policy_grant)
            except json.JSONDecodeError:
                grant = {}
            if isinstance(grant, dict) and grant.get("sandboxed") is False:
                self.store.mark_session_unsandboxed(
                    self.session_id,
                    tool_name=tool_name,
                    reason=str(grant.get("reason") or "approval fallback"),
                )
        attempt, created = self.store.begin_tool_attempt(
            session_id=self.session_id,
            turn_id=self.turn_id,
            tool_call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
            side_effect=side_effect,
            risk_class=risk_class,
            policy_grant=policy_grant,
            deadline_at=time.time() + tool_deadline_s(tool_name, self.timeout_s),
        )
        if not created:
            if attempt.state == ToolReconciliation.COMPLETED:
                return attempt.id, attempt.result
            if attempt.state == ToolReconciliation.UNCERTAIN:
                return (
                    attempt.id,
                    "DENIED: prior mutating tool attempt has an uncertain outcome; "
                    "reconcile it before retrying",
                )
            if attempt.state == ToolReconciliation.IN_PROGRESS and side_effect != "read":
                return (
                    attempt.id,
                    "DENIED: matching mutating tool call is already in progress",
                )
            if (
                attempt.state == ToolReconciliation.FAILED
                and side_effect == "external_mutation"
            ):
                return (
                    attempt.id,
                    "DENIED: prior external mutation failed with an unknown side-effect "
                    "boundary; reconcile before retrying",
                )
        preview = args_preview(arguments)
        self.store.append_event(
            session_id=self.session_id,
            turn_id=self.turn_id,
            kind=RunEventKind.TOOL_STARTED,
            payload={
                "attempt_id": attempt.id,
                "tool": tool_name,
                "side_effect": side_effect,
                "policy_grant": policy_grant,
                "args_preview": preview,
                "status": "running",
                # tool_card-friendly shape (WS3 consumes; bulky args stay in store)
                "tool_card": {
                    "name": tool_name,
                    "args_preview": preview,
                    "status": "running",
                    "duration_ms": None,
                    "artifact_refs": [],
                    "attempt_id": attempt.id,
                },
            },
            idempotency_key=f"tool-start:{attempt.id}",
        )
        return attempt.id, None

    def after(self, attempt_id: str, result: str) -> None:
        completed = self.store.complete_tool_attempt(attempt_id, result=result)
        try:
            detail = json.loads(result)
        except (TypeError, json.JSONDecodeError):
            detail = {}
        if isinstance(detail, dict) and detail.get("sandboxed") is False:
            self.store.mark_session_unsandboxed(
                self.session_id,
                tool_name=completed.tool_name,
                reason=(
                    f"security_profile={detail.get('security_profile') or 'unknown'}"
                ),
            )
        args = self.store.tool_attempt_arguments(attempt_id)
        preview = args_preview(args)
        status = tool_status_from_result(result, completed.state.value)
        duration_ms = max(
            0.0,
            round((float(completed.updated_at) - float(completed.created_at)) * 1000.0, 1),
        )
        refs = artifact_refs_from_result(result)
        frame = computer_frame_from_result(
            completed.tool_name, result, artifact_refs=refs
        )
        payload: dict[str, Any] = {
            "attempt_id": attempt_id,
            "tool": completed.tool_name,
            "state": completed.state.value,
            "args_preview": preview,
            "status": status,
            "duration_ms": duration_ms,
            "artifact_refs": refs,
            "tool_card": {
                "name": completed.tool_name,
                "args_preview": preview,
                "status": status,
                "duration_ms": duration_ms,
                "artifact_refs": refs,
                "attempt_id": attempt_id,
            },
        }
        if frame is not None:
            payload["computer_frame"] = frame
        self.store.append_event(
            session_id=self.session_id,
            turn_id=self.turn_id,
            kind=RunEventKind.TOOL_COMPLETED,
            payload=payload,
            idempotency_key=f"tool-complete:{attempt_id}",
        )
