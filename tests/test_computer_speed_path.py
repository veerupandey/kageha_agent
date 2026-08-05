"""Unit tests for computer-use speed path (adaptive chunks + driver transport)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.computer import (
    best_window_id,
    labels_to_keypad_text,
    maybe_write_computer_thumb,
    register_computer_tools,
)


def test_labels_to_keypad_text_promotes_calculator_sequence():
    assert labels_to_keypad_text(["All Clear", "8", "Add", "9", "Equals"]) == "8+9="
    assert labels_to_keypad_text(["5", "+"]) == "5+"
    assert labels_to_keypad_text(["View", "Basic"]) is None
    assert labels_to_keypad_text([]) is None


def test_maybe_write_computer_thumb_makes_small_jpeg(tmp_path: Path):
    pytest.importorskip("PIL")
    from PIL import Image

    src = tmp_path / "screen.png"
    Image.new("RGB", (1200, 800), color=(20, 40, 60)).save(src)
    dest = tmp_path / "thumbs" / "screen_thumb.jpg"
    assert maybe_write_computer_thumb(src, dest) is True
    assert dest.is_file()
    with Image.open(dest) as thumb:
        assert thumb.size[0] <= 480
        assert thumb.size[1] <= 270
        assert thumb.format == "JPEG"


def test_best_window_id_prefers_on_screen_large():
    windows = [
        {
            "window_id": 1,
            "is_on_screen": False,
            "bounds": {"width": 1920, "height": 30},
        },
        {
            "window_id": 2,
            "is_on_screen": True,
            "bounds": {"width": 230, "height": 408},
        },
        {
            "window_id": 3,
            "is_on_screen": True,
            "bounds": {"width": 10, "height": 10},
        },
    ]
    assert best_window_id(windows) == 2


def test_socket_transport_unwrap_and_timing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import kageha.harness.tools.computer_driver as driver_mod

    monkeypatch.setattr(driver_mod, "require_macos", lambda: None)
    monkeypatch.setattr(driver_mod, "socket_path", lambda: tmp_path / "sock")
    monkeypatch.setenv("KAGEHA_CUA_TRANSPORT", "socket")
    driver_mod.reset_timing()

    async def fake_socket(tool, args=None, *, timeout=60.0):
        assert tool == "list_apps"
        return {"apps": [{"name": "Calculator", "pid": 1}]}

    monkeypatch.setattr(driver_mod, "_call_socket", fake_socket)
    # ensure_daemon no-op
    async def _noop_ensure(**kwargs):
        return None

    monkeypatch.setattr(driver_mod, "ensure_daemon", _noop_ensure)

    async def _run():
        # Path.exists for socket_path — create dummy file
        (tmp_path / "sock").write_text("", encoding="utf-8")
        data = await driver_mod.call("list_apps", {}, ensure=False)
        assert data["apps"][0]["name"] == "Calculator"
        snap = driver_mod.timing_snapshot()
        assert snap["socket_calls"] == 1
        assert snap["calls"] == 1
        assert snap["last_transport"] == "socket"

    asyncio.run(_run())


def test_adaptive_labels_use_type_text_not_clicks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import kageha.harness.tools.computer as computer_mod
    import kageha.harness.tools.computer_driver as driver_mod

    monkeypatch.setattr(computer_mod, "_require_macos", lambda: None)
    monkeypatch.setattr(driver_mod, "require_macos", lambda: None)
    calls: list[str] = []

    async def fake_call(tool: str, args=None, **kwargs):
        calls.append(tool)
        if tool == "list_apps":
            return {
                "apps": [
                    {
                        "name": "Calculator",
                        "bundle_id": "com.apple.calculator",
                        "pid": 42,
                        "running": True,
                        "windows": [
                            {
                                "window_id": 7,
                                "is_on_screen": True,
                                "bounds": {"width": 200, "height": 400},
                            }
                        ],
                    }
                ]
            }
        if tool == "type_text":
            return {"ok": True, "text": args.get("text"), "verified": True}
        if tool == "get_window_state":
            if args.get("include_screenshot") and args.get("screenshot_out_file"):
                from PIL import Image

                Image.new("RGB", (320, 200), color=(30, 30, 30)).save(
                    args["screenshot_out_file"]
                )
            return {
                "elements": [],
                "tree_markdown": 'AXStaticText = "17"',
            }
        if tool == "click":
            raise AssertionError("adaptive path must not click")
        raise AssertionError(tool)

    monkeypatch.setattr(driver_mod, "call", fake_call)
    root = tmp_path / "session"
    root.mkdir()
    (root / "artifacts").mkdir()
    ctx = HarnessContext(
        workspace=SessionWorkspace(run_id="t", root=root),
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )
    reg = register_computer_tools(ctx)

    async def _run():
        out = json.loads(
            await reg.get("computer_click_sequence").call(
                app="Calculator",
                labels="All Clear,8,Add,9,Equals",
            )
        )
        assert out["ok"] is True
        assert out["mode"] == "adaptive_text_from_labels"
        assert out["text"] == "8+9="
        assert "click" not in calls
        assert "type_text" in calls
        assert out["timing"]["elapsed_ms"] >= 0
        assert out["screenshot"].endswith("action_0001.png")
        assert out["thumb_path"].endswith("action_0001.jpg")

    asyncio.run(_run())
