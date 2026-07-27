#!/usr/bin/env python3
"""Spike scorecard for cua-driver against common macOS apps.

Usage (macOS, driver installed + permissions granted):
  uv run python scripts/computer_driver_spike.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kageha.harness.tools import computer_driver as driver  # noqa: E402

TARGETS = (
    ("Calculator", "com.apple.calculator"),
    ("TextEdit", "com.apple.textedit"),
    ("Finder", "com.apple.finder"),
    ("Safari", "com.apple.Safari"),
    ("Google Chrome", "com.google.Chrome"),
)


async def score_one(name: str, bundle_id: str) -> dict:
    row: dict = {"app": name, "bundle_id": bundle_id}
    try:
        launched = await driver.call("launch_app", {"bundle_id": bundle_id})
        pid = int(launched.get("pid") or 0)
        wins = launched.get("windows") or []
        row["launch_ok"] = pid > 0
        row["self_activation_suppressed"] = launched.get("self_activation_suppressed")
        if not wins:
            lw = await driver.call("list_windows", {"pid": pid})
            wins = lw.get("windows") or []
        if not wins:
            row["error"] = "no windows"
            return row
        wid = int(wins[0]["window_id"])
        before = None
        apps = (await driver.call("list_apps", {})).get("apps") or []
        for a in apps:
            if a.get("active"):
                before = a.get("name")
                break
        state = await driver.call(
            "get_window_state",
            {
                "pid": pid,
                "window_id": wid,
                "include_screenshot": False,
                "max_elements": 40,
            },
        )
        els = state.get("elements") or []
        row["element_count"] = len(els)
        row["degraded"] = bool(state.get("degraded"))
        # Click first button-like element if any
        click_idx = None
        for el in els:
            role = str(el.get("role") or "").lower()
            if "button" in role or role in {"axbutton", "button"}:
                click_idx = int(el["element_index"])
                break
        if click_idx is not None:
            await driver.call(
                "click",
                {
                    "pid": pid,
                    "window_id": wid,
                    "element_index": click_idx,
                    "delivery_mode": "background",
                },
            )
            row["click_ok"] = True
        else:
            row["click_ok"] = False
            row["click_skip"] = "no button element"
        after = None
        apps = (await driver.call("list_apps", {})).get("apps") or []
        for a in apps:
            if a.get("active"):
                after = a.get("name")
                break
        row["frontmost_before"] = before
        row["frontmost_after"] = after
        row["focus_stolen"] = bool(before and after and before != after and after == name)
    except Exception as exc:  # noqa: BLE001
        row["error"] = str(exc)[:300]
    return row


async def main() -> int:
    if not driver.driver_available():
        print("cua-driver not available", file=sys.stderr)
        return 2
    perms = await driver.permissions_status()
    print("permissions:", json.dumps(perms, indent=2))
    results = []
    for name, bid in TARGETS:
        print(f"… {name}")
        results.append(await score_one(name, bid))
    print(json.dumps({"results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
