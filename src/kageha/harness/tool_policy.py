"""OpenClaw-style tools.allow / tools.deny with group expansion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from kageha.config import tools_policy_paths

TOOL_GROUPS: dict[str, frozenset[str]] = {
    "group:fs": frozenset(
        {"read_file", "write_file", "list_dir", "edit_file", "list_files", "apply_patch"}
    ),
    "group:runtime": frozenset(
        {
            "bash",
            "shell",
            "run_shell",
            "todo_write",
            "todo_read",
            "escalate_plan",
            "request_approval",
            "ask_human",
            "forge_tool",
            "install_python_packages",
        }
    ),
    "group:web": frozenset(
        {
            "web_search",
            "parallel_web_search",
            "web_fetch",
            "download_file",
            "http_get",
            "research_run",
            "parallel_web_fetch",
            "headless_fetch",
        }
    ),
    "group:browser": frozenset(
        {
            "browser_connect",
            "browser_open",
            "browser_navigate",
            "browser_click",
            "browser_type",
            "browser_fill",
            "browser_press",
            "browser_scroll",
            "browser_wait",
            "browser_snapshot",
            "browser_screenshot",
            "browser_evaluate",
            "browser_cdp",
            "browser_tabs",
            "browser_lock",
            "browser_close",
            "browser_console",
            "browser_get_images",
            "browse",
            "extract",
            "screenshot",
        }
    ),
    "group:memory": frozenset(
        {
            "memory_recall",
            "memory_fetch",
            "memory_inspect",
            "memory_remember",
            "memory_correct",
            "memory_forget",
            "memory_explain",
            "memory_forgotten",
            "session_search",
        }
    ),
    "group:messaging": frozenset({"ask_human", "request_approval"}),
    "group:media": frozenset(
        {
            "nano_banana_generate",
            "nano_banana_edit",
            "gemini_tts",
            "fal_generate_image",
            "fal_edit_image",
            "fal_image_to_video",
            "fal_text_to_video",
        }
    ),
    "group:mcp": frozenset(
        {
            "mcp_list_servers",
            "mcp_call",
            "mcp_read_resource",
            "mcp_list_prompts",
            "mcp_get_prompt",
            "mcp_reload",
            "mcp_protocol_status",
        }
    ),
    "group:skills": frozenset(
        {
            "skill_list",
            "skill_load",
            "skill_list_resources",
            "skill_read",
            "skill_run",
            "skill_manage",
            "skill_install",
            "skill_validate",
        }
    ),
    "group:subagent": frozenset({"spawn_subagent", "spawn_subagents", "spawn_task_graph"}),
    "group:computer": frozenset(
        {
            "computer_doctor",
            "computer_launch",
            "computer_wait",
            "computer_list_apps",
            "computer_get_state",
            "computer_click",
            "computer_click_sequence",
            "computer_set_value",
            "computer_type",
            "computer_key",
            "computer_hotkey",
            "computer_scroll",
            "computer_screenshot",
            "computer_move",
        }
    ),
    "group:pdf": frozenset({"pdf_extract", "pdf_meta"}),
    "group:diagram": frozenset(
        {"render_diagram", "write_diagram_source", "choose_diagram_mode"}
    ),
    "group:connections": frozenset(
        {
            "connections_status",
            "gmail_list",
            "gmail_send",
            "gcal_list_events",
            "gdrive_list_files",
            "github_api",
        }
    ),
}


def _as_tools_section(chunk: dict[str, Any]) -> dict[str, Any]:
    if isinstance(chunk.get("tools"), dict):
        return dict(chunk["tools"])
    return dict(chunk)


def _merge_policy(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge tools policies. Later files win for scalars; allow/deny/packs union.

    Empty allow/deny/packs in an overlay are ignored so a repo stub cannot wipe
    a real home/project list. ``packs: all`` in any layer wins as ``all``.
    """
    out = dict(base)
    for key, value in overlay.items():
        if key in {"allow", "deny", "packs"}:
            if value in (None, [], ""):
                continue
            if key == "packs" and (
                value == "all"
                or (
                    isinstance(value, list)
                    and any(str(v).strip().lower() in {"all", "*"} for v in value)
                )
            ):
                out[key] = ["all"]
                continue
            prev = list(out.get(key) or [])
            if key == "packs" and prev == ["all"]:
                continue
            # Preserve order, unique.
            merged: list[Any] = []
            for item in prev + list(value if isinstance(value, list) else [value]):
                if item not in merged:
                    merged.append(item)
            out[key] = merged
        else:
            out[key] = value
    return out


def load_tools_policy(path: Path | None = None) -> dict[str, Any]:
    paths = [path] if path else tools_policy_paths()
    policy: dict[str, Any] = {}
    for p in paths:
        if p and p.is_file():
            try:
                chunk = yaml.safe_load(p.read_text()) or {}
            except Exception:  # noqa: BLE001
                continue
            if isinstance(chunk, dict):
                policy = _merge_policy(policy, _as_tools_section(chunk))
    return policy


def expand_policy_entry(entry: str) -> set[str]:
    raw = (entry or "").strip()
    if not raw:
        return set()
    if raw.startswith("group:"):
        return set(TOOL_GROUPS.get(raw, frozenset()))
    return {raw}


def _expand_list(entries: list[Any] | None) -> set[str]:
    out: set[str] = set()
    for entry in entries or []:
        out |= expand_policy_entry(str(entry))
    return out


def tool_denied(name: str, *, policy: dict[str, Any] | None = None) -> bool:
    """True when deny wins or name is outside a non-empty allow list."""
    pol = policy if policy is not None else load_tools_policy()
    deny = _expand_list(list(pol.get("deny") or []))
    if name in deny:
        return True
    # Prefix deny for dynamic mcp_/forged_ tools
    for d in deny:
        if d.endswith("_*") and name.startswith(d[:-1]):
            return True
    allow = _expand_list(list(pol.get("allow") or []))
    if allow and name not in allow:
        # Allow dynamic mcp_ / forged_ when group:mcp or forge_tool implied
        if name.startswith("mcp_") and (
            "mcp_list_servers" in allow or any(a.startswith("mcp_") for a in allow)
        ):
            return False
        if name.startswith("forged_") and "forge_tool" in allow:
            return False
        return True
    return False


def filter_tool_names(
    names: list[str] | set[str],
    *,
    policy: dict[str, Any] | None = None,
) -> list[str]:
    pol = policy if policy is not None else load_tools_policy()
    return [n for n in names if not tool_denied(n, policy=pol)]
