"""Regression suite for macOS computer-use tools + computer_use skill.

No interactive HITL — ApprovalGate is either auto_approve or fail-closed (no approver).
Driver calls are mocked so CI stays green without cua-driver / TCC.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
from pathlib import Path
from types import SimpleNamespace

import pytest

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.builtin import load_entry_point_tools
from kageha.harness.tools.computer import register_computer_tools
from kageha.harness.tools.skills_tools import activate_skills
from kageha.loop.controller import _filter_tools_for_skills
from kageha.memory.skills import SkillRegistry

_COMPUTER_TOOLS = (
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
)

_IS_DARWIN = platform.system() == "Darwin"
_MACOS_ONLY = "ERROR: computer-use v1 is macOS-only"


def _ctx(tmp_path: Path, *, auto_approve: bool = True) -> HarnessContext:
    root = tmp_path / "session"
    root.mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(exist_ok=True)
    ws = SessionWorkspace(run_id="test", root=root)
    return HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=auto_approve, approver=None),
        router=SimpleNamespace(),
    )


def test_computer_use_skill_discoverable():
    reg = SkillRegistry()
    assert "computer_use" in reg.skills
    catalog = reg.catalog()
    assert "computer_use" in catalog
    skill = reg.skills["computer_use"]
    body = (skill.path / "SKILL.md").read_text(encoding="utf-8")
    assert "browser_" in body
    assert "computer_get_state" in body
    assert "include_screenshot=false" in body
    assert "readings are enough" in body.lower()
    assert "computer_get_state" in (skill.allowed_tools or [])
    assert "computer_click" in (skill.allowed_tools or [])
    assert "computer_doctor" in (skill.allowed_tools or [])


def test_computer_use_autoload_excludes_web_browse():
    reg = SkillRegistry()
    loaded = reg.auto_load_for_task(
        "Use computer_use and computer_get_state on Calculator, NOT browser",
        limit=4,
    )
    assert loaded.names == ["computer_use"]
    assert "web_browse" not in loaded.names


def test_all_computer_tools_registered(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_TOOL_PACKS", "computer")
    ctx = _ctx(tmp_path)
    reg = register_computer_tools(ctx)
    names = set(reg.names())
    for name in _COMPUTER_TOOLS:
        assert name in names, f"missing tool: {name}"

    ep = load_entry_point_tools(ctx)
    ep_names = set(ep.names())
    for name in _COMPUTER_TOOLS:
        assert name in ep_names, f"missing from entry points: {name}"


def test_skill_filter_keeps_computer_tools_when_computer_use_active(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KAGEHA_TOOL_PACKS", "computer")
    ctx = _ctx(tmp_path)
    ctx.tools = load_entry_point_tools(ctx)
    activate_skills(ctx, ["computer_use"])
    names = {s.name for s in _filter_tools_for_skills(ctx)}
    assert "computer_get_state" in names
    assert "computer_click" in names
    assert "bash" in names


@pytest.mark.skipif(_IS_DARWIN, reason="non-Darwin macOS-only ERROR path")
def test_non_darwin_screenshot_and_click_return_macos_only(tmp_path):
    ctx = _ctx(tmp_path, auto_approve=True)
    reg = register_computer_tools(ctx)

    async def _run():
        shot = await reg.get("computer_screenshot").call()
        assert shot.startswith("ERROR:"), shot
        assert "macOS-only" in shot
        click = await reg.get("computer_click").call(ref="e0")
        assert click.startswith("ERROR:"), click
        assert "macOS-only" in click

    asyncio.run(_run())


def test_blocked_apps_denied(tmp_path, monkeypatch: pytest.MonkeyPatch):
    import kageha.harness.tools.computer as computer_mod
    import kageha.harness.tools.computer_driver as driver_mod

    monkeypatch.setattr(computer_mod, "_require_macos", lambda: None)
    monkeypatch.setattr(driver_mod, "require_macos", lambda: None)

    async def fake_call(tool: str, args=None, **kwargs):
        if tool == "list_apps":
            return {
                "apps": [
                    {
                        "name": "Terminal",
                        "bundle_id": "com.apple.Terminal",
                        "pid": 1,
                        "running": True,
                    }
                ]
            }
        raise AssertionError(f"unexpected call {tool}")

    monkeypatch.setattr(driver_mod, "call", fake_call)
    ctx = _ctx(tmp_path, auto_approve=True)
    reg = register_computer_tools(ctx)

    async def _run():
        out = await reg.get("computer_get_state").call(app="Terminal")
        assert out.startswith("DENIED:"), out

    asyncio.run(_run())


@pytest.mark.parametrize(
    "keys",
    [
        "command+q",
        "cmd+q",
        "command+option+escape",
    ],
)
def test_blocked_hotkeys_denied_without_approval(tmp_path, keys: str, monkeypatch: pytest.MonkeyPatch):
    """Destructive hotkeys are blocked before HITL; no approver needed."""
    import kageha.harness.tools.computer as computer_mod

    monkeypatch.setattr(computer_mod, "_require_macos", lambda: None)
    ctx = _ctx(tmp_path, auto_approve=False)
    reg = register_computer_tools(ctx)

    async def _run():
        out = await reg.get("computer_hotkey").call(keys=keys)
        assert out.startswith("DENIED:"), out
        assert ctx.approvals.log == []

    asyncio.run(_run())


def test_input_tools_denied_without_approver(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """auto_approve=False + no approver → DENIED (CI-safe, no interactive HITL)."""
    import kageha.harness.tools.computer as computer_mod
    import kageha.harness.tools.computer_allowlist as allow_mod
    import kageha.harness.tools.computer_driver as driver_mod

    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(computer_mod, "_require_macos", lambda: None)
    monkeypatch.setattr(driver_mod, "require_macos", lambda: None)
    monkeypatch.setattr(allow_mod, "get_decision", lambda _bid: None)

    async def fake_call(tool: str, args=None, **kwargs):
        if tool == "list_apps":
            return {
                "apps": [
                    {
                        "name": "Calculator",
                        "bundle_id": "com.apple.calculator",
                        "pid": 42,
                        "running": True,
                        "windows": [{"window_id": 7}],
                    }
                ]
            }
        if tool == "get_window_state":
            return {
                "elements": [
                    {
                        "element_index": 0,
                        "role": "button",
                        "label": "1",
                    }
                ],
                "tree_markdown": "[element_index 0] button 1",
            }
        return {"ok": True}

    monkeypatch.setattr(driver_mod, "call", fake_call)
    ctx = _ctx(tmp_path, auto_approve=False)
    reg = register_computer_tools(ctx)

    async def _run():
        st = await reg.get("computer_get_state").call(app="Calculator")
        assert not st.startswith("ERROR:"), st
        cases = [
            ("computer_click", {"ref": "e0"}),
            ("computer_click_sequence", {"refs": "e0"}),
            ("computer_type", {"text": "x"}),
            ("computer_key", {"key": "escape"}),
            ("computer_set_value", {"ref": "e0", "value": "1"}),
            ("computer_scroll", {"direction": "down"}),
            ("computer_hotkey", {"keys": "command+c"}),
            ("computer_move", {"x": 0, "y": 0}),
            ("computer_launch", {"app": "Calculator"}),
        ]
        for name, kwargs in cases:
            out = await reg.get(name).call(**kwargs)
            assert out.startswith("DENIED:"), (name, out)

    asyncio.run(_run())


def test_get_state_includes_readings_and_click_ref(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    import kageha.harness.tools.computer as computer_mod
    import kageha.harness.tools.computer_driver as driver_mod

    monkeypatch.setattr(computer_mod, "_require_macos", lambda: None)
    monkeypatch.setattr(driver_mod, "require_macos", lambda: None)
    calls: list[tuple[str, dict]] = []

    async def fake_call(tool: str, args=None, **kwargs):
        args = args or {}
        calls.append((tool, args))
        if tool == "list_apps":
            return {
                "apps": [
                    {
                        "name": "Calculator",
                        "bundle_id": "com.apple.calculator",
                        "pid": 42,
                        "running": True,
                        "active": False,
                        "windows": [{"window_id": 7}],
                    },
                    {
                        "name": "Cursor",
                        "bundle_id": "com.todesktop.cursor",
                        "pid": 9,
                        "running": True,
                        "active": True,
                    },
                ]
            }
        if tool == "get_window_state":
            return {
                "elements": [
                    {"element_index": 3, "role": "AXButton", "label": "5"},
                    {"element_index": 4, "role": "AXButton", "label": "+"},
                ],
                "tree_markdown": (
                    '- [0] AXWindow "Calculator"\n'
                    '    - AXStaticText = "42"\n'
                    '    - [3] AXButton (5)\n'
                ),
            }
        if tool == "click":
            return {"ok": True, "element_index": args.get("element_index")}
        if tool == "type_text":
            return {"ok": True, "text": args.get("text"), "verified": True}
        raise AssertionError(tool)

    monkeypatch.setattr(driver_mod, "call", fake_call)
    ctx = _ctx(tmp_path, auto_approve=True)
    reg = register_computer_tools(ctx)

    async def _run():
        st = json.loads(await reg.get("computer_get_state").call(app="Calculator"))
        assert st["pid"] == 42
        assert st["window_id"] == 7
        assert "e3" in st["snapshot"]
        assert st["readings"]
        assert any(r.get("value") == "42" for r in st["readings"])
        assert st.get("frontmost_app") == "Cursor"
        assert st.get("compact") is True
        assert "tree_markdown" not in st
        assert any(
            t == "get_window_state" and a.get("include_screenshot") is False
            for t, a in calls
        )
        clicked = json.loads(await reg.get("computer_click").call(ref="e3"))
        assert clicked["mode"] == "ax_ref"
        assert clicked["ref"] == "e3"
        assert "readings" in clicked
        assert isinstance(clicked.get("result"), dict)
        assert set(clicked["result"]).issubset({"ok", "verified", "element_index", "error", "text"})
        assert any(t == "click" and a.get("element_index") == 3 for t, a in calls)
        seq = json.loads(
            await reg.get("computer_click_sequence").call(refs="e3,e4")
        )
        assert seq["ok"] is True
        assert seq["refs"] == ["e3", "e4"]
        assert "readings" in seq
        labeled = json.loads(
            await reg.get("computer_click_sequence").call(
                app="Calculator", labels="5,+"
            )
        )
        assert labeled["ok"] is True
        # Keypad labels adaptively chunk into type_text (OSWorld-Human action grouping).
        assert labeled["mode"] == "adaptive_text_from_labels"
        assert labeled.get("text") == "5+"
        assert labeled.get("labels") == ["5", "+"]
        assert "timing" in labeled
        typed = json.loads(
            await reg.get("computer_click_sequence").call(
                app="Calculator", text="8+9="
            )
        )
        assert typed["ok"] is True
        assert typed["mode"] == "type_text"
        assert typed.get("text") == "8+9="
        assert any(t == "type_text" for t, _a in calls)
        # Non-keypad labels still use click sequence.
        menuish = json.loads(
            await reg.get("computer_click_sequence").call(
                app="Calculator", labels="View,Basic"
            )
        )
        assert menuish["ok"] is False or menuish.get("mode") == "ax_label_sequence"

    asyncio.run(_run())


def test_computer_type_retries_foreground_then_errors_if_unverifiable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """Codex/Electron: unverifiable insert must not report ok (false success)."""
    import kageha.harness.tools.computer as computer_mod
    import kageha.harness.tools.computer_allowlist as allow_mod
    import kageha.harness.tools.computer_driver as driver_mod

    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(computer_mod, "_require_macos", lambda: None)
    monkeypatch.setattr(driver_mod, "require_macos", lambda: None)
    monkeypatch.setattr(allow_mod, "get_decision", lambda _bid: "allow")
    modes: list[str] = []

    async def fake_call(tool: str, args=None, **kwargs):
        args = args or {}
        if tool == "list_apps":
            return {
                "apps": [
                    {
                        "name": "ChatGPT",
                        "bundle_id": "com.openai.codex",
                        "pid": 99,
                        "running": True,
                        "windows": [{"window_id": 3, "title": "Codex"}],
                    }
                ]
            }
        if tool == "get_window_state":
            return {
                "elements": [
                    {"element_index": 0, "role": "textField", "label": "Message"}
                ],
                "tree_markdown": "[element_index 0] textField Message",
            }
        if tool == "type_text":
            modes.append(str(args.get("delivery_mode") or ""))
            return {
                "characters": len(str(args.get("text") or "")),
                "effect": "unverifiable",
                "escalation": {"recommended": "foreground"},
                "path": "key_events",
            }
        return {"ok": True}

    monkeypatch.setattr(driver_mod, "call", fake_call)
    ctx = _ctx(tmp_path, auto_approve=True)
    reg = register_computer_tools(ctx)

    async def _run():
        await reg.get("computer_get_state").call(app="ChatGPT")
        out = await reg.get("computer_type").call(text="hello codex")
        assert out.startswith("ERROR:"), out
        assert "unverifiable" in out.lower()
        assert modes == ["background", "foreground"]

    asyncio.run(_run())


def test_denied_requests_produce_zero_driver_mutations(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """REL-001 / Req 2.6: denied requests MUST NOT produce completed mutation calls.

    Brief non-mutating driver contact (list_apps, get_window_state) before
    the block is acceptable, but no mutating call (click, type_text,
    launch_app, scroll, key, hotkey, etc.) should reach the driver.
    """
    import kageha.harness.tools.computer as computer_mod
    import kageha.harness.tools.computer_allowlist as allow_mod
    import kageha.harness.tools.computer_driver as driver_mod

    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(computer_mod, "_require_macos", lambda: None)
    monkeypatch.setattr(driver_mod, "require_macos", lambda: None)
    monkeypatch.setattr(allow_mod, "get_decision", lambda _bid: None)

    # Track all driver calls with their tool names.
    driver_calls: list[str] = []

    # Define which driver tools are mutations (state-changing).
    _MUTATION_TOOLS = frozenset({
        "click",
        "type_text",
        "launch_app",
        "scroll",
        "key",
        "hotkey",
        "move",
        "set_value",
        "drag",
    })

    async def tracking_call(tool: str, args=None, **kwargs):
        driver_calls.append(tool)
        if tool == "list_apps":
            return {
                "apps": [
                    {
                        "name": "Calculator",
                        "bundle_id": "com.apple.calculator",
                        "pid": 42,
                        "running": True,
                        "windows": [{"window_id": 7}],
                    }
                ]
            }
        if tool == "get_window_state":
            return {
                "elements": [
                    {"element_index": 0, "role": "button", "label": "1"},
                    {"element_index": 1, "role": "button", "label": "2"},
                ],
                "tree_markdown": "[element_index 0] button 1\n[element_index 1] button 2",
            }
        if tool == "list_windows":
            return {"windows": [{"window_id": 7}]}
        # Any mutation call reaching here means the gate failed to block.
        if tool in _MUTATION_TOOLS:
            return {"ok": True}
        raise AssertionError(f"unexpected driver call: {tool}")

    monkeypatch.setattr(driver_mod, "call", tracking_call)

    # Fail-closed: no approver, auto_approve=False
    ctx = _ctx(tmp_path, auto_approve=False)
    reg = register_computer_tools(ctx)

    async def _run():
        # Allow get_state (non-mutating) to bind the session target.
        st = await reg.get("computer_get_state").call(app="Calculator")
        assert not st.startswith("ERROR:"), st

        # Record which driver calls happened for get_state (non-mutating only).
        pre_mutation_calls = list(driver_calls)
        for c in pre_mutation_calls:
            assert c not in _MUTATION_TOOLS, (
                f"Non-mutating get_state produced mutation call: {c}"
            )

        # Now attempt all mutating tools — all should be DENIED.
        mutating_attempts = [
            ("computer_click", {"ref": "e0"}),
            ("computer_click_sequence", {"refs": "e0,e1"}),
            ("computer_click_sequence", {"app": "Calculator", "text": "123"}),
            ("computer_click_sequence", {"app": "Calculator", "labels": "1,2"}),
            ("computer_type", {"text": "hello"}),
            ("computer_key", {"key": "escape"}),
            ("computer_set_value", {"ref": "e0", "value": "9"}),
            ("computer_scroll", {"direction": "down"}),
            ("computer_hotkey", {"keys": "command+c"}),
            ("computer_move", {"x": 100, "y": 100}),
            ("computer_launch", {"app": "Calculator"}),
        ]

        for name, kwargs in mutating_attempts:
            out = await reg.get(name).call(**kwargs)
            assert out.startswith("DENIED:"), (
                f"{name} was not denied: {out[:200]}"
            )

        # Core assertion: filter driver calls to only those after get_state,
        # and verify ZERO of them are mutation calls.
        post_getstate_calls = driver_calls[len(pre_mutation_calls):]
        completed_mutations = [
            c for c in post_getstate_calls if c in _MUTATION_TOOLS
        ]
        assert completed_mutations == [], (
            f"Expected zero completed mutation calls for denied requests, "
            f"but driver saw: {completed_mutations}"
        )

        # Non-mutating calls (list_apps, get_window_state, list_windows) in
        # the post-getstate phase are acceptable per Req 2.6 — they are
        # brief non-mutating driver contact before the block.
        non_mutating_post = [
            c for c in post_getstate_calls if c not in _MUTATION_TOOLS
        ]
        for c in non_mutating_post:
            assert c in {"list_apps", "get_window_state", "list_windows"}, (
                f"Unexpected non-mutating driver call: {c}"
            )

    asyncio.run(_run())


def test_denied_requests_zero_mutations_no_prior_get_state(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """REL-001 / Req 2.6: denied mutations produce zero driver mutations even
    when no prior get_state has been called (unbound session).

    Verifies the gate blocks before any driver mutation call regardless of
    whether the session has a bound app/window target.
    """
    import kageha.harness.tools.computer as computer_mod
    import kageha.harness.tools.computer_allowlist as allow_mod
    import kageha.harness.tools.computer_driver as driver_mod

    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(computer_mod, "_require_macos", lambda: None)
    monkeypatch.setattr(driver_mod, "require_macos", lambda: None)
    monkeypatch.setattr(allow_mod, "get_decision", lambda _bid: None)

    driver_calls: list[str] = []

    _MUTATION_TOOLS = frozenset({
        "click",
        "type_text",
        "launch_app",
        "scroll",
        "key",
        "hotkey",
        "move",
        "set_value",
        "drag",
    })

    async def tracking_call(tool: str, args=None, **kwargs):
        driver_calls.append(tool)
        if tool == "list_apps":
            return {
                "apps": [
                    {
                        "name": "Calculator",
                        "bundle_id": "com.apple.calculator",
                        "pid": 42,
                        "running": True,
                        "windows": [{"window_id": 7}],
                    }
                ]
            }
        if tool == "get_window_state":
            return {
                "elements": [
                    {"element_index": 0, "role": "button", "label": "1"},
                ],
                "tree_markdown": "[element_index 0] button 1",
            }
        if tool == "list_windows":
            return {"windows": [{"window_id": 7}]}
        if tool in _MUTATION_TOOLS:
            return {"ok": True}
        raise AssertionError(f"unexpected driver call: {tool}")

    monkeypatch.setattr(driver_mod, "call", tracking_call)

    # Fail-closed: no approver, auto_approve=False — no get_state binding.
    ctx = _ctx(tmp_path, auto_approve=False)
    reg = register_computer_tools(ctx)

    async def _run():
        # Attempt mutating calls without prior get_state (unbound session).
        # These should either return ERROR (unbound target) or DENIED,
        # but never produce a completed mutation driver call.
        unbound_attempts = [
            ("computer_click", {"ref": "e0"}),
            ("computer_type", {"text": "hello"}),
            ("computer_key", {"key": "escape"}),
            ("computer_scroll", {"direction": "down"}),
            ("computer_hotkey", {"keys": "command+c"}),
            ("computer_move", {"x": 50, "y": 50}),
            # launch and click_sequence with app= may contact the driver to
            # resolve the app, but must not produce any mutation calls.
            ("computer_launch", {"app": "Calculator"}),
            ("computer_click_sequence", {"app": "Calculator", "text": "5+3"}),
        ]

        for name, kwargs in unbound_attempts:
            out = await reg.get(name).call(**kwargs)
            assert out.startswith("DENIED:") or out.startswith("ERROR:"), (
                f"{name} was not denied/errored without prior get_state: {out[:200]}"
            )

        # Core assertion: zero mutation calls reached the driver.
        completed_mutations = [
            c for c in driver_calls if c in _MUTATION_TOOLS
        ]
        assert completed_mutations == [], (
            f"Expected zero completed mutation calls for denied requests "
            f"(no prior get_state), but driver saw: {completed_mutations}"
        )

    asyncio.run(_run())


@pytest.mark.live_ui
@pytest.mark.skipif(not _IS_DARWIN, reason="computer-use v1 is macOS-only")
@pytest.mark.skipif(
    os.environ.get("KAGEHA_LIVE_UI_TESTS") != "1",
    reason="set KAGEHA_LIVE_UI_TESTS=1 to run a real screencapture/cua-driver smoke test",
)
def test_darwin_screenshot_smoke(tmp_path):
    """Genuinely live: takes a real screenshot via cua-driver/screencapture (REL-002, Req 3.3)."""
    ctx = _ctx(tmp_path)
    reg = register_computer_tools(ctx)

    async def _run():
        out = await reg.get("computer_screenshot").call()
        if out.startswith("ERROR:"):
            pytest.skip(out)
        data = json.loads(out)
        dest = ctx.workspace.root / data["path"]
        assert dest.is_file()
        assert dest.stat().st_size > 0

    asyncio.run(_run())
