"""Lifecycle hooks (.kageha/hooks.json + ~/.kageha/hooks.json).

Events: preToolUse, postToolUse, beforeShell, afterFileEdit, stop,
preCompact, subagentStart, subagentStop.

Actions: command (shell), http (webhook), or deny (block with message).
Hooks add gates; they never remove hard HITL risk classes.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kageha.project.brain import resolve_project_root

HOOK_EVENTS = frozenset(
    {
        "preToolUse",
        "postToolUse",
        "beforeShell",
        "afterFileEdit",
        "stop",
        "preCompact",
        "subagentStart",
        "subagentStop",
    }
)

# Cursor / Claude Code casing aliases → canonical names.
_EVENT_ALIASES = {
    "PreToolUse": "preToolUse",
    "PostToolUse": "postToolUse",
    "BeforeShell": "beforeShell",
    "BeforeShellExecution": "beforeShell",
    "AfterFileEdit": "afterFileEdit",
    "Stop": "stop",
    "PreCompact": "preCompact",
    "SubagentStart": "subagentStart",
    "SubagentStop": "subagentStop",
}


def normalize_hook_event(raw: str) -> str:
    text = (raw or "").strip()
    if text in HOOK_EVENTS:
        return text
    if text in _EVENT_ALIASES:
        return _EVENT_ALIASES[text]
    # case-insensitive fallback
    lower_map = {k.lower(): v for k, v in _EVENT_ALIASES.items()}
    lower_map.update({e.lower(): e for e in HOOK_EVENTS})
    return lower_map.get(text.lower(), text)


@dataclass
class HookActionResult:
    allowed: bool = True
    message: str = ""
    extra_context: str = ""
    ran: int = 0


@dataclass
class HookSpec:
    event: str
    command: str = ""
    http: str = ""
    deny_message: str = ""
    timeout_s: float = 15.0
    matcher: str = ""  # optional tool-name substring / glob-ish


@dataclass
class HookRunner:
    hooks: list[HookSpec] = field(default_factory=list)
    project_root: Path | None = None

    def for_event(self, event: str) -> list[HookSpec]:
        return [h for h in self.hooks if h.event == event]

    def run(
        self,
        event: str,
        *,
        payload: dict[str, Any] | None = None,
        tool_name: str = "",
    ) -> HookActionResult:
        if event not in HOOK_EVENTS:
            return HookActionResult()
        data = dict(payload or {})
        if tool_name:
            data.setdefault("tool_name", tool_name)
        if self.project_root is not None:
            data.setdefault("project_root", str(self.project_root))
        result = HookActionResult(allowed=True)
        for hook in self.for_event(event):
            if hook.matcher and tool_name and hook.matcher not in tool_name:
                continue
            if hook.deny_message and event in {"preToolUse", "beforeShell"}:
                # Explicit static deny when matcher matched (or no matcher).
                if not hook.command and not hook.http:
                    result.allowed = False
                    result.message = hook.deny_message
                    return result
            out = _run_one(hook, data)
            result.ran += 1
            if out.get("extra_context"):
                result.extra_context = (
                    (result.extra_context + "\n" if result.extra_context else "")
                    + str(out["extra_context"])
                )
            if out.get("allowed") is False:
                result.allowed = False
                result.message = str(
                    out.get("message") or hook.deny_message or f"Blocked by {event} hook"
                )
                return result
            if out.get("message"):
                result.message = str(out["message"])
        return result


def _load_hooks_file(path: Path) -> list[HookSpec]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items: list[Any]
    if isinstance(raw, dict):
        # Shape: {"hooks": {"preToolUse": [...]}} or flat list under "hooks"
        nested = raw.get("hooks")
        if isinstance(nested, dict):
            items = []
            for event, rows in nested.items():
                if not isinstance(rows, list):
                    continue
                canon = normalize_hook_event(str(event))
                for row in rows:
                    if isinstance(row, dict):
                        items.append({**row, "event": canon})
                    elif isinstance(row, str):
                        items.append({"event": canon, "command": row})
        elif isinstance(nested, list):
            items = nested
        else:
            items = []
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    out: list[HookSpec] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        event = normalize_hook_event(str(row.get("event") or ""))
        if event not in HOOK_EVENTS:
            continue
        out.append(
            HookSpec(
                event=event,
                command=str(row.get("command") or row.get("script") or ""),
                http=str(row.get("http") or row.get("url") or ""),
                deny_message=str(row.get("deny_message") or row.get("deny") or ""),
                timeout_s=float(row.get("timeout_s") or row.get("timeout") or 15),
                matcher=str(row.get("matcher") or row.get("tool") or ""),
            )
        )
    return out


def load_hook_runner(project_root: str | Path | None = None) -> HookRunner:
    from kageha.config import kageha_home

    hooks: list[HookSpec] = []
    hooks.extend(_load_hooks_file(kageha_home() / "hooks.json"))
    root = resolve_project_root(project_root)
    if root is not None:
        hooks.extend(_load_hooks_file(root / ".kageha" / "hooks.json"))
    return HookRunner(hooks=hooks, project_root=root)


def _run_one(hook: HookSpec, payload: dict[str, Any]) -> dict[str, Any]:
    env = os.environ.copy()
    env["KAGEHA_HOOK_EVENT"] = hook.event
    env["KAGEHA_HOOK_PAYLOAD"] = json.dumps(payload, default=str)[:100_000]
    if payload.get("project_root"):
        env["KAGEHA_PROJECT_ROOT"] = str(payload["project_root"])
    cwd = str(payload.get("project_root") or Path.cwd())
    timeout = max(1.0, min(float(hook.timeout_s), 120.0))

    if hook.command:
        try:
            proc = subprocess.run(
                hook.command,
                shell=True,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "allowed": False,
                "message": f"Hook timed out after {timeout:.0f}s: {hook.command[:80]}",
            }
        except OSError as exc:
            return {"allowed": False, "message": f"Hook failed: {exc}"}
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        # Exit 2 = hard block (Claude Code convention); other non-zero = soft warn.
        if proc.returncode == 2:
            return {
                "allowed": False,
                "message": stdout or stderr or hook.deny_message or "Blocked by hook",
            }
        extra = stdout
        if proc.returncode != 0 and stderr:
            extra = (extra + "\n" if extra else "") + f"[hook warn] {stderr[:500]}"
        out: dict[str, Any] = {"allowed": True}
        if extra:
            # If stdout is JSON with allowed/message, honor it.
            try:
                parsed = json.loads(stdout)
                if isinstance(parsed, dict):
                    return {
                        "allowed": parsed.get("allowed", True) is not False,
                        "message": str(parsed.get("message") or ""),
                        "extra_context": str(parsed.get("extra_context") or ""),
                    }
            except json.JSONDecodeError:
                pass
            out["extra_context"] = extra[:4000]
        return out

    if hook.http:
        body = json.dumps({"event": hook.event, **payload}, default=str).encode("utf-8")
        req = urllib.request.Request(
            hook.http,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {"allowed": False, "message": f"Hook HTTP failed: {exc}"}
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return {"allowed": True, "extra_context": raw[:2000]}
        if isinstance(parsed, dict):
            return {
                "allowed": parsed.get("allowed", True) is not False,
                "message": str(parsed.get("message") or ""),
                "extra_context": str(parsed.get("extra_context") or ""),
            }
        return {"allowed": True}

    return {"allowed": True}
