"""Tests for the URL-level deduplication guardrail and tool capability metadata.

Covers:
- URL extraction and normalization
- URL dedup warn/block thresholds in after_call
- Pre-emptive blocking in before_call
- Tool capability metadata and alternative suggestions
- Enhanced SWITCH_TOOL steering message integration
"""

from __future__ import annotations


from kageha.loop.tool_guardrails import (
    TOOL_CAPABILITIES,
    ToolCallGuardrailConfig,
    ToolCallGuardrailController,
    _extract_url_from_args,
    suggest_alternatives_for_tool,
)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 1: URL Extraction
# ═══════════════════════════════════════════════════════════════════════


class TestUrlExtraction:
    """Test _extract_url_from_args normalization and edge cases."""

    def test_basic_url(self):
        url = _extract_url_from_args("web_fetch", {"url": "https://example.com/page"})
        assert url == "https://example.com/page"

    def test_strips_query_params(self):
        """Same URL with different query params should normalize to same key."""
        url1 = _extract_url_from_args(
            "web_fetch", {"url": "https://kageha.ca/products/matcha?max_chars=5000"}
        )
        url2 = _extract_url_from_args(
            "web_fetch", {"url": "https://kageha.ca/products/matcha?mode=selective"}
        )
        assert url1 == url2
        assert url1 == "https://kageha.ca/products/matcha"

    def test_strips_fragment(self):
        url = _extract_url_from_args(
            "web_fetch", {"url": "https://example.com/page#section-2"}
        )
        assert url == "https://example.com/page"

    def test_lowercases_host(self):
        url = _extract_url_from_args(
            "web_fetch", {"url": "https://KAGEHA.CA/Products/Matcha"}
        )
        assert url == "https://kageha.ca/Products/Matcha"

    def test_strips_trailing_slash(self):
        url = _extract_url_from_args("web_fetch", {"url": "https://kageha.ca/"})
        # Root path normalizes to just "/" (kept for consistency)
        assert url == "https://kageha.ca/"

    def test_preserves_path(self):
        url = _extract_url_from_args(
            "web_fetch", {"url": "https://kageha.ca/blogs/matcha-guide"}
        )
        assert url == "https://kageha.ca/blogs/matcha-guide"

    def test_non_url_bearing_tool(self):
        """Non-URL tools should return None."""
        url = _extract_url_from_args("bash", {"command": "curl https://example.com"})
        assert url is None

    def test_non_http_url(self):
        """Non-HTTP URLs should return None."""
        url = _extract_url_from_args("web_fetch", {"url": "ftp://example.com/file"})
        assert url is None

    def test_empty_url(self):
        url = _extract_url_from_args("web_fetch", {"url": ""})
        assert url is None

    def test_missing_url_key(self):
        url = _extract_url_from_args("web_fetch", {"query": "search term"})
        assert url is None

    def test_headless_fetch(self):
        """Other URL-bearing tools should work too."""
        url = _extract_url_from_args(
            "headless_fetch", {"url": "https://spa-app.com/dashboard"}
        )
        assert url == "https://spa-app.com/dashboard"

    def test_different_paths_are_different(self):
        """Different paths on same domain should NOT collapse."""
        url1 = _extract_url_from_args("web_fetch", {"url": "https://kageha.ca/products"})
        url2 = _extract_url_from_args("web_fetch", {"url": "https://kageha.ca/blogs"})
        assert url1 != url2


# ═══════════════════════════════════════════════════════════════════════
# SECTION 2: URL Dedup in after_call
# ═══════════════════════════════════════════════════════════════════════


class TestUrlDedupAfterCall:
    """Test the URL dedup detection in after_call."""

    def _make_ctrl(self) -> ToolCallGuardrailController:
        return ToolCallGuardrailController(
            ToolCallGuardrailConfig(enabled=True, hard_stop_enabled=True)
        )

    def test_first_call_allows(self):
        ctrl = self._make_ctrl()
        d = ctrl.after_call("web_fetch", {"url": "https://kageha.ca/page"}, "content")
        assert d.action == "allow"

    def test_second_call_warns(self):
        ctrl = self._make_ctrl()
        ctrl.after_call("web_fetch", {"url": "https://kageha.ca/page"}, "content")
        d = ctrl.after_call(
            "web_fetch",
            {"url": "https://kageha.ca/page", "max_chars": "5000"},
            "content",
        )
        assert d.action == "warn"
        assert d.code == "same_url_repeated_warning"
        assert "already fetched" in d.message
        assert d.steer == "switch_tool"

    def test_third_call_blocks(self):
        ctrl = self._make_ctrl()
        ctrl.after_call("web_fetch", {"url": "https://kageha.ca/page"}, "content")
        ctrl.after_call("web_fetch", {"url": "https://kageha.ca/page?a=1"}, "content")
        d = ctrl.after_call("web_fetch", {"url": "https://kageha.ca/page?b=2"}, "more")
        assert d.action == "block"
        assert d.code == "same_url_repeated_block"
        assert "BLOCKED" in d.message
        assert "CANNOT" in d.message  # capability hint
        assert d.steer == "switch_tool"

    def test_different_urls_dont_trigger(self):
        ctrl = self._make_ctrl()
        ctrl.after_call("web_fetch", {"url": "https://kageha.ca/page1"}, "content1")
        ctrl.after_call("web_fetch", {"url": "https://kageha.ca/page2"}, "content2")
        d = ctrl.after_call("web_fetch", {"url": "https://kageha.ca/page3"}, "content3")
        assert d.action == "allow"

    def test_mixed_url_and_non_url_tools(self):
        """Non-URL tools interleaved shouldn't affect URL counts."""
        ctrl = self._make_ctrl()
        ctrl.after_call("web_fetch", {"url": "https://kageha.ca/page"}, "content")
        ctrl.after_call("bash", {"command": "echo hi"}, '{"exit_code": 0}')
        d = ctrl.after_call("web_fetch", {"url": "https://kageha.ca/page"}, "content")
        # Either URL dedup or idempotent no-progress fires (both valid)
        assert d.action == "warn"
        assert d.code in ("same_url_repeated_warning", "idempotent_no_progress_warning")

    def test_reset_clears_url_tracker(self):
        """New turn should reset URL counts."""
        ctrl = self._make_ctrl()
        ctrl.after_call("web_fetch", {"url": "https://kageha.ca/page"}, "content")
        ctrl.after_call("web_fetch", {"url": "https://kageha.ca/page"}, "content")
        ctrl.reset_for_turn()
        d = ctrl.after_call("web_fetch", {"url": "https://kageha.ca/page"}, "content")
        assert d.action == "allow"


# ═══════════════════════════════════════════════════════════════════════
# SECTION 3: URL Dedup in before_call (pre-emptive block)
# ═══════════════════════════════════════════════════════════════════════


class TestUrlDedupBeforeCall:
    """Test pre-emptive blocking in before_call."""

    def _make_ctrl(self) -> ToolCallGuardrailController:
        return ToolCallGuardrailController(
            ToolCallGuardrailConfig(enabled=True, hard_stop_enabled=True)
        )

    def test_before_call_blocks_after_threshold(self):
        ctrl = self._make_ctrl()
        # Accumulate via after_call
        ctrl.after_call("web_fetch", {"url": "https://kageha.ca/x"}, "content")
        ctrl.after_call("web_fetch", {"url": "https://kageha.ca/x?a=1"}, "content")
        ctrl.after_call("web_fetch", {"url": "https://kageha.ca/x?b=2"}, "content")
        # Now before_call should pre-emptively block
        d = ctrl.before_call("web_fetch", {"url": "https://kageha.ca/x?c=3"})
        assert d.action == "block"
        assert d.code == "same_url_repeated_block"
        assert "already fetched" in d.message
        assert d.steer == "switch_tool"

    def test_before_call_allows_below_threshold(self):
        ctrl = self._make_ctrl()
        ctrl.after_call("web_fetch", {"url": "https://kageha.ca/x"}, "content")
        d = ctrl.before_call("web_fetch", {"url": "https://kageha.ca/x?a=1"})
        # Only 1 prior call — below block threshold (3)
        assert d.action != "block"

    def test_before_call_different_url_allows(self):
        ctrl = self._make_ctrl()
        ctrl.after_call("web_fetch", {"url": "https://kageha.ca/x"}, "c")
        ctrl.after_call("web_fetch", {"url": "https://kageha.ca/x"}, "c")
        ctrl.after_call("web_fetch", {"url": "https://kageha.ca/x"}, "c")
        # Different URL should still be allowed
        d = ctrl.before_call("web_fetch", {"url": "https://kageha.ca/other"})
        assert d.action != "block"


# ═══════════════════════════════════════════════════════════════════════
# SECTION 4: Tool Capability Metadata
# ═══════════════════════════════════════════════════════════════════════


class TestToolCapabilities:
    """Test the TOOL_CAPABILITIES dict and suggest_alternatives_for_tool."""

    def test_web_fetch_capabilities_exist(self):
        assert "web_fetch" in TOOL_CAPABILITIES
        caps = TOOL_CAPABILITIES["web_fetch"]
        assert "can_do" in caps
        assert "cannot_do" in caps
        assert "alternatives" in caps

    def test_cannot_do_includes_images(self):
        caps = TOOL_CAPABILITIES["web_fetch"]
        cannot = " ".join(caps["cannot_do"]).lower()
        assert "image" in cannot

    def test_suggest_for_image_goal(self):
        alt = suggest_alternatives_for_tool("web_fetch", goal_hint="extract images from page")
        assert "CANNOT" in alt
        assert "browser_snapshot" in alt or "browser" in alt.lower()

    def test_suggest_for_js_goal(self):
        alt = suggest_alternatives_for_tool("web_fetch", goal_hint="render javascript SPA")
        assert "CANNOT" in alt
        assert "browser" in alt.lower()

    def test_suggest_unknown_tool(self):
        """Unknown tools should return empty string."""
        alt = suggest_alternatives_for_tool("some_random_tool", goal_hint="anything")
        assert alt == ""

    def test_suggest_no_goal_hint(self):
        """Without goal hint, should still return something useful."""
        alt = suggest_alternatives_for_tool("web_fetch")
        assert "CANNOT" in alt
        assert len(alt) > 50


# ═══════════════════════════════════════════════════════════════════════
# SECTION 5: Enhanced SWITCH_TOOL Steering Integration
# ═══════════════════════════════════════════════════════════════════════


class TestSwitchToolSteering:
    """Test the enhanced switch_tool_steering_message with alternatives."""

    def test_steering_includes_alternatives(self):
        from kageha.loop.adaptive import switch_tool_steering_message
        from kageha.loop.task_state import FailureRecord, TaskState

        state = TaskState(objective="Download product images for Instagram carousel")
        state.failures.append(
            FailureRecord(
                step=5,
                action="web_fetch",
                result="text without image URLs",
                cause="text extraction has no image tags",
                kind="bad_output",
                required_change="use browser_snapshot for visual content",
            )
        )
        msg = switch_tool_steering_message(state)
        assert "SWITCH_TOOL" in msg
        assert "CANNOT" in msg
        assert "browser_snapshot" in msg or "browser" in msg.lower()
        assert "image" in msg.lower()

    def test_steering_without_failures(self):
        from kageha.loop.adaptive import switch_tool_steering_message
        from kageha.loop.task_state import TaskState

        state = TaskState(objective="do stuff")
        state.control_reason = "generic timeout"
        msg = switch_tool_steering_message(state)
        assert "SWITCH_TOOL" in msg
        assert "Do NOT retry" in msg

    def test_steering_non_web_tool(self):
        """Non-web tools shouldn't get web-specific alternatives."""
        from kageha.loop.adaptive import switch_tool_steering_message
        from kageha.loop.task_state import FailureRecord, TaskState

        state = TaskState(objective="compile the project")
        state.failures.append(
            FailureRecord(
                step=2,
                action="bash",
                result="exit code 1",
                cause="compile error",
                kind="tool_error",
                required_change="fix syntax",
            )
        )
        msg = switch_tool_steering_message(state)
        assert "SWITCH_TOOL" in msg
        # bash isn't in TOOL_CAPABILITIES so no capability block
        assert "CANNOT" not in msg


# ═══════════════════════════════════════════════════════════════════════
# SECTION 6: End-to-End Scenario (the web_fetch loop)
# ═══════════════════════════════════════════════════════════════════════


class TestWebFetchLoopScenario:
    """Simulate the exact scenario that caused 13 wasted calls."""

    def test_web_fetch_loop_blocked_at_3(self):
        """The agent fetching same URL with varying params gets blocked at call 3."""
        ctrl = ToolCallGuardrailController(
            ToolCallGuardrailConfig(enabled=True, hard_stop_enabled=True)
        )

        url = "https://kageha.ca/products/classic-ceremonial-matcha"

        # Call 1: normal fetch
        d1 = ctrl.after_call(
            "web_fetch",
            {"url": url, "max_chars": "12000"},
            "Daily Ceremonial Matcha...",
        )
        assert d1.action == "allow"

        # Call 2: same URL, different params → WARN
        d2 = ctrl.after_call(
            "web_fetch",
            {"url": url, "max_chars": "20000"},
            "Daily Ceremonial Matcha...",
        )
        assert d2.action == "warn"
        assert "same_url_repeated" in d2.code
        assert "already fetched" in d2.message

        # Call 3: same URL again → BLOCK
        d3 = ctrl.after_call(
            "web_fetch",
            {"url": url, "mode": "selective", "search_phrase": "image"},
            "Daily Ceremonial...",
        )
        assert d3.action == "block"
        assert d3.steer == "switch_tool"
        assert "CANNOT" in d3.message
        assert "browser_snapshot" in d3.message

        # Call 4: before_call should also block
        d4 = ctrl.before_call(
            "web_fetch",
            {"url": url, "max_chars": "5000"},
        )
        assert d4.action == "block"

    def test_different_domain_not_affected(self):
        """Fetching a DIFFERENT URL is unaffected by the dedup on the first."""
        ctrl = ToolCallGuardrailController(
            ToolCallGuardrailConfig(enabled=True, hard_stop_enabled=True)
        )

        # Hit the first URL 3 times (gets blocked)
        ctrl.after_call("web_fetch", {"url": "https://a.com/x"}, "c")
        ctrl.after_call("web_fetch", {"url": "https://a.com/x?p=1"}, "c")
        ctrl.after_call("web_fetch", {"url": "https://a.com/x?p=2"}, "c")

        # Different URL should be fine
        d = ctrl.after_call("web_fetch", {"url": "https://b.com/other"}, "content")
        assert d.code != "same_url_repeated_block"

    def test_custom_thresholds(self):
        """Custom config can make it more or less aggressive."""
        ctrl = ToolCallGuardrailController(
            ToolCallGuardrailConfig(
                enabled=True,
                hard_stop_enabled=True,
                url_dedup_warn_after=3,
                url_dedup_block_after=5,
            )
        )
        url = "https://example.com/page"
        for i in range(4):
            ctrl.after_call("web_fetch", {"url": f"{url}?i={i}"}, "content")

        # At 4 calls with warn_after=3, should be warning
        d = ctrl.after_call("web_fetch", {"url": f"{url}?i=4"}, "content")
        assert d.action == "block"  # 5th call hits block_after=5
