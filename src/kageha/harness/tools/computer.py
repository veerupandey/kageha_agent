"""macOS computer-use tools — AX/SOM via cua-driver, HITL + per-app allowlist.

Prefer browser_* for websites. Pixel/PyAutoGUI paths are degraded fallback only.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from kageha.harness.approvals import ApprovalDecision, ApprovalRequest
from kageha.harness.tools.base import ToolRegistry, tool
from kageha.harness.tools import computer_allowlist as allowlist
from kageha.harness.tools import computer_driver as driver

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext

_BLOCKED_HOTKEYS = re.compile(
    r"(command\s*\+\s*option\s*\+\s*escape|ctrl\s*\+\s*alt\s*\+\s*del|"
    r"force.?quit|cmd\s*\+\s*q\b|command\s*\+\s*q\b)",
    re.I,
)

# WebUI computer_frame observer — small thumbs only (not high-FPS video).
_COMPUTER_THUMB_MAX = (480, 270)


def maybe_write_computer_thumb(src: Path, dest: Path) -> bool:
    """Write a small JPEG thumb for WebUI computer_frame. Best-effort; never raises."""
    try:
        if not src.is_file():
            return False
        from PIL import Image

        dest.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as image:
            frame = image.convert("RGB")
            frame.thumbnail(_COMPUTER_THUMB_MAX)
            frame.save(dest, format="JPEG", quality=70, optimize=True)
        return dest.is_file()
    except Exception:  # noqa: BLE001
        return False


def _require_macos() -> str | None:
    return driver.require_macos()


def _require_pyautogui():
    try:
        import pyautogui  # type: ignore

        pyautogui.FAILSAFE = True
        return pyautogui
    except ImportError as e:
        raise ImportError(
            "Pixel fallback needs Computer extra. Run: uv sync --extra computer "
            "and grant Accessibility + Screen Recording. Prefer cua-driver AX path."
        ) from e


def _run_input_action(action: Callable[[], None]) -> str | None:
    try:
        action()
    except Exception as exc:  # noqa: BLE001
        return (
            "ERROR: computer input failed "
            f"({type(exc).__name__}): {str(exc)[:300]}"
        )
    return None


def _slim_driver_result(result: Any) -> dict[str, Any]:
    """Keep action confirmations tiny for model/UI loops (no AX dumps)."""
    if not isinstance(result, dict):
        return {"ok": True}
    out: dict[str, Any] = {}
    for key in ("ok", "verified", "effect", "element_index", "error", "text", "path"):
        if key in result and result[key] not in (None, ""):
            val = result[key]
            out[key] = val[:120] if isinstance(val, str) else val
    esc = result.get("escalation")
    if isinstance(esc, dict) and esc.get("recommended"):
        out["escalation"] = {"recommended": str(esc["recommended"])[:40]}
    return out or {"ok": True}


def _driver_unverifiable(result: Any) -> bool:
    """True when cua-driver could not confirm the input landed (common in Electron)."""
    if not isinstance(result, dict):
        return False
    if str(result.get("effect") or "").lower() == "unverifiable":
        return True
    if result.get("verified") is False:
        return True
    return False


async def _call_input_with_foreground_retry(
    method: str, args: dict[str, Any], *, ensure: bool = True
) -> Any:
    """Background first; on unverifiable, escalate to foreground once (driver hint)."""
    result = await driver.call(method, args, ensure=ensure)
    if not _driver_unverifiable(result):
        return result
    if str(args.get("delivery_mode") or "") == "foreground":
        return result
    retry = dict(args)
    retry["delivery_mode"] = "foreground"
    try:
        return await driver.call(method, retry, ensure=ensure)
    except driver.ComputerDriverError:
        return result


def _unverifiable_input_error(action: str, result: Any) -> str:
    slim = _slim_driver_result(result) if isinstance(result, dict) else {}
    return (
        f"ERROR: {action} effect unverifiable — input may not have landed "
        "(common in Electron/web apps like Codex/ChatGPT). "
        "Click the composer/input ref first, retry typing, or tell the user "
        f"AX insert is blocked. driver={json.dumps(slim, separators=(',', ':'))}"
    )


def _cap_snapshot(snap: str, *, limit: int = 2800) -> str:
    text = snap or ""
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "\n…[snapshot truncated]"


_KEYPAD_LABEL_CHARS: dict[str, str] = {
    "0": "0",
    "1": "1",
    "2": "2",
    "3": "3",
    "4": "4",
    "5": "5",
    "6": "6",
    "7": "7",
    "8": "8",
    "9": "9",
    "+": "+",
    "add": "+",
    "plus": "+",
    "-": "-",
    "subtract": "-",
    "minus": "-",
    "*": "*",
    "×": "*",
    "multiply": "*",
    "multiplication": "*",
    "/": "/",
    "÷": "/",
    "divide": "/",
    "division": "/",
    "=": "=",
    "equals": "=",
    "equal": "=",
    ".": ".",
    "decimal": ".",
    "dot": ".",
    "point": ".",
    "%": "%",
    "percent": "%",
    # Clear starts a fresh expression — omit from typed payload.
    "c": "",
    "ac": "",
    "clear": "",
    "all clear": "",
    "allclear": "",
}


def labels_to_keypad_text(labels: list[str]) -> str | None:
    """Adaptive chunk: map calculator-like label sequences → type_text payload.

    Research (OSWorld-Human): grouping micro-clicks into one action cuts LLM +
    AX round-trips. Returns None when any label is not a keypad token.
    """
    if not labels:
        return None
    chars: list[str] = []
    for raw in labels:
        key = re.sub(r"\s+", " ", (raw or "").strip().lower())
        if key not in _KEYPAD_LABEL_CHARS:
            # Allow single-char operators already normalized.
            if len(key) == 1 and key in _KEYPAD_LABEL_CHARS:
                chars.append(_KEYPAD_LABEL_CHARS[key])
                continue
            return None
        chars.append(_KEYPAD_LABEL_CHARS[key])
    text = "".join(chars)
    return text or None


def best_window_id(windows: list[Any]) -> int | None:
    """Prefer on-screen, largest real window (skip menubar-sized stubs)."""
    scored: list[tuple[tuple[int, float, int], int]] = []
    for w in windows or []:
        if not isinstance(w, dict) or w.get("window_id") is None:
            continue
        try:
            wid = int(w["window_id"])
        except (TypeError, ValueError):
            continue
        bounds = w.get("bounds") if isinstance(w.get("bounds"), dict) else {}
        try:
            area = float(bounds.get("width") or 0) * float(bounds.get("height") or 0)
        except (TypeError, ValueError):
            area = 0.0
        on = 1 if w.get("is_on_screen") else 0
        # Tiny strips (menu bar / title stubs) lose to real app windows.
        scored.append(((on, area, int(w.get("z_index") or 0)), wid))
    if not scored:
        return None
    scored.sort(key=lambda row: row[0], reverse=True)
    return scored[0][1]


def register_computer_tools(ctx: "HarnessContext") -> ToolRegistry:
    reg = ToolRegistry()
    gate = ctx.approvals
    # Session targeting: last get_state binds app/pid/window + ref map.
    state: dict[str, Any] = {
        "app": None,
        "bundle_id": None,
        "pid": None,
        "window_id": None,
        "ref_map": {},
        "frontmost_before": None,
    }

    async def _approve_app_input(action: str, detail: str) -> str | None:
        bid = str(state.get("bundle_id") or "")
        name = str(state.get("app") or "")
        blocked = allowlist.is_blocked_app(bundle_id=bid, name=name)
        if blocked:
            return f"DENIED: {blocked}"
        decision = allowlist.get_decision(bid) if bid else None
        if decision == "deny":
            return f"DENIED: app {bid or name} is deny-listed"
        if decision == "always":
            return None
        if decision == "once":
            allowlist.consume_once(bid)
            return None
        ok = await gate.require(
            ApprovalRequest(
                action=f"computer_app:{bid or name or 'unknown'}",
                detail=(
                    f"{action} on {name or bid or 'app'}: {detail}\n"
                    "(Approve once this session; app will be always-allowed after yes.)"
                ),
                risk_class="computer_input",
                default=ApprovalDecision.ASK,
            )
        )
        if not ok:
            return gate.denial_message(action)
        # Persist always-allow only after a real HITL approve (not --auto-approve).
        if bid and not gate.auto_approve:
            allowlist.set_decision(bid, "always", name=name)
        return None

    async def _resolve_app(
        app: str,
        *,
        launch_if_needed: bool = True,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Return (app_record, error)."""
        target = (app or "").strip()
        if not target:
            return None, "ERROR: app name or bundle_id required"
        try:
            data = await driver.call("list_apps", {})
        except driver.ComputerDriverError as exc:
            return None, f"ERROR: {exc}"
        apps = data.get("apps") if isinstance(data.get("apps"), list) else []
        target_l = target.lower()
        match: dict[str, Any] | None = None
        for row in apps:
            if not isinstance(row, dict):
                continue
            bid = str(row.get("bundle_id") or "")
            name = str(row.get("name") or "")
            if bid == target or name == target:
                match = row
                break
            if target_l in bid.lower() or target_l in name.lower():
                match = row
                break
        if match is None and launch_if_needed:
            try:
                launched = await driver.call(
                    "launch_app",
                    (
                        {"bundle_id": target}
                        if "." in target
                        else {"name": target}
                    ),
                )
            except driver.ComputerDriverError as exc:
                return None, f"ERROR: launch_app failed: {exc}"
            match = {
                "name": launched.get("name") or target,
                "bundle_id": launched.get("bundle_id") or target,
                "pid": launched.get("pid"),
                "running": True,
                "windows": launched.get("windows") or [],
            }
        if match is None:
            return None, f"ERROR: app not found: {target}"
        blocked = allowlist.is_blocked_app(
            bundle_id=str(match.get("bundle_id") or ""),
            name=str(match.get("name") or ""),
        )
        if blocked:
            return None, f"DENIED: {blocked}"
        return match, None

    async def _pick_window(pid: int) -> tuple[int | None, str | None]:
        try:
            data = await driver.call("list_windows", {"pid": int(pid)})
        except driver.ComputerDriverError as exc:
            return None, f"ERROR: list_windows failed: {exc}"
        windows = data.get("windows") if isinstance(data.get("windows"), list) else []
        if not windows:
            return None, f"ERROR: no windows for pid={pid}"
        wid = best_window_id(windows)
        if wid is None:
            return None, f"ERROR: no usable window for pid={pid}"
        return wid, None

    async def _frontmost_name() -> str | None:
        try:
            data = await driver.call("list_apps", {})
        except driver.ComputerDriverError:
            return None
        for row in data.get("apps") or []:
            if isinstance(row, dict) and row.get("active"):
                return str(row.get("name") or row.get("bundle_id") or "")
        return None

    @tool(
        description=(
            "Diagnose computer-use readiness: cua-driver binary, daemon, "
            "Accessibility/Screen Recording permissions."
        )
    )
    async def computer_doctor() -> str:
        err = _require_macos()
        if err:
            return err
        binary = driver.driver_bin()
        out: dict[str, Any] = {
            "driver_bin": binary,
            "available": bool(binary),
        }
        if not binary:
            out["hint"] = "Run scripts/install_computer_driver.sh"
            return json.dumps(out)
        try:
            await driver.ensure_daemon(timeout=6.0)
            out["daemon"] = "running"
        except driver.ComputerDriverError as exc:
            out["daemon"] = f"error: {exc}"
        perms = await driver.permissions_status()
        out["permissions"] = perms
        out["ready"] = bool(
            perms.get("accessibility") and perms.get("screen_recording")
        )
        if not out["ready"]:
            out["hint"] = "cua-driver permissions grant"
        return json.dumps(out)

    @tool(
        description=(
            "Launch a macOS app in the background (no focus steal). "
            "Prefer bundle_id (com.apple.calculator) or name (Calculator)."
        ),
        risk_class="computer_input",
    )
    async def computer_launch(app: str) -> str:
        err = _require_macos()
        if err:
            return err
        target = (app or "").strip()
        if not target:
            return "ERROR: app name or bundle_id required"
        # Pre-bind identity for allowlist before launch when possible.
        match, fail = await _resolve_app(target, launch_if_needed=False)
        if match:
            state["app"] = match.get("name") or target
            state["bundle_id"] = match.get("bundle_id")
        else:
            state["app"] = target
            state["bundle_id"] = target if "." in target else None
        denied = await _approve_app_input("computer_launch", f"launch {target}")
        if denied:
            return denied
        try:
            launched = await driver.call(
                "launch_app",
                {"bundle_id": target} if "." in target else {"name": target},
            )
        except driver.ComputerDriverError as exc:
            return f"ERROR: {exc}"
        pid = int(launched.get("pid") or 0)
        wins = launched.get("windows") or []
        wid = None
        if wins and isinstance(wins[0], dict) and wins[0].get("window_id") is not None:
            wid = int(wins[0]["window_id"])
        state.update(
            {
                "app": launched.get("name") or target,
                "bundle_id": launched.get("bundle_id") or state.get("bundle_id"),
                "pid": pid or state.get("pid"),
                "window_id": wid or state.get("window_id"),
            }
        )
        return json.dumps(
            {
                "ok": True,
                "app": state["app"],
                "bundle_id": state["bundle_id"],
                "pid": state["pid"],
                "window_id": state["window_id"],
                "self_activation_suppressed": launched.get(
                    "self_activation_suppressed"
                ),
                "next": "Call computer_get_state(app=…) before clicking.",
            }
        )

    @tool(description="Wait for UI to settle (milliseconds). Use after launch/click.")
    async def computer_wait(ms: int = 400) -> str:
        err = _require_macos()
        if err:
            return err
        delay = max(0, min(10_000, int(ms))) / 1000.0
        await asyncio.sleep(delay)
        return json.dumps({"ok": True, "waited_ms": int(delay * 1000)})

    @tool(description="List macOS apps (running + installed) via cua-driver.")
    async def computer_list_apps(running_only: bool = True) -> str:
        err = _require_macos()
        if err:
            return err
        try:
            data = await driver.call("list_apps", {})
        except driver.ComputerDriverError as exc:
            return f"ERROR: {exc}"
        apps = data.get("apps") if isinstance(data.get("apps"), list) else []
        rows = []
        for row in apps:
            if not isinstance(row, dict):
                continue
            if running_only and not row.get("running"):
                continue
            rows.append(
                {
                    "name": row.get("name"),
                    "bundle_id": row.get("bundle_id"),
                    "pid": row.get("pid"),
                    "running": bool(row.get("running")),
                    "active": bool(row.get("active")),
                }
            )
        return json.dumps({"apps": rows, "count": len(rows)})

    async def _peek_readings(
        *, max_elements: int = 32
    ) -> tuple[list[dict[str, Any]], float]:
        """Cheap post-action readings (no screenshot). Returns (readings, peek_ms)."""
        import time as _time

        pid = int(state.get("pid") or 0)
        window_id = state.get("window_id")
        if pid <= 0 or window_id is None:
            return [], 0.0
        t0 = _time.perf_counter()
        try:
            data = await driver.call(
                "get_window_state",
                {
                    "pid": pid,
                    "window_id": int(window_id),
                    "include_screenshot": False,
                    "max_elements": max(1, min(80, int(max_elements))),
                },
                ensure=False,
                timeout=12.0,
            )
        except driver.ComputerDriverError:
            return [], (_time.perf_counter() - t0) * 1000.0
        elements = data.get("elements") if isinstance(data.get("elements"), list) else []
        _snap, ref_map, readings = driver.elements_to_snapshot(
            elements,
            limit=max_elements,
            tree_markdown=str(data.get("tree_markdown") or ""),
            compact=True,
        )
        if ref_map:
            state["ref_map"] = ref_map
        return readings[:12], (_time.perf_counter() - t0) * 1000.0

    @tool(
        description=(
            "Capture AX snapshot + readings for an app (AX app state). "
            "Default is compact (include_screenshot=false, no pixel frames) for fast loops. "
            "Prefer computer_click_sequence(text=…) when possible — skip get_state. "
            "Prefer refs (e0…) from snapshot; verify with readings. "
            "app: name or bundle_id (e.g. Calculator)."
        )
    )
    async def computer_get_state(
        app: str = "",
        include_screenshot: bool = False,
        max_elements: int = 48,
        launch_if_needed: bool = True,
        compact: bool = True,
    ) -> str:
        err = _require_macos()
        if err:
            return err
        target = (app or state.get("app") or "").strip()
        if not target:
            return "ERROR: app required (name or bundle_id)"
        match, fail = await _resolve_app(target, launch_if_needed=launch_if_needed)
        if fail:
            return fail
        assert match is not None
        pid = int(match.get("pid") or 0)
        if pid <= 0:
            # launch path may have set windows; try launch again
            try:
                launched = await driver.call(
                    "launch_app",
                    {"bundle_id": match.get("bundle_id")}
                    if match.get("bundle_id")
                    else {"name": match.get("name")},
                )
                pid = int(launched.get("pid") or 0)
                wins = launched.get("windows") or []
            except driver.ComputerDriverError as exc:
                return f"ERROR: app not running and launch failed: {exc}"
        else:
            wins = match.get("windows") or []
        window_id = best_window_id(wins) if wins else None
        if window_id is None:
            window_id, werr = await _pick_window(pid)
            if werr:
                return werr
        assert window_id is not None
        shot_rel = "artifacts/computer/state.png"
        shot_abs = ctx.workspace.path(shot_rel)
        shot_abs.parent.mkdir(parents=True, exist_ok=True)
        # Cap AX fan-out: large dumps dominate model latency + WebUI history.
        elem_cap = max(1, min(120 if compact else 400, int(max_elements)))
        args: dict[str, Any] = {
            "pid": pid,
            "window_id": window_id,
            "include_screenshot": bool(include_screenshot),
            "max_elements": elem_cap,
        }
        if include_screenshot:
            args["screenshot_out_file"] = str(shot_abs)
        try:
            data = await driver.call(
                "get_window_state", args, timeout=45.0 if compact else 90.0
            )
        except driver.ComputerDriverError as exc:
            return f"ERROR: get_window_state failed: {exc}"
        elements = data.get("elements") if isinstance(data.get("elements"), list) else []
        snap, ref_map, readings = driver.elements_to_snapshot(
            elements,
            limit=elem_cap,
            tree_markdown=str(data.get("tree_markdown") or ""),
            compact=bool(compact),
        )
        front = await _frontmost_name()
        state.update(
            {
                "app": match.get("name") or target,
                "bundle_id": match.get("bundle_id"),
                "pid": pid,
                "window_id": window_id,
                "ref_map": ref_map,
                "frontmost_before": front,
            }
        )
        out: dict[str, Any] = {
            "app": state["app"],
            "bundle_id": state["bundle_id"],
            "pid": pid,
            "window_id": window_id,
            "element_count": len(ref_map),
            "snapshot": _cap_snapshot(snap, limit=2800 if compact else 6000),
            "readings": readings[:16],
            "frontmost_app": front,
            "focus_note": (
                "background OK"
                if front and front != state["app"]
                else "target may be frontmost"
            ),
            "degraded": bool(data.get("degraded")),
            "degraded_reason": data.get("degraded_reason"),
            "compact": bool(compact),
            "loop": (
                "Prefer computer_click_sequence(text=/labels=/refs=). "
                "Clicks return readings — do not re-call get_state unless refs are stale. "
                "Keep include_screenshot=false; use computer_screenshot only if asked."
            ),
        }
        if not compact:
            out["tree_markdown"] = (data.get("tree_markdown") or "")[:2000]
        if include_screenshot and shot_abs.is_file():
            out["screenshot"] = shot_rel
            out["screenshot_bytes"] = shot_abs.stat().st_size
            out["screenshot_hint"] = (
                f"Inspect session file {shot_rel} (path only; open via Artifacts, not chat)."
            )
            thumb_rel = "artifacts/computer/thumbs/state_thumb.jpg"
            if maybe_write_computer_thumb(shot_abs, ctx.workspace.path(thumb_rel)):
                out["thumb"] = thumb_rel
                out["thumb_path"] = thumb_rel
        elif data.get("screenshot_file_path"):
            out["screenshot"] = str(data.get("screenshot_file_path"))
        return json.dumps(out)

    def _bound_target() -> str | None:
        if not state.get("pid") or not state.get("window_id"):
            return (
                "ERROR: call computer_get_state(app=...) first this turn "
                "before input actions"
            )
        return None

    def _normalize_label(raw: str) -> str:
        s = (raw or "").strip().lower()
        aliases = {
            "ac": "all clear",
            "allclear": "all clear",
            "clear": "all clear",
            "+": "add",
            "plus": "add",
            "-": "subtract",
            "minus": "subtract",
            "sub": "subtract",
            "×": "multiply",
            "*": "multiply",
            "x": "multiply",
            "mul": "multiply",
            "÷": "divide",
            "/": "divide",
            "div": "divide",
            "=": "equals",
            "equal": "equals",
            "enter": "equals",
            "%": "percent",
            "+/-": "plus minus",
            "±": "plus minus",
            ".": "decimal",
            "dot": "decimal",
            "point": "decimal",
            "delete": "delete",
            "backspace": "delete",
        }
        key = "".join(ch for ch in s if ch.isalnum() or ch in "+-×÷=%./")
        return aliases.get(key, aliases.get(s, s))

    def _label_tokens(raw: str) -> list[str]:
        parts: list[str] = []
        buf = ""
        for ch in (raw or ""):
            if ch in ",;|":
                if buf.strip():
                    parts.append(buf.strip())
                buf = ""
            else:
                buf += ch
        if buf.strip():
            parts.append(buf.strip())
        # Also allow space-separated single tokens like "8 Add 9"
        if len(parts) == 1 and " " in parts[0]:
            maybe = parts[0].split()
            if all(len(p) <= 12 for p in maybe):
                parts = maybe
        return parts

    def _resolve_labels_to_indices(
        labels: list[str], elements: list[dict[str, Any]]
    ) -> tuple[list[int], list[str]]:
        """Map button labels → element_index, preserving order."""
        catalog: list[tuple[int, set[str], str]] = []
        for i, el in enumerate(elements):
            if not isinstance(el, dict):
                continue
            idx = el.get("element_index", i)
            try:
                idx_i = int(idx)
            except (TypeError, ValueError):
                continue
            names = {
                _normalize_label(str(el.get("label") or "")),
                _normalize_label(str(el.get("title") or "")),
                _normalize_label(str(el.get("name") or "")),
                _normalize_label(str(el.get("value") or "")),
            }
            names.discard("")
            catalog.append((idx_i, names, str(el.get("label") or el.get("title") or "")))
        indices: list[int] = []
        unresolved: list[str] = []
        for label in labels:
            want = _normalize_label(label)
            if not want:
                continue
            hit = None
            for idx_i, names, _disp in catalog:
                if want in names or any(want == n or want in n or n in want for n in names if n):
                    # Prefer exact normalized match
                    if want in names:
                        hit = idx_i
                        break
                    if hit is None:
                        hit = idx_i
            if hit is None:
                unresolved.append(label)
            else:
                indices.append(hit)
        return indices, unresolved


    @tool(
        description=(
            "Click a UI element by snapshot ref (e0) or window-local x,y pixels. "
            "Prefer ref. Requires prior computer_get_state. Returns readings after click "
            "so you often skip a full get_state. HITL / app allowlist."
        ),
        risk_class="computer_input",
    )
    async def computer_click(
        ref: str = "",
        x: int = -1,
        y: int = -1,
        button: str = "left",
        clicks: int = 1,
        allow_global_cursor: bool = False,
    ) -> str:
        err = _require_macos()
        if err:
            return err
        miss = _bound_target()
        if miss and not allow_global_cursor:
            return miss
        btn = button if button in {"left", "right", "middle"} else "left"
        idx = driver.ref_to_index(ref) if ref else None
        if idx is not None:
            denied = await _approve_app_input(
                "computer_click", f"{btn} click ref=e{idx}"
            )
            if denied:
                return denied
            args: dict[str, Any] = {
                "pid": int(state["pid"]),
                "window_id": int(state["window_id"]),
                "element_index": idx,
                "button": btn,
                "delivery_mode": "background",
            }
            if clicks > 1:
                args["count"] = max(1, min(3, int(clicks)))
            try:
                result = await driver.call("click", args)
            except driver.ComputerDriverError as exc:
                return f"ERROR: {exc}"
            readings, peek_ms = await _peek_readings(max_elements=24)
            return json.dumps(
                {
                    "ok": True,
                    "mode": "ax_ref",
                    "ref": f"e{idx}",
                    "button": btn,
                    "result": _slim_driver_result(result),
                    "readings": readings,
                    "timing": {"peek_ms": round(peek_ms, 1)},
                    "loop": "Quote readings; skip get_state unless refs look stale.",
                }
            )
        if x >= 0 and y >= 0 and state.get("pid") and state.get("window_id"):
            denied = await _approve_app_input(
                "computer_click", f"{btn} click pixels ({x},{y})"
            )
            if denied:
                return denied
            try:
                result = await driver.call(
                    "click",
                    {
                        "pid": int(state["pid"]),
                        "window_id": int(state["window_id"]),
                        "x": int(x),
                        "y": int(y),
                        "button": btn,
                        "count": max(1, min(3, int(clicks))),
                        "delivery_mode": "background",
                    },
                )
            except driver.ComputerDriverError as exc:
                return f"ERROR: {exc}"
            readings, peek_ms = await _peek_readings(max_elements=24)
            return json.dumps(
                {
                    "ok": True,
                    "mode": "pixel",
                    "x": x,
                    "y": y,
                    "button": btn,
                    "result": _slim_driver_result(result),
                    "readings": readings,
                    "timing": {"peek_ms": round(peek_ms, 1)},
                    "loop": "Quote readings; skip get_state unless refs look stale.",
                }
            )
        if allow_global_cursor and x >= 0 and y >= 0:
            denied = await _approve_app_input(
                "computer_click", f"GLOBAL cursor {btn} at ({x},{y})"
            )
            if denied:
                return denied
            try:
                pag = _require_pyautogui()
            except ImportError as e:
                return f"ERROR: {e}"
            failure = _run_input_action(
                lambda: pag.click(
                    int(x),
                    int(y),
                    clicks=max(1, min(3, int(clicks))),
                    button=btn,
                )
            )
            if failure:
                return failure
            return f"clicked ({x},{y}) button={btn} clicks={clicks} mode=global_cursor"
        return (
            "ERROR: provide ref=eN from computer_get_state, or x,y window pixels, "
            "or allow_global_cursor=true with screen x,y"
        )

    async def _bind_app_window(
        target: str, *, launch_if_needed: bool
    ) -> str | None:
        """Bind pid/window_id without a full AX snapshot. Returns ERROR string or None."""
        match, fail = await _resolve_app(target, launch_if_needed=launch_if_needed)
        if fail:
            return fail
        assert match is not None
        pid = int(match.get("pid") or 0)
        wins = match.get("windows") or []
        window_id = best_window_id(wins) if wins else None
        if window_id is None:
            window_id, werr = await _pick_window(pid)
            if werr:
                return werr
        assert window_id is not None
        state.update(
            {
                "app": match.get("name") or target,
                "bundle_id": match.get("bundle_id"),
                "pid": pid,
                "window_id": window_id,
            }
        )
        return None

    def _timing_payload(*, peek_ms: float = 0.0, mode: str = "") -> dict[str, Any]:
        snap = driver.timing_snapshot()
        return {
            "driver_ms": round(float(snap.get("driver_ms_total") or 0.0), 1),
            "peek_ms": round(float(peek_ms), 1),
            "driver_calls": int(snap.get("calls") or 0),
            "transport": snap.get("last_transport") or "",
            "mode": mode,
        }

    @tool(
        description=(
            "FASTEST desktop action. Prefer text= for calculators/keypads: "
            "app='Calculator' text='8+9=' (types keys, returns readings, ~1s). "
            "labels= auto-chunks keypad sequences into text= when possible "
            "(All Clear,8,Add,9,Equals → type 8+9=). Or refs='e6,e16…'. "
            "One call — quote readings and stop."
        ),
        risk_class="computer_input",
    )
    async def computer_click_sequence(
        refs: str = "",
        labels: str = "",
        text: str = "",
        app: str = "",
        launch_if_needed: bool = True,
        button: str = "left",
    ) -> str:
        import time as _time

        err = _require_macos()
        if err:
            return err
        btn = button if button in {"left", "right", "middle"} else "left"
        type_text = (text or "").strip()
        label_list = _label_tokens(labels) if labels else []
        ref_tokens = (refs or "").replace(",", " ").split() if refs else []
        adaptive_from_labels = False
        # Adaptive chunk (OSWorld-Human): promote keypad label clicks → type_text.
        if not type_text and label_list:
            promoted = labels_to_keypad_text(label_list)
            if promoted:
                type_text = promoted
                adaptive_from_labels = True
        seq_t0 = _time.perf_counter()
        driver.reset_timing()

        # --- Keyboard fast path (Calculator / numeric entry) ---
        if type_text:
            target = (app or state.get("app") or "").strip()
            if not target:
                return "ERROR: app required with text= (e.g. app='Calculator' text='8+9=')"
            if not (state.get("pid") and state.get("window_id") and (
                not app or state.get("app") == target or state.get("bundle_id") == target
            )):
                bind_err = await _bind_app_window(target, launch_if_needed=launch_if_needed)
                if bind_err:
                    return bind_err
            denied = await _approve_app_input(
                "computer_click_sequence",
                f"type {type_text!r} into {state.get('app')}",
            )
            if denied:
                return denied
            try:
                result = await driver.call(
                    "type_text",
                    {
                        "pid": int(state["pid"]),
                        "text": type_text,
                        "delay_ms": 0,
                        "delivery_mode": "background",
                    },
                    ensure=False,
                    timeout=30.0,
                )
            except driver.ComputerDriverError as exc:
                return f"ERROR: type_text failed: {exc}"
            readings, peek_ms = await _peek_readings(max_elements=24)
            mode = (
                "adaptive_text_from_labels" if adaptive_from_labels else "type_text"
            )
            timing = _timing_payload(peek_ms=peek_ms, mode=mode)
            timing["elapsed_ms"] = round((_time.perf_counter() - seq_t0) * 1000.0, 1)
            return json.dumps(
                {
                    "ok": True,
                    "mode": mode,
                    "app": state.get("app"),
                    "text": type_text,
                    "labels": label_list if adaptive_from_labels else None,
                    "result": _slim_driver_result(result),
                    "readings": readings,
                    "timing": timing,
                    "loop": "Quote readings and stop. Do not call get_state or screenshot.",
                }
            )

        elements: list[dict[str, Any]] = []
        need_bind = bool(app.strip() or label_list) or not (
            state.get("pid") and state.get("window_id")
        )
        if need_bind:
            target = (app or state.get("app") or "").strip()
            if not target:
                return (
                    "ERROR: app required for one-shot labels "
                    "(e.g. app='Calculator', labels='8,Add,9,Equals')"
                )
            bind_err = await _bind_app_window(target, launch_if_needed=launch_if_needed)
            if bind_err:
                return bind_err
            # Snapshot only when we need label→index mapping.
            if label_list:
                try:
                    data = await driver.call(
                        "get_window_state",
                        {
                            "pid": int(state["pid"]),
                            "window_id": int(state["window_id"]),
                            "include_screenshot": False,
                            "max_elements": 48,
                        },
                        ensure=False,
                        timeout=30.0,
                    )
                except driver.ComputerDriverError as exc:
                    return f"ERROR: get_window_state failed: {exc}"
                elements = (
                    data.get("elements") if isinstance(data.get("elements"), list) else []
                )
                _snap, ref_map, _readings = driver.elements_to_snapshot(
                    elements,
                    limit=48,
                    tree_markdown=str(data.get("tree_markdown") or ""),
                    compact=True,
                )
                state["ref_map"] = ref_map
        else:
            miss = _bound_target()
            if miss:
                return miss

        indices: list[int] = []
        mode = "ax_ref_sequence"
        if label_list:
            mode = "ax_label_sequence"
            if not elements:
                try:
                    data = await driver.call(
                        "get_window_state",
                        {
                            "pid": int(state["pid"]),
                            "window_id": int(state["window_id"]),
                            "include_screenshot": False,
                            "max_elements": 48,
                        },
                        ensure=False,
                        timeout=20.0,
                    )
                except driver.ComputerDriverError as exc:
                    return f"ERROR: snapshot for labels failed: {exc}"
                elements = (
                    data.get("elements")
                    if isinstance(data.get("elements"), list)
                    else []
                )
            indices, unresolved = _resolve_labels_to_indices(label_list, elements)
            if unresolved:
                return json.dumps(
                    {
                        "ok": False,
                        "error": f"unresolved labels: {unresolved}",
                        "hint": "Use text='8+9=' for Calculator, or refs= from get_state",
                    }
                )
        else:
            for token in ref_tokens:
                idx = driver.ref_to_index(token.strip())
                if idx is None:
                    return f"ERROR: invalid ref {token!r} (expected eN)"
                indices.append(idx)
        if not indices:
            return "ERROR: provide text= (fastest), labels=, or refs="
        if len(indices) > 16:
            return "ERROR: max 16 clicks per computer_click_sequence"

        denied = await _approve_app_input(
            "computer_click_sequence",
            f"{btn} click sequence {[f'e{i}' for i in indices]}"
            + (f" labels={label_list}" if label_list else ""),
        )
        if denied:
            return denied
        steps: list[dict[str, Any]] = []
        for idx in indices:
            try:
                result = await driver.call(
                    "click",
                    {
                        "pid": int(state["pid"]),
                        "window_id": int(state["window_id"]),
                        "element_index": idx,
                        "button": btn,
                        "delivery_mode": "background",
                    },
                    ensure=False,
                )
            except driver.ComputerDriverError as exc:
                return json.dumps(
                    {
                        "ok": False,
                        "error": str(exc),
                        "completed": steps,
                        "failed_ref": f"e{idx}",
                    }
                )
            steps.append({"ref": f"e{idx}", "result": _slim_driver_result(result)})
        readings, peek_ms = await _peek_readings(max_elements=24)
        timing = _timing_payload(peek_ms=peek_ms, mode=mode)
        timing["elapsed_ms"] = round((_time.perf_counter() - seq_t0) * 1000.0, 1)
        return json.dumps(
            {
                "ok": True,
                "mode": mode,
                "app": state.get("app"),
                "clicks": len(steps),
                "refs": [s["ref"] for s in steps],
                "labels": label_list or None,
                "readings": readings,
                "timing": timing,
                "loop": (
                    "If readings show the goal, stop and quote them. "
                    "Do not re-call get_state or screenshot."
                ),
            }
        )


    @tool(
        description="AX set_value on element ref (eN). Prefer over typing into fields. HITL.",
        risk_class="computer_input",
    )
    async def computer_set_value(ref: str, value: str) -> str:
        err = _require_macos()
        if err:
            return err
        miss = _bound_target()
        if miss:
            return miss
        idx = driver.ref_to_index(ref)
        if idx is None:
            return "ERROR: ref must look like e12"
        denied = await _approve_app_input(
            "computer_set_value", f"set e{idx}={value[:120]!r}"
        )
        if denied:
            return denied
        try:
            result = await driver.call(
                "set_value",
                {
                    "pid": int(state["pid"]),
                    "window_id": int(state["window_id"]),
                    "element_index": idx,
                    "value": value,
                },
            )
        except driver.ComputerDriverError as exc:
            return f"ERROR: {exc}"
        return json.dumps({"ok": True, "ref": f"e{idx}", "result": result})

    @tool(
        description=(
            "Type text into focused/ref field via cua-driver. "
            "Retries foreground if background insert is unverifiable. HITL."
        ),
        risk_class="computer_input",
    )
    async def computer_type(text: str, ref: str = "", interval: float = 0.02) -> str:
        err = _require_macos()
        if err:
            return err
        miss = _bound_target()
        if miss:
            return miss
        preview = text if len(text) <= 200 else text[:200] + "…"
        denied = await _approve_app_input(
            "computer_type", f"type {len(text)} chars ref={ref or '-'}: {preview!r}"
        )
        if denied:
            return denied
        args: dict[str, Any] = {
            "pid": int(state["pid"]),
            "text": text,
            "delivery_mode": "background",
            "delay_ms": int(max(0.0, min(0.2, float(interval))) * 1000),
        }
        idx = driver.ref_to_index(ref) if ref else None
        if idx is not None:
            args["element_index"] = idx
            args["window_id"] = int(state["window_id"])
        try:
            result = await _call_input_with_foreground_retry("type_text", args)
        except driver.ComputerDriverError as exc:
            return f"ERROR: {exc}"
        if _driver_unverifiable(result):
            return _unverifiable_input_error("computer_type", result)
        mode = (
            str(result.get("delivery_mode") or args.get("delivery_mode") or "")
            if isinstance(result, dict)
            else str(args.get("delivery_mode") or "")
        )
        return json.dumps(
            {
                "ok": True,
                "chars": len(text),
                "verified": True,
                "delivery_mode": mode or "background",
                "result": _slim_driver_result(result),
            }
        )

    @tool(
        description=(
            "Press a single key (return, tab, escape, …) via cua-driver. "
            "Retries foreground if unverifiable. HITL."
        ),
        risk_class="computer_input",
    )
    async def computer_key(key: str, ref: str = "") -> str:
        err = _require_macos()
        if err:
            return err
        miss = _bound_target()
        if miss:
            return miss
        k = key.strip().lower()
        if k == "enter":
            k = "return"
        denied = await _approve_app_input("computer_key", f"key {k}")
        if denied:
            return denied
        args: dict[str, Any] = {
            "pid": int(state["pid"]),
            "key": k,
            "delivery_mode": "background",
            "window_id": int(state["window_id"]),
        }
        idx = driver.ref_to_index(ref) if ref else None
        if idx is not None:
            args["element_index"] = idx
        try:
            result = await _call_input_with_foreground_retry("press_key", args)
        except driver.ComputerDriverError as exc:
            return f"ERROR: {exc}"
        if _driver_unverifiable(result):
            return _unverifiable_input_error("computer_key", result)
        return json.dumps(
            {
                "ok": True,
                "key": k,
                "verified": True,
                "result": _slim_driver_result(result),
            }
        )

    @tool(
        description="Hotkey combo as '+'-joined keys (e.g. command+c). Destructive blocked. HITL.",
        risk_class="computer_input",
    )
    async def computer_hotkey(keys: str) -> str:
        err = _require_macos()
        if err:
            return err
        if _BLOCKED_HOTKEYS.search(keys):
            return f"DENIED: blocked hotkey pattern: {keys}"
        parts = [p.strip().lower() for p in keys.replace("-", "+").split("+") if p.strip()]
        if not parts:
            return "ERROR: empty hotkey"
        joined = "+".join(parts)
        if joined in {
            "command+q",
            "cmd+q",
            "command+option+esc",
            "command+option+escape",
        }:
            return f"DENIED: blocked hotkey: {joined}"
        miss = _bound_target()
        if miss:
            return miss
        denied = await _approve_app_input("computer_hotkey", f"hotkey {joined}")
        if denied:
            return denied
        # cua-driver hotkey uses keys array; last is the key, rest modifiers
        key = parts[-1]
        mods = []
        for p in parts[:-1]:
            if p in {"cmd", "command"}:
                mods.append("cmd")
            elif p in {"option", "alt"}:
                mods.append("option")
            elif p in {"ctrl", "control"}:
                mods.append("ctrl")
            elif p == "shift":
                mods.append("shift")
            else:
                mods.append(p)
        if not mods:
            return "ERROR: hotkey needs a modifier (e.g. command+c); use computer_key for a single key"
        try:
            result = await _call_input_with_foreground_retry(
                "hotkey",
                {
                    "pid": int(state["pid"]),
                    "window_id": int(state["window_id"]),
                    "keys": mods + [key],
                    "delivery_mode": "background",
                },
            )
        except driver.ComputerDriverError as exc:
            return f"ERROR: {exc}"
        if _driver_unverifiable(result):
            return _unverifiable_input_error("computer_hotkey", result)
        return json.dumps(
            {
                "ok": True,
                "hotkey": joined,
                "verified": True,
                "result": _slim_driver_result(result),
            }
        )

    @tool(
        description="Scroll app window/element. direction=up|down|left|right. Prefer ref. HITL.",
        risk_class="computer_input",
    )
    async def computer_scroll(
        direction: str = "down",
        amount: int = 3,
        ref: str = "",
        by: str = "line",
    ) -> str:
        err = _require_macos()
        if err:
            return err
        miss = _bound_target()
        if miss:
            return miss
        d = direction.strip().lower()
        if d not in {"up", "down", "left", "right"}:
            return "ERROR: direction must be up|down|left|right"
        denied = await _approve_app_input(
            "computer_scroll", f"scroll {d} amount={amount} ref={ref or '-'}"
        )
        if denied:
            return denied
        args: dict[str, Any] = {
            "pid": int(state["pid"]),
            "direction": d,
            "amount": max(1, min(50, int(amount))),
            "by": by if by in {"line", "page"} else "line",
            "delivery_mode": "background",
            "window_id": int(state["window_id"]),
        }
        idx = driver.ref_to_index(ref) if ref else None
        if idx is not None:
            args["element_index"] = idx
        try:
            result = await driver.call("scroll", args)
        except driver.ComputerDriverError as exc:
            return f"ERROR: {exc}"
        return json.dumps({"ok": True, "direction": d, "result": result})

    @tool(
        description=(
            "Full-display screenshot to artifacts/computer/screen.png (no HITL). "
            "Slow — only when the user asks for a picture or AX readings are insufficient. "
            "Prefer computer_get_state(compact=true, include_screenshot=false) + readings."
        )
    )
    async def computer_screenshot(path: str = "artifacts/computer/screen.png") -> str:
        err = _require_macos()
        if err:
            return err
        dest = ctx.workspace.path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Prefer driver desktop capture when available; fall back to screencapture.
        def _shot_payload(*, via: str) -> str:
            payload: dict[str, Any] = {
                "path": path,
                "bytes": dest.stat().st_size,
                "via": via,
            }
            thumb_rel = "artifacts/computer/thumbs/screen_thumb.jpg"
            if maybe_write_computer_thumb(dest, ctx.workspace.path(thumb_rel)):
                payload["thumb"] = thumb_rel
                payload["thumb_path"] = thumb_rel
            return json.dumps(payload)

        try:
            await driver.call(
                "get_desktop_state",
                {"screenshot_out_file": str(dest)},
                timeout=30.0,
            )
            if dest.is_file():
                return _shot_payload(via="cua-driver")
        except driver.ComputerDriverError:
            pass
        if not shutil.which("screencapture"):
            return "ERROR: screencapture not found and cua-driver desktop capture failed"
        proc = await asyncio.create_subprocess_exec(
            "screencapture",
            "-x",
            str(dest),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not dest.is_file():
            return (
                f"ERROR: screencapture failed (code={proc.returncode}): "
                f"{stderr.decode(errors='replace')[:400]}. "
                "Grant Screen Recording to CuaDriver.app / Terminal."
            )
        return _shot_payload(via="screencapture")

    @tool(
        description=(
            "DEGRADED: move real system cursor (PyAutoGUI). Prefer AX refs. "
            "Requires allow via HITL. HITL required."
        ),
        risk_class="computer_input",
    )
    async def computer_move(x: int, y: int) -> str:
        err = _require_macos()
        if err:
            return err
        denied = await _approve_app_input(
            "computer_move", f"GLOBAL move to ({x},{y})"
        )
        if denied:
            return denied
        try:
            pag = _require_pyautogui()
        except ImportError as e:
            return f"ERROR: {e}"
        failure = _run_input_action(
            lambda: pag.moveTo(int(x), int(y), duration=0.15)
        )
        if failure:
            return failure
        return f"moved to ({x},{y}) mode=global_cursor"

    for t in (
        computer_doctor,
        computer_launch,
        computer_wait,
        computer_list_apps,
        computer_get_state,
        computer_click,
        computer_click_sequence,
        computer_set_value,
        computer_type,
        computer_key,
        computer_hotkey,
        computer_scroll,
        computer_screenshot,
        computer_move,
    ):
        if hasattr(t, "name"):
            reg.register(t)  # type: ignore[arg-type]
    return reg
