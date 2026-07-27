"""Pure tool-call loop guardrails (Hermes-style) + post-checkpoint guard (OpenClaw).

Side-effect free: tracks per-turn observations and returns decisions. The loop
controller owns whether those become tool-result guidance or a hard halt.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

# Read-only / idempotent tools — unchanged results mean a stuck loop.
IDEMPOTENT_TOOL_NAMES = frozenset(
    {
        "read_file",
        "list_files",
        "web_search",
        "web_fetch",
        "http_get",
        "research_run",
        "parallel_web_fetch",
        "headless_fetch",
        "browser_snapshot",
        "browser_console",
        "browser_get_images",
        "session_search",
        "memory_recall",
        "memory_fetch",
        "memory_inspect",
        "kb_search",
        "skill_list",
        # computer_get_state is intentionally NOT idempotent-guarded: the
        # observe→act→observe CUA loop must re-snapshot every turn.
        "computer_list_apps",
        "computer_doctor",
        "computer_screenshot",
    }
)

MUTATING_TOOL_NAMES = frozenset(
    {
        "write_file",
        "bash",
        "shell",
        "browser_click",
        "browser_type",
        "browser_press",
        "browser_scroll",
        "browser_open",
        "browser_navigate",
        "ask_human",
        "spawn_subagent",
        "todo",
        "memory_remember",
        "memory_correct",
        "memory_forget",
        "skill_run",
        "computer_click",
        "computer_type",
        "computer_key",
        "computer_hotkey",
        "computer_scroll",
        "computer_set_value",
        "computer_launch",
        "computer_move",
    }
)

_VOLATILE_ARG_KEYS = frozenset(
    {
        "timestamp",
        "ts",
        "pid",
        "session_id",
        "run_id",
        "request_id",
        "duration",
        "duration_ms",
        "elapsed",
        "cwd",
        "working_directory",
    }
)


@dataclass(frozen=True)
class ToolCallGuardrailConfig:
    """Thresholds for per-turn tool-call loop detection."""

    enabled: bool = True
    hard_stop_enabled: bool = True
    platform: str = "cli"
    exact_failure_warn_after: int = 2
    exact_failure_block_after: int = 4
    same_tool_failure_warn_after: int = 3
    same_tool_failure_halt_after: int = 6
    no_progress_warn_after: int = 2
    no_progress_block_after: int = 4
    history_size: int = 20
    ping_pong_warn_after: int = 4
    ping_pong_halt_after: int = 6
    global_breaker_warn_after: int = 8
    global_breaker_halt_after: int = 12
    unknown_tool_warn_after: int = 2
    unknown_tool_block_after: int = 4
    stagnant_tools_warn_after: int = 6
    stagnant_tools_halt_after: int = 10
    idempotent_tools: frozenset[str] = field(default_factory=lambda: IDEMPOTENT_TOOL_NAMES)
    mutating_tools: frozenset[str] = field(default_factory=lambda: MUTATING_TOOL_NAMES)

    @classmethod
    def from_env(cls, platform: str | None = None) -> "ToolCallGuardrailConfig":
        from kageha.config import (
            normalize_platform,
            tool_guardrails_enabled,
            tool_guardrails_hard_stop,
            tool_guardrails_thresholds,
        )

        plat = normalize_platform(platform)
        t = tool_guardrails_thresholds()
        return cls(
            enabled=tool_guardrails_enabled(),
            hard_stop_enabled=tool_guardrails_hard_stop(plat),
            platform=plat,
            exact_failure_warn_after=t["exact_failure_warn"],
            exact_failure_block_after=t["exact_failure_block"],
            same_tool_failure_warn_after=t["same_tool_failure_warn"],
            same_tool_failure_halt_after=t["same_tool_failure_halt"],
            no_progress_warn_after=t["no_progress_warn"],
            no_progress_block_after=t["no_progress_block"],
            history_size=t["history_size"],
            ping_pong_warn_after=t["ping_pong_warn"],
            ping_pong_halt_after=t["ping_pong_halt"],
            global_breaker_warn_after=t["global_breaker_warn"],
            global_breaker_halt_after=t["global_breaker_halt"],
            unknown_tool_warn_after=t["unknown_tool_warn"],
            unknown_tool_block_after=t["unknown_tool_block"],
            stagnant_tools_warn_after=t["stagnant_tools_warn"],
            stagnant_tools_halt_after=t["stagnant_tools_halt"],
        )


@dataclass(frozen=True)
class ToolCallSignature:
    tool_name: str
    args_hash: str

    @classmethod
    def from_call(cls, tool_name: str, args: Mapping[str, Any] | None) -> "ToolCallSignature":
        canonical = canonical_tool_args(args or {})
        return cls(tool_name=tool_name, args_hash=_sha256(canonical))

    def to_metadata(self) -> dict[str, str]:
        return {"tool_name": self.tool_name, "args_hash": self.args_hash}


@dataclass(frozen=True)
class ToolGuardrailDecision:
    action: str = "allow"  # allow | warn | block | halt
    code: str = "allow"
    message: str = ""
    tool_name: str = ""
    count: int = 0
    signature: ToolCallSignature | None = None
    result_hash: str = ""
    # Structured steer hint for the loop controller (Hermes SWITCH_TOOL / RETRY).
    steer: str = ""  # "" | "retry" | "switch_tool"

    @property
    def allows_execution(self) -> bool:
        return self.action in {"allow", "warn"}

    @property
    def should_halt(self) -> bool:
        return self.action in {"block", "halt"}

    def to_metadata(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "action": self.action,
            "code": self.code,
            "message": self.message,
            "tool_name": self.tool_name,
            "count": self.count,
        }
        if self.steer:
            data["steer"] = self.steer
        if self.signature is not None:
            data["signature"] = self.signature.to_metadata()
        if self.result_hash:
            data["result_hash"] = self.result_hash
        return data


def is_unknown_tool_error(result: str | None) -> bool:
    text = (result or "").strip()
    return bool(re.match(r"(?i)^ERROR:\s*unknown tool\b", text))


def canonical_tool_args(args: Mapping[str, Any]) -> str:
    """Sorted compact JSON with volatile keys stripped."""
    cleaned = _strip_volatile(args if isinstance(args, Mapping) else {})
    return json.dumps(
        cleaned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def classify_tool_failure(tool_name: str, result: str | None) -> bool:
    """Mirror TaskState.record_tool failure heuristics."""
    content = result or ""
    lowered = content.lower()
    if content.startswith("ERROR") or "DENIED:" in content:
        return True
    if any(
        marker in lowered
        for marker in (
            "captcha",
            "access denied",
            "unusual traffic",
            "verify you are human",
            "challenge page",
            "login required",
            "sign in to continue",
        )
    ):
        return True
    if tool_name in {"bash", "shell"}:
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            data = None
        if isinstance(data, dict):
            exit_code = data.get("exit_code")
            if exit_code is not None and int(exit_code) != 0:
                return True
    if re.search(r'(?i)"(?:error|failed)"\s*:', content[:500]) or content.startswith(
        "Error"
    ):
        return True
    return False


def result_hash(result: str | None) -> str:
    """Stable hash of tool output (JSON-canonical when parseable)."""
    raw = result or ""
    # Drop prior guardrail guidance suffixes so retries compare cleanly.
    raw = re.sub(
        r"\n\n\[Tool loop (?:warning|hard stop):[^\]]*\]\s*$",
        "",
        raw,
        flags=re.I,
    )
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if parsed is not None:
        try:
            if isinstance(parsed, dict):
                parsed = _strip_volatile(parsed)
            canonical = json.dumps(
                parsed,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        except TypeError:
            canonical = str(parsed)
    else:
        # Normalize whitespace for text results.
        canonical = re.sub(r"\s+", " ", raw).strip()
    return _sha256(canonical)


@dataclass(frozen=True)
class _HistoryEntry:
    tool_name: str
    args_hash: str
    result_hash: str

    @property
    def triple_key(self) -> str:
        return f"{self.tool_name}|{self.args_hash}|{self.result_hash}"

    @property
    def pair_key(self) -> str:
        return f"{self.tool_name}|{self.args_hash}"


class ToolCallGuardrailController:
    """Per-turn controller for repeated failed / non-progressing tool calls."""

    def __init__(self, config: ToolCallGuardrailConfig | None = None):
        self.config = config or ToolCallGuardrailConfig()
        self.reset_for_turn()

    def reset_for_turn(self) -> None:
        self._exact_failure_counts: dict[ToolCallSignature, int] = {}
        self._same_tool_failure_counts: dict[str, int] = {}
        self._no_progress: dict[ToolCallSignature, tuple[str, int]] = {}
        self._history: list[_HistoryEntry] = []
        self._halt_decision: ToolGuardrailDecision | None = None
        self._unknown_tool_count: int = 0
        self._seen_triples: set[str] = set()
        self._stagnant_tools_streak: int = 0
        self._pending_steer: str = ""

    @property
    def halt_decision(self) -> ToolGuardrailDecision | None:
        return self._halt_decision

    @property
    def pending_steer(self) -> str:
        return self._pending_steer

    def consume_steer(self) -> str:
        steer = self._pending_steer
        self._pending_steer = ""
        return steer

    def before_call(
        self, tool_name: str, args: Mapping[str, Any] | None
    ) -> ToolGuardrailDecision:
        signature = ToolCallSignature.from_call(tool_name, args)
        if not self.config.enabled or not self.config.hard_stop_enabled:
            return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

        if self._unknown_tool_count >= self.config.unknown_tool_block_after:
            decision = ToolGuardrailDecision(
                action="block",
                code="unknown_tool_block",
                message=(
                    f"Blocked {tool_name}: {self._unknown_tool_count} consecutive "
                    "unknown-tool errors. Use only tools from the available schema "
                    "or stop and ask the user."
                ),
                tool_name=tool_name,
                count=self._unknown_tool_count,
                signature=signature,
                steer="switch_tool",
            )
            self._halt_decision = decision
            self._pending_steer = "switch_tool"
            return decision

        if self._stagnant_tools_streak >= self.config.stagnant_tools_halt_after:
            decision = ToolGuardrailDecision(
                action="block",
                code="stagnant_with_tools_block",
                message=(
                    f"Blocked {tool_name}: {self._stagnant_tools_streak} tool calls "
                    "with no new progress. Stop calling tools that repeat prior "
                    "outcomes; summarize or change approach."
                ),
                tool_name=tool_name,
                count=self._stagnant_tools_streak,
                signature=signature,
                steer="switch_tool",
            )
            self._halt_decision = decision
            self._pending_steer = "switch_tool"
            return decision

        exact_count = self._exact_failure_counts.get(signature, 0)
        if exact_count >= self.config.exact_failure_block_after:
            decision = ToolGuardrailDecision(
                action="block",
                code="repeated_exact_failure_block",
                message=(
                    f"Blocked {tool_name}: the same tool call failed {exact_count} "
                    "times with identical arguments. Stop retrying it unchanged; "
                    "change strategy or explain the blocker."
                ),
                tool_name=tool_name,
                count=exact_count,
                signature=signature,
            )
            self._halt_decision = decision
            return decision

        if self._is_idempotent(tool_name):
            record = self._no_progress.get(signature)
            if record is not None:
                _rh, repeat_count = record
                if repeat_count >= self.config.no_progress_block_after:
                    decision = ToolGuardrailDecision(
                        action="block",
                        code="idempotent_no_progress_block",
                        message=(
                            f"Blocked {tool_name}: this read-only call returned the "
                            f"same result {repeat_count} times. Stop repeating it; "
                            "use the result already provided or try a different query."
                        ),
                        tool_name=tool_name,
                        count=repeat_count,
                        signature=signature,
                        result_hash=_rh,
                    )
                    self._halt_decision = decision
                    return decision

        return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

    def after_call(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None,
        result: str | None,
        *,
        failed: bool | None = None,
    ) -> ToolGuardrailDecision:
        if not self.config.enabled:
            return ToolGuardrailDecision(tool_name=tool_name)

        signature = ToolCallSignature.from_call(tool_name, args)
        if failed is None:
            failed = classify_tool_failure(tool_name, result)

        rh = result_hash(result)
        primary: ToolGuardrailDecision

        # OpenClaw-style unknown-tool retry detector (before generic failure path).
        if is_unknown_tool_error(result):
            self._unknown_tool_count += 1
            count = self._unknown_tool_count
            if (
                self.config.hard_stop_enabled
                and count >= self.config.unknown_tool_block_after
            ):
                primary = ToolGuardrailDecision(
                    action="halt",
                    code="unknown_tool_halt",
                    message=(
                        f"Stopped: {count} consecutive unknown-tool errors "
                        f"(last: {tool_name}). Use only declared tools."
                    ),
                    tool_name=tool_name,
                    count=count,
                    signature=signature,
                    result_hash=rh,
                    steer="switch_tool",
                )
            elif count >= self.config.unknown_tool_warn_after:
                primary = ToolGuardrailDecision(
                    action="warn",
                    code="unknown_tool_warning",
                    message=(
                        f"{count} consecutive unknown-tool errors (last: {tool_name}). "
                        "Pick a tool from the available schema — do not invent names."
                    ),
                    tool_name=tool_name,
                    count=count,
                    signature=signature,
                    result_hash=rh,
                    steer="switch_tool",
                )
            else:
                primary = ToolGuardrailDecision(
                    tool_name=tool_name,
                    count=count,
                    signature=signature,
                    result_hash=rh,
                    steer="switch_tool",
                )
            if primary.steer:
                self._pending_steer = primary.steer
            history_decision = self._observe_history(
                tool_name=tool_name,
                signature=signature,
                result_hash_value=rh,
                is_progress=False,
            )
            return self._merge_decisions(primary, history_decision)

        self._unknown_tool_count = 0

        if failed:
            exact_count = self._exact_failure_counts.get(signature, 0) + 1
            self._exact_failure_counts[signature] = exact_count
            self._no_progress.pop(signature, None)

            same_count = self._same_tool_failure_counts.get(tool_name, 0) + 1
            self._same_tool_failure_counts[tool_name] = same_count

            if (
                self.config.hard_stop_enabled
                and same_count >= self.config.same_tool_failure_halt_after
            ):
                primary = ToolGuardrailDecision(
                    action="halt",
                    code="same_tool_failure_halt",
                    message=(
                        f"Stopped {tool_name}: it failed {same_count} times this turn. "
                        "Stop retrying the same failing tool path and choose a "
                        "different approach."
                    ),
                    tool_name=tool_name,
                    count=same_count,
                    signature=signature,
                    result_hash=rh,
                    steer="switch_tool",
                )
            elif exact_count >= self.config.exact_failure_warn_after:
                primary = ToolGuardrailDecision(
                    action="warn",
                    code="repeated_exact_failure_warning",
                    message=(
                        f"{tool_name} has failed {exact_count} times with identical "
                        "arguments. This looks like a loop; inspect the error and "
                        "change strategy instead of retrying it unchanged."
                    ),
                    tool_name=tool_name,
                    count=exact_count,
                    signature=signature,
                    result_hash=rh,
                    steer="retry",
                )
            elif same_count >= self.config.same_tool_failure_warn_after:
                primary = ToolGuardrailDecision(
                    action="warn",
                    code="same_tool_failure_warning",
                    message=(
                        f"{tool_name} has failed {same_count} times this turn. "
                        "Diagnose before retrying; try different arguments or a "
                        "different tool."
                    ),
                    tool_name=tool_name,
                    count=same_count,
                    signature=signature,
                    result_hash=rh,
                    steer="switch_tool",
                )
            else:
                primary = ToolGuardrailDecision(
                    tool_name=tool_name,
                    count=exact_count,
                    signature=signature,
                    result_hash=rh,
                )
            is_progress = False
        else:
            # Success clears failure counters for this signature / tool.
            self._exact_failure_counts.pop(signature, None)
            self._same_tool_failure_counts.pop(tool_name, None)

            if not self._is_idempotent(tool_name):
                self._no_progress.pop(signature, None)
                primary = ToolGuardrailDecision(
                    tool_name=tool_name, signature=signature, result_hash=rh
                )
                is_progress = True
            else:
                previous = self._no_progress.get(signature)
                repeat_count = 1
                if previous is not None and previous[0] == rh:
                    repeat_count = previous[1] + 1
                self._no_progress[signature] = (rh, repeat_count)

                if (
                    self.config.hard_stop_enabled
                    and repeat_count >= self.config.no_progress_block_after
                ):
                    primary = ToolGuardrailDecision(
                        action="halt",
                        code="idempotent_no_progress_halt",
                        message=(
                            f"Stopped {tool_name}: returned the same result "
                            f"{repeat_count} times. Use the result already provided "
                            "or change the query."
                        ),
                        tool_name=tool_name,
                        count=repeat_count,
                        signature=signature,
                        result_hash=rh,
                        steer="switch_tool",
                    )
                    is_progress = False
                elif repeat_count >= self.config.no_progress_warn_after:
                    primary = ToolGuardrailDecision(
                        action="warn",
                        code="idempotent_no_progress_warning",
                        message=(
                            f"{tool_name} returned the same result {repeat_count} times. "
                            "Use the result already provided or change the query instead "
                            "of repeating it unchanged."
                        ),
                        tool_name=tool_name,
                        count=repeat_count,
                        signature=signature,
                        result_hash=rh,
                        steer="retry",
                    )
                    is_progress = False
                else:
                    primary = ToolGuardrailDecision(
                        tool_name=tool_name,
                        count=repeat_count,
                        signature=signature,
                        result_hash=rh,
                    )
                    is_progress = repeat_count == 1

        if primary.steer:
            self._pending_steer = primary.steer

        # OpenClaw-style rolling history: ping-pong + global + stagnant-with-tools.
        history_decision = self._observe_history(
            tool_name=tool_name,
            signature=signature,
            result_hash_value=rh,
            is_progress=is_progress,
        )
        return self._merge_decisions(primary, history_decision)

    def _observe_history(
        self,
        *,
        tool_name: str,
        signature: ToolCallSignature,
        result_hash_value: str,
        is_progress: bool = False,
    ) -> ToolGuardrailDecision:
        entry = _HistoryEntry(
            tool_name=tool_name,
            args_hash=signature.args_hash,
            result_hash=result_hash_value,
        )
        self._history.append(entry)
        max_hist = max(4, self.config.history_size)
        if len(self._history) > max_hist:
            self._history = self._history[-max_hist:]

        triple = entry.triple_key
        # Stagnant-with-tools: keep calling tools but outcomes aren't new.
        if is_progress and triple not in self._seen_triples:
            self._seen_triples.add(triple)
            self._stagnant_tools_streak = 0
        else:
            if triple not in self._seen_triples:
                self._seen_triples.add(triple)
                # First sighting of a failure/no-progress triple still counts as
                # "new information" once; repeats deepen stagnation.
                if is_progress:
                    self._stagnant_tools_streak = 0
                else:
                    self._stagnant_tools_streak += 1
            else:
                self._stagnant_tools_streak += 1

        if (
            self.config.hard_stop_enabled
            and self._stagnant_tools_streak >= self.config.stagnant_tools_halt_after
        ):
            decision = ToolGuardrailDecision(
                action="halt",
                code="stagnant_with_tools_halt",
                message=(
                    f"CRITICAL: {self._stagnant_tools_streak} tool calls with no new "
                    "progress. Stop the tool loop and change approach or ask the user."
                ),
                tool_name=tool_name,
                count=self._stagnant_tools_streak,
                signature=signature,
                result_hash=result_hash_value,
                steer="switch_tool",
            )
            self._halt_decision = decision
            self._pending_steer = "switch_tool"
            return decision
        if self._stagnant_tools_streak >= self.config.stagnant_tools_warn_after:
            self._pending_steer = "switch_tool"
            return ToolGuardrailDecision(
                action="warn",
                code="stagnant_with_tools_warning",
                message=(
                    f"{self._stagnant_tools_streak} consecutive tool calls without new "
                    "progress. Switch tools or strategy instead of repeating."
                ),
                tool_name=tool_name,
                count=self._stagnant_tools_streak,
                signature=signature,
                result_hash=result_hash_value,
                steer="switch_tool",
            )

        # Global circuit breaker: identical (tool, args, result) streak in window.
        identical = sum(1 for item in self._history if item.triple_key == triple)
        if (
            self.config.hard_stop_enabled
            and identical >= self.config.global_breaker_halt_after
        ):
            decision = ToolGuardrailDecision(
                action="halt",
                code="global_circuit_breaker",
                message=(
                    f"CRITICAL: {tool_name} repeated identical no-progress outcomes "
                    f"{identical} times. Session blocked to prevent runaway loops."
                ),
                tool_name=tool_name,
                count=identical,
                signature=signature,
                result_hash=result_hash_value,
                steer="switch_tool",
            )
            self._halt_decision = decision
            self._pending_steer = "switch_tool"
            return decision
        if identical >= self.config.global_breaker_warn_after:
            return ToolGuardrailDecision(
                action="warn",
                code="global_circuit_breaker_warning",
                message=(
                    f"{tool_name} has repeated identical outcomes {identical} times. "
                    "Stop this path and change strategy."
                ),
                tool_name=tool_name,
                count=identical,
                signature=signature,
                result_hash=result_hash_value,
                steer="switch_tool",
            )

        # Ping-pong: A,B,A,B… with stable results (no progress).
        ping = self._ping_pong_streak()
        if ping >= self.config.ping_pong_halt_after and self.config.hard_stop_enabled:
            decision = ToolGuardrailDecision(
                action="halt",
                code="ping_pong_halt",
                message=(
                    f"CRITICAL: alternating tool-call pattern lasting {ping} calls "
                    "with no progress. Stop the ping-pong and pick one approach."
                ),
                tool_name=tool_name,
                count=ping,
                signature=signature,
                result_hash=result_hash_value,
                steer="switch_tool",
            )
            self._halt_decision = decision
            self._pending_steer = "switch_tool"
            return decision
        if ping >= self.config.ping_pong_warn_after:
            return ToolGuardrailDecision(
                action="warn",
                code="ping_pong_warning",
                message=(
                    f"You are alternating between two tool calls ({ping} consecutive). "
                    "This looks like a stuck ping-pong loop — consolidate into one plan."
                ),
                tool_name=tool_name,
                count=ping,
                signature=signature,
                result_hash=result_hash_value,
                steer="switch_tool",
            )

        return ToolGuardrailDecision(
            tool_name=tool_name,
            signature=signature,
            result_hash=result_hash_value,
        )

    def _ping_pong_streak(self) -> int:
        """Length of trailing A,B,A,B… pattern over pair keys (tool+args)."""
        hist = self._history
        if len(hist) < 4:
            return 0
        a = hist[-1].pair_key
        b = hist[-2].pair_key
        if a == b:
            return 0
        streak = 2
        expect_a = True  # hist[-3] should match a, hist[-4] match b, …
        for idx in range(len(hist) - 3, -1, -1):
            want = a if expect_a else b
            if hist[idx].pair_key != want:
                break
            streak += 1
            expect_a = not expect_a
        # Require true alternation of at least two distinct tools/args.
        return streak if streak >= 4 else 0

    def _merge_decisions(
        self,
        primary: ToolGuardrailDecision,
        secondary: ToolGuardrailDecision,
    ) -> ToolGuardrailDecision:
        """Prefer halt > block > warn > allow; preserve strongest steer."""
        rank = {"halt": 3, "block": 2, "warn": 1, "allow": 0}
        if rank.get(secondary.action, 0) > rank.get(primary.action, 0):
            chosen = secondary
        else:
            chosen = primary
        if not chosen.steer and (primary.steer or secondary.steer):
            # Prefer switch_tool over retry when both appear.
            steer = "switch_tool" if "switch_tool" in {
                primary.steer, secondary.steer
            } else (primary.steer or secondary.steer)
            chosen = ToolGuardrailDecision(
                action=chosen.action,
                code=chosen.code,
                message=chosen.message,
                tool_name=chosen.tool_name,
                count=chosen.count,
                signature=chosen.signature,
                result_hash=chosen.result_hash,
                steer=steer,
            )
        if chosen.should_halt:
            self._halt_decision = chosen
        if chosen.steer:
            self._pending_steer = chosen.steer
        return chosen

    def _is_idempotent(self, tool_name: str) -> bool:
        if tool_name in self.config.mutating_tools:
            return False
        return tool_name in self.config.idempotent_tools


@dataclass
class PostCheckpointGuardObservation:
    tool_name: str
    args_hash: str
    result_hash: str


@dataclass
class PostCheckpointGuardVerdict:
    should_abort: bool = False
    armed: bool = False
    remaining_attempts: int = 0
    detector: str = ""
    count: int = 0
    tool_name: str = ""
    message: str = ""


class PostCheckpointGuard:
    """OpenClaw-style short-window guard after history compaction.

    Arms for ``window_size`` observations. Aborts if the same
    (tool, argsHash, resultHash) triple appears ``abort_after`` times
    within the armed window.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        window_size: int = 4,
        abort_after: int = 3,
    ):
        self.enabled = enabled
        self.window_size = max(1, window_size)
        self.abort_after = max(2, abort_after)
        self._remaining = 0
        self._history: list[PostCheckpointGuardObservation] = []

    def arm(self) -> None:
        if not self.enabled:
            return
        self._remaining = self.window_size
        self._history = []

    @property
    def armed(self) -> bool:
        return self.enabled and self._remaining > 0

    def observe(
        self, tool_name: str, args: Mapping[str, Any] | None, result: str | None
    ) -> PostCheckpointGuardVerdict:
        if not self.enabled or self._remaining <= 0:
            return PostCheckpointGuardVerdict(
                should_abort=False,
                armed=False,
                remaining_attempts=0,
            )
        sig = ToolCallSignature.from_call(tool_name, args)
        rh = result_hash(result)
        obs = PostCheckpointGuardObservation(
            tool_name=tool_name,
            args_hash=sig.args_hash,
            result_hash=rh,
        )
        self._remaining -= 1
        self._history.append(obs)
        matches = sum(
            1
            for entry in self._history
            if entry.tool_name == obs.tool_name
            and entry.args_hash == obs.args_hash
            and entry.result_hash == obs.result_hash
        )
        armed_after = self._remaining > 0
        if matches >= self.abort_after:
            return PostCheckpointGuardVerdict(
                should_abort=True,
                armed=armed_after,
                remaining_attempts=self._remaining,
                detector="checkpoint_loop_persisted",
                count=matches,
                tool_name=tool_name,
                message=(
                    f"CRITICAL: tool {tool_name} repeated {matches} times with "
                    f"identical arguments and results within {self.window_size} "
                    "attempts after checkpoint compaction. Aborting to prevent "
                    "runaway resource use."
                ),
            )
        return PostCheckpointGuardVerdict(
            should_abort=False,
            armed=armed_after,
            remaining_attempts=self._remaining,
        )


def synthetic_block_result(decision: ToolGuardrailDecision) -> str:
    """Build synthetic role=tool content for a blocked tool call."""
    return json.dumps(
        {
            "error": decision.message,
            "guardrail": decision.to_metadata(),
        },
        ensure_ascii=False,
    )


def append_guidance(result: str, decision: ToolGuardrailDecision) -> str:
    """Append runtime guidance to the current tool result content."""
    if decision.action not in {"warn", "halt", "block"} or not decision.message:
        return result
    label = (
        "Tool loop hard stop"
        if decision.action in {"halt", "block"}
        else "Tool loop warning"
    )
    suffix = (
        f"\n\n[{label}: "
        f"{decision.code}; count={decision.count}; {decision.message}]"
    )
    return (result or "") + suffix


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            k = str(key)
            if k.lower() in _VOLATILE_ARG_KEYS:
                continue
            out[k] = _strip_volatile(item)
        return out
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value[:32]]
    return value


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()
