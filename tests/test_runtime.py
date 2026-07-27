"""Durability and state-machine contracts for the durable runtime."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from kageha.loop.controller import RunResult
from kageha.loop.goal_card import GoalCard, GoalItem
from kageha.runtime.engine import AgentRuntime
from kageha.runtime.providers import ProviderControlPlane
from kageha.runtime.reducer import InvalidTransition, reduce_event
from kageha.runtime.store import RuntimeStore
from kageha.runtime.types import (
    FailureClass,
    ProviderHealth,
    RunEvent,
    RunEventKind,
    RunPhase,
    ToolReconciliation,
    TurnRequest,
)


@pytest.fixture
def store(tmp_path: Path) -> RuntimeStore:
    value = RuntimeStore(tmp_path / "runtime.db")
    yield value
    value.close()


def test_runtime_store_uses_wal_and_all_canonical_tables(store: RuntimeStore):
    status = store.status()
    assert status["schema_version"] == 1
    assert status["wal"] is True
    assert status["counts"] == {
        "sessions": 0,
        "turns": 0,
        "events": 0,
        "tool_attempts": 0,
        "provider_health": 0,
        "processes": 0,
        "benchmark_runs": 0,
        "channel_messages": 0,
        "metric_points": 0,
        "trace_spans": 0,
    }


def test_event_reducer_requires_validated_completion():
    accepted = RunEvent.create(
        session_id="s",
        turn_id="t",
        sequence=1,
        kind=RunEventKind.ACCEPTED,
        payload={"objective": "build"},
    )
    snapshot = reduce_event(None, accepted)
    bad = RunEvent.create(
        session_id="s",
        turn_id="t",
        sequence=2,
        kind=RunEventKind.COMPLETED,
        payload={"validated": False},
    )
    with pytest.raises(InvalidTransition, match="validated"):
        reduce_event(snapshot, bad)


def test_events_are_idempotent_and_rebuildable(store: RuntimeStore):
    request = TurnRequest(objective="write hello", idempotency_key="request-1")
    accepted, snapshot = store.start_turn(request)
    duplicate, duplicate_snapshot = store.start_turn(request)
    assert duplicate.id == accepted.id
    assert duplicate_snapshot.last_sequence == snapshot.last_sequence

    _, planned = store.append_event(
        session_id=accepted.session_id,
        turn_id=accepted.turn_id,
        kind=RunEventKind.PLANNED,
        payload={
            "plan": [{"id": "p1", "description": "write"}],
            "goals": [{"id": "g1", "description": "hello exists"}],
        },
        idempotency_key="plan-1",
    )
    assert planned.phase == RunPhase.EXECUTING
    assert planned.plan_version == 1

    # Corrupt only the projection; event replay must repair it.
    store._conn.execute(  # noqa: SLF001 - deliberate corruption injection
        "UPDATE turns SET snapshot_json=? WHERE id=?",
        (json.dumps({**planned.to_dict(), "phase": "failed"}), accepted.turn_id),
    )
    rebuilt = store.rebuild(accepted.session_id)
    assert rebuilt[accepted.turn_id].phase == RunPhase.EXECUTING
    assert store.get_snapshot(accepted.turn_id).plan_version == 1


def test_inflight_reconciliation_distinguishes_side_effects(store: RuntimeStore):
    accepted, _ = store.start_turn(TurnRequest(objective="tools"))
    read, _ = store.begin_tool_attempt(
        session_id=accepted.session_id,
        turn_id=accepted.turn_id,
        tool_call_id="read-1",
        tool_name="read_file",
        arguments={"path": "x"},
        side_effect="read",
        risk_class="safe",
    )
    write, _ = store.begin_tool_attempt(
        session_id=accepted.session_id,
        turn_id=accepted.turn_id,
        tool_call_id="write-1",
        tool_name="write_file",
        arguments={"path": "x", "content": "y"},
        side_effect="mutation",
        risk_class="safe",
    )
    reconciled = {item.id: item for item in store.reconcile_inflight(accepted.session_id)}
    assert reconciled[read.id].state == ToolReconciliation.RETRYABLE
    assert reconciled[write.id].state == ToolReconciliation.UNCERTAIN


@pytest.mark.asyncio
async def test_agent_runtime_journals_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    store = RuntimeStore(tmp_path / "runtime.db")

    class FakeController:
        def __init__(self, **kwargs):
            self.event_sink = kwargs["event_sink"]

        def cancel(self):
            return None

        def inject(self, message: str):
            return None

        async def run(self, task: str, **kwargs):
            workspace = kwargs["workspace"]
            self.event_sink("run_start", {"task": task})
            workspace.write_text(
                "plan.json",
                json.dumps(
                    {
                        "steps": [
                            {"id": "p1", "description": "write", "tools": []}
                        ]
                    }
                ),
            )
            workspace.write_text(
                "goal_card.json",
                json.dumps(
                    {
                        "task": task,
                        "items": [
                            {
                                "id": "g1",
                                "description": "done",
                                "passes": True,
                                "evidence": "fake",
                            }
                        ],
                    }
                ),
            )
            workspace.write_text("artifact.txt", "done")
            self.event_sink("plan", {"source": "test", "steps": 1})
            self.event_sink(
                "control",
                {"validation": "pass", "decision": "stop_success"},
            )
            return RunResult(
                run_id=workspace.run_id,
                status="success",
                message="done",
                goal=GoalCard(
                    task=task,
                    items=[GoalItem("g1", "done", passes=True, evidence="fake")],
                ),
                steps=1,
                spent_usd=0.0,
                artifacts=["artifact.txt"],
                validated=True,
            )

    runtime = AgentRuntime(store=store, controller_factory=FakeController)
    result = await runtime.execute(TurnRequest(objective="make it"))
    assert result.status == "success"
    sessions = store.list_sessions()
    assert len(sessions) == 1
    snapshot = store.latest_snapshot(sessions[0]["id"])
    assert snapshot.phase == RunPhase.COMPLETED
    assert snapshot.validated is True
    assert "artifact.txt" in snapshot.artifacts
    store.close()


def test_provider_circuit_half_opens_after_cooldown(store: RuntimeStore):
    control = ProviderControlPlane(store, auto_heal=False)
    control.record_route_failure(
        model_id="gemini-flash",
        provider="gemini",
        error="empty model response",
        failure_class=FailureClass.TRANSIENT.value,
    )
    assert control.is_model_healthy("gemini-flash") is False
    # Expire the durable circuit without waiting real wall clock.
    row = next(r for r in store.provider_health() if r["model_id"] == "gemini-flash")
    store.record_provider_health(
        ProviderHealth(
            provider="gemini",
            model_id="gemini-flash",
            available=False,
            state="open",
            failure_class=FailureClass.TRANSIENT,
            error=row["error"],
            circuit_open_until=time.time() - 1.0,
        )
    )
    assert control.is_model_healthy("gemini-flash") is True
    assert control.heal_circuits() == 1
    healed = next(r for r in store.provider_health() if r["model_id"] == "gemini-flash")
    assert healed["available"] is True
    assert healed["state"] == "unknown"


def test_circuit_allows_matches_v1_breaker():
    from kageha.harness.circuit import CircuitBreaker, circuit_allows

    now = time.time()
    assert circuit_allows(open_until=now - 1) is True
    assert circuit_allows(open_until=now + 30) is False
    breaker = CircuitBreaker(reset_after_s=10.0)
    breaker.opened_at["m"] = now - 11
    assert breaker.allow("m") is True
    breaker.opened_at["m"] = now
    assert breaker.allow("m") is False

