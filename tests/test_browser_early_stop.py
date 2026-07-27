"""Browser screenshot early-stop unit tests."""

from __future__ import annotations

from kageha.harness.tools.browser_early_stop import (
    goal_wants_browser_screenshot,
    has_browser_screenshot_evidence,
    select_browser_early_stop,
)


def test_goal_detects_screenshot_request():
    assert goal_wants_browser_screenshot(
        "Open Chrome, go to https://kageha.ca and take a screenshot"
    )
    assert goal_wants_browser_screenshot("capture a screenshot of google.com")
    assert not goal_wants_browser_screenshot("refactor the auth module")


def test_early_stop_on_successful_screenshot():
    hit = select_browser_early_stop(
        [
            ("browser_open", "opened https://kageha.ca"),
            (
                "browser_screenshot",
                "Saved screenshot to artifacts/kageha_ca_screenshot.png",
            ),
        ],
        objective="Open https://kageha.ca and take a screenshot",
    )
    assert hit is not None
    assert hit.tool == "browser_screenshot"
    assert hit.path == "artifacts/kageha_ca_screenshot.png"
    assert "kageha_ca_screenshot.png" in hit.answer


def test_no_early_stop_without_screenshot_goal():
    hit = select_browser_early_stop(
        [
            (
                "browser_screenshot",
                "Saved screenshot to artifacts/midflow.png",
            ),
        ],
        objective="Fill the checkout form and submit the order",
    )
    assert hit is None


def test_has_browser_screenshot_evidence():
    assert has_browser_screenshot_evidence(
        [("browser_screenshot", "path=artifacts/page.png")]
    )
    assert not has_browser_screenshot_evidence([("bash", "ls")])
