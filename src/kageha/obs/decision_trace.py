"""Decision trace — structured WHY logging for agent decisions.

Records the reasoning behind every significant decision point:
- Model selection (why this model for this role)
- Adaptive control (why repair vs replan vs continue)
- Verifier verdicts (why pass vs fail, what evidence)
- Tool dispatch (why parallel vs serial, why blocked)
- Stop decisions (which rule triggered and why)

Each trace entry has a category, decision, reasoning, and context.
Traces are stored in the RuntimeStore for replay and debugging.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class TraceCategory(str, Enum):
    """Categories of traced decisions."""

    MODEL_SELECTION = "model_selection"
    ADAPTIVE_CONTROL = "adaptive_control"
    VERIFIER = "verifier"
    TOOL_DISPATCH = "tool_dispatch"
    STOP_RULE = "stop_rule"
    HOOK = "hook"
    PERMISSION = "permission"
    CONTEXT = "context"
    PLAN = "plan"
    SPEC = "spec"


@dataclass
class DecisionEntry:
    """A single traced decision."""

    category: TraceCategory
    decision: str  # What was decided
    reasoning: str  # Why it was decided
    alternatives: list[str] = field(default_factory=list)  # What else was considered
    context: dict[str, Any] = field(default_factory=dict)  # Supporting data
    timestamp: float = field(default_factory=time.time)
    step: int = 0
    task_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "decision": self.decision,
            "reasoning": self.reasoning,
            "alternatives": self.alternatives,
            "context": self.context,
            "timestamp": self.timestamp,
            "step": self.step,
            "task_id": self.task_id,
        }

    def to_compact(self) -> str:
        """One-line summary for log display."""
        return (
            f"[{self.category.value}] {self.decision} — {self.reasoning}"
        )


class DecisionTracer:
    """Collects and stores decision traces for a session.

    Usage:
        tracer = DecisionTracer()
        tracer.trace_model_selection(
            chosen="claude-sonnet-5",
            role="coding",
            reasoning="Primary model for coding role, all providers healthy",
            alternatives=["gpt-5.6-mini", "gemini-flash"],
        )
    """

    def __init__(self, *, max_entries: int = 2000) -> None:
        self._entries: list[DecisionEntry] = []
        self._max_entries = max_entries
        self._step = 0
        self._task_id = ""
        self._sink: Any = None  # Optional EventLog/RuntimeStore sink

    def set_step(self, step: int) -> None:
        self._step = step

    def set_task_id(self, task_id: str) -> None:
        self._task_id = task_id

    def set_sink(self, sink: Any) -> None:
        """Set an optional sink (EventLog, file, etc.) for real-time trace output."""
        self._sink = sink

    @property
    def entries(self) -> list[DecisionEntry]:
        return list(self._entries)

    def _record(self, entry: DecisionEntry) -> None:
        entry.step = self._step
        entry.task_id = self._task_id
        self._entries.append(entry)
        if len(self._entries) > self._max_entries:
            # Keep most recent entries
            self._entries = self._entries[-self._max_entries:]
        if self._sink is not None:
            try:
                self._sink.append("decision_trace", entry.to_dict())
            except Exception:  # noqa: BLE001
                pass

    def trace_model_selection(
        self,
        *,
        chosen: str,
        role: str,
        reasoning: str,
        alternatives: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Record why a specific model was selected for a role."""
        self._record(DecisionEntry(
            category=TraceCategory.MODEL_SELECTION,
            decision=f"Selected '{chosen}' for role '{role}'",
            reasoning=reasoning,
            alternatives=alternatives or [],
            context=context or {"role": role, "model": chosen},
        ))

    def trace_adaptive_control(
        self,
        *,
        decision: str,
        reasoning: str,
        state_summary: str = "",
        alternatives: list[str] | None = None,
    ) -> None:
        """Record adaptive control decision (REPAIR/REPLAN/CONTINUE/etc.)."""
        self._record(DecisionEntry(
            category=TraceCategory.ADAPTIVE_CONTROL,
            decision=decision,
            reasoning=reasoning,
            alternatives=alternatives or [],
            context={"state_summary": state_summary} if state_summary else {},
        ))

    def trace_verifier(
        self,
        *,
        verdict: str,
        reasoning: str,
        evidence: str = "",
        defects: list[str] | None = None,
    ) -> None:
        """Record verifier pass/fail decision with evidence."""
        self._record(DecisionEntry(
            category=TraceCategory.VERIFIER,
            decision=f"Verdict: {verdict}",
            reasoning=reasoning,
            context={
                "evidence": evidence,
                "defects": defects or [],
            },
        ))

    def trace_tool_dispatch(
        self,
        *,
        tool_name: str,
        decision: str,
        reasoning: str,
        risk_class: str = "",
    ) -> None:
        """Record tool dispatch decision (parallel/serial/blocked)."""
        self._record(DecisionEntry(
            category=TraceCategory.TOOL_DISPATCH,
            decision=f"{tool_name}: {decision}",
            reasoning=reasoning,
            context={"tool": tool_name, "risk_class": risk_class},
        ))

    def trace_stop_rule(
        self,
        *,
        rule: str,
        triggered: bool,
        reasoning: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Record stop rule evaluation."""
        self._record(DecisionEntry(
            category=TraceCategory.STOP_RULE,
            decision=f"{'TRIGGERED' if triggered else 'passed'}: {rule}",
            reasoning=reasoning,
            context=context or {},
        ))

    def trace_hook(
        self,
        *,
        event: str,
        hook_name: str,
        decision: str,
        reasoning: str,
    ) -> None:
        """Record hook execution result."""
        self._record(DecisionEntry(
            category=TraceCategory.HOOK,
            decision=f"{event}/{hook_name}: {decision}",
            reasoning=reasoning,
            context={"event": event, "hook": hook_name},
        ))

    def trace_permission(
        self,
        *,
        tool_name: str,
        decision: str,
        reasoning: str,
    ) -> None:
        """Record permission/approval decision."""
        self._record(DecisionEntry(
            category=TraceCategory.PERMISSION,
            decision=f"{tool_name}: {decision}",
            reasoning=reasoning,
            context={"tool": tool_name},
        ))

    def trace_context(
        self,
        *,
        decision: str,
        reasoning: str,
        budget_used: dict[str, int] | None = None,
    ) -> None:
        """Record context engineering decision (truncation, compression)."""
        self._record(DecisionEntry(
            category=TraceCategory.CONTEXT,
            decision=decision,
            reasoning=reasoning,
            context={"budget": budget_used} if budget_used else {},
        ))

    def trace_plan(
        self,
        *,
        decision: str,
        reasoning: str,
    ) -> None:
        """Record planning decision."""
        self._record(DecisionEntry(
            category=TraceCategory.PLAN,
            decision=decision,
            reasoning=reasoning,
        ))

    def trace_spec(
        self,
        *,
        decision: str,
        reasoning: str,
        spec_name: str = "",
        stage: str = "",
    ) -> None:
        """Record spec pipeline decision."""
        self._record(DecisionEntry(
            category=TraceCategory.SPEC,
            decision=decision,
            reasoning=reasoning,
            context={"spec": spec_name, "stage": stage},
        ))

    # ── Query & Export ──────────────────────────────────────────────

    def recent(self, n: int = 20) -> list[DecisionEntry]:
        """Get the N most recent entries."""
        return self._entries[-n:]

    def by_category(self, category: TraceCategory) -> list[DecisionEntry]:
        """Filter entries by category."""
        return [e for e in self._entries if e.category == category]

    def by_step(self, step: int) -> list[DecisionEntry]:
        """Get all decisions made at a specific step."""
        return [e for e in self._entries if e.step == step]

    def summary(self, last_n: int = 10) -> str:
        """Human-readable summary of recent decisions."""
        lines = [f"Decision Trace (last {last_n}):"]
        for entry in self._entries[-last_n:]:
            lines.append(f"  step={entry.step} {entry.to_compact()}")
        return "\n".join(lines)

    def export_jsonl(self, path: Path) -> int:
        """Export all entries to JSONL file."""
        with path.open("w", encoding="utf-8") as f:
            for entry in self._entries:
                f.write(json.dumps(entry.to_dict(), default=str) + "\n")
        return len(self._entries)

    def clear(self) -> None:
        """Clear all entries."""
        self._entries.clear()
