"""Runtime harness, security, validator, channel and operations contracts."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from hypothesis import given, strategies as st

from kageha.harness.sandbox import SessionWorkspace, run_shell
from kageha.loop.controller import RunResult
from kageha.loop.goal_card import GoalCard, GoalItem
from kageha.models.registry import ModelConfig, ModelRegistry, ProviderConfig
from kageha.runtime.channels import DurableChannelQueue, identity_hash
from kageha.runtime.engine import AgentRuntime, RunHandle
from kageha.runtime.journal import (
    ToolJournal,
    args_preview,
    artifact_refs_from_result,
    classify_side_effect,
    computer_frame_from_result,
)
from kageha.runtime.policies import (
    RecoveryAction,
    RecoveryPolicy,
    Scheduler,
    StopDecision,
    StopPolicy,
)
from kageha.runtime.providers import (
    ProviderControlPlane,
    ProviderRequirement,
    classify_provider_failure,
)
from kageha.runtime.reducer import reduce_event
from kageha.runtime.security import ExecutionSecurityPolicy
from kageha.runtime.store import RuntimeStore
from kageha.runtime.supervisor import ServiceSupervisor
from kageha.runtime.telemetry import Telemetry
from kageha.runtime.types import (
    FailureClass,
    ProviderHealth,
    RunEventKind,
    RunEvent,
    RunPhase,
    SecurityProfile,
    ToolReconciliation,
    TurnRequest,
)
from kageha.runtime.validators import (
    ValidationContext,
    ValidatorRegistry,
    compile_requirements,
)


@pytest.fixture
def store(tmp_path: Path) -> RuntimeStore:
    value = RuntimeStore(tmp_path / "runtime.db")
    yield value
    value.close()


def _turn(store: RuntimeStore, objective: str = "test"):
    return store.start_turn(TurnRequest(objective=objective))[0]


@given(st.lists(st.integers(min_value=0, max_value=1000), max_size=40))
def test_reducer_progress_is_monotonic_and_replay_idempotent(values: list[int]):
    accepted = RunEvent.create(
        session_id="s",
        turn_id="t",
        sequence=1,
        kind=RunEventKind.ACCEPTED,
        payload={"objective": "property", "max_steps": 1001},
    )
    snapshot = reduce_event(None, accepted)
    for sequence, value in enumerate(values, start=2):
        event = RunEvent.create(
            session_id="s",
            turn_id="t",
            sequence=sequence,
            kind=RunEventKind.PROGRESS,
            payload={"step": value, "usd_spent": value / 1000},
        )
        before_steps = snapshot.steps_used
        snapshot = reduce_event(snapshot, event)
        assert snapshot.steps_used >= before_steps
        assert reduce_event(snapshot, event) is snapshot


def test_checkpoint_created_for_every_transition(store: RuntimeStore):
    accepted = _turn(store)
    store.append_event(
        session_id=accepted.session_id,
        turn_id=accepted.turn_id,
        kind=RunEventKind.PLANNING_STARTED,
    )
    events = store.events(accepted.turn_id)
    checkpoints = store._conn.execute(  # noqa: SLF001
        "SELECT sequence FROM checkpoints WHERE turn_id=? ORDER BY sequence",
        (accepted.turn_id,),
    ).fetchall()
    assert [row[0] for row in checkpoints] == [event.sequence for event in events]


def test_crash_injection_occurs_after_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "runtime.db"
    store = RuntimeStore(path)
    monkeypatch.setenv("KAGEHA_CRASH_AFTER_EVENT", "accepted")
    with pytest.raises(RuntimeError, match="injected crash"):
        store.start_turn(TurnRequest(objective="durable"))
    store.close()
    monkeypatch.delenv("KAGEHA_CRASH_AFTER_EVENT")
    reopened = RuntimeStore(path)
    try:
        sessions = reopened.list_sessions()
        assert len(sessions) == 1
        assert reopened.latest_snapshot(sessions[0]["id"]).objective == "durable"
    finally:
        reopened.close()


def test_mutation_idempotency_ignores_provider_tool_call_id(store: RuntimeStore):
    accepted = _turn(store)
    first, created = store.begin_tool_attempt(
        session_id=accepted.session_id,
        turn_id=accepted.turn_id,
        tool_call_id="provider-a",
        tool_name="send_message",
        arguments={"to": "u", "text": "hello"},
        side_effect="external_mutation",
        risk_class="messaging",
    )
    second, created_again = store.begin_tool_attempt(
        session_id=accepted.session_id,
        turn_id=accepted.turn_id,
        tool_call_id="provider-b",
        tool_name="send_message",
        arguments={"to": "u", "text": "hello"},
        side_effect="external_mutation",
        risk_class="messaging",
    )
    assert created is True
    assert created_again is False
    assert second.id == first.id


def test_tool_journal_replays_completed_and_blocks_uncertain(store: RuntimeStore):
    accepted = _turn(store)
    journal = ToolJournal(
        store,
        session_id=accepted.session_id,
        turn_id=accepted.turn_id,
    )
    attempt_id, replay = journal.before(
        call_id="1",
        tool_name="write_file",
        arguments={"path": "x", "content": "y"},
        risk_class="safe",
    )
    assert replay is None
    journal.after(attempt_id, "wrote x")
    _, replay = journal.before(
        call_id="different",
        tool_name="write_file",
        arguments={"path": "x", "content": "y"},
        risk_class="safe",
    )
    assert replay == "wrote x"
    assert classify_side_effect("memory_recall", "safe") == "read"
    assert classify_side_effect("send_message", "messaging") == "external_mutation"


def test_tool_journal_emits_tool_card_and_computer_frame_fields(store: RuntimeStore):
    accepted = _turn(store)
    journal = ToolJournal(
        store,
        session_id=accepted.session_id,
        turn_id=accepted.turn_id,
    )
    assert args_preview({"path": "README.md", "query": "x" * 80}).startswith(
        "path=README.md"
    )
    assert artifact_refs_from_result(
        json.dumps({"path": "artifacts/computer/screen.png", "thumb": "artifacts/computer/thumbs/screen_thumb.jpg"})
    ) == [
        "artifacts/computer/screen.png",
        "artifacts/computer/thumbs/screen_thumb.jpg",
    ]

    attempt_id, replay = journal.before(
        call_id="shot-1",
        tool_name="computer_screenshot",
        arguments={"path": "artifacts/computer/screen.png"},
        risk_class="safe",
    )
    assert replay is None
    events = store.events(accepted.turn_id, after_sequence=0)
    started = next(ev for ev in events if ev.kind.value == "tool_started")
    assert started.payload["args_preview"].startswith("path=")
    assert started.payload["tool_card"]["status"] == "running"

    journal.after(
        attempt_id,
        json.dumps(
            {
                "path": "artifacts/computer/screen.png",
                "thumb": "artifacts/computer/thumbs/screen_thumb.jpg",
                "bytes": 12,
            }
        ),
    )
    events = store.events(accepted.turn_id, after_sequence=started.sequence)
    completed = next(ev for ev in events if ev.kind.value == "tool_completed")
    assert completed.payload["status"] == "ok"
    assert completed.payload["duration_ms"] is not None
    assert "artifacts/computer/screen.png" in completed.payload["artifact_refs"]
    assert completed.payload["tool_card"]["name"] == "computer_screenshot"
    frame = completed.payload["computer_frame"]
    assert frame["action"] == "computer_screenshot"
    assert frame["path"].endswith("screen_thumb.jpg")
    assert computer_frame_from_result("computer_click", '{"ok": true}') is None


def test_tool_journal_handles_inflight_failed_and_unsandboxed_attempts(
    store: RuntimeStore,
):
    accepted = _turn(store)
    journal = ToolJournal(
        store,
        session_id=accepted.session_id,
        turn_id=accepted.turn_id,
    )
    attempt_id, replay = journal.before(
        call_id="a",
        tool_name="send_message",
        arguments={"to": "u", "text": "hello"},
        risk_class="messaging",
        policy_grant=json.dumps({"sandboxed": False, "reason": "approved"}),
    )
    assert replay is None
    _, replay = journal.before(
        call_id="b",
        tool_name="send_message",
        arguments={"to": "u", "text": "hello"},
        risk_class="messaging",
    )
    assert replay and "already in progress" in replay
    store.reconcile_inflight(accepted.session_id)
    _, replay = journal.before(
        call_id="c",
        tool_name="send_message",
        arguments={"to": "u", "text": "hello"},
        risk_class="messaging",
    )
    assert replay and "uncertain outcome" in replay

    failed_id, _ = journal.before(
        call_id="d",
        tool_name="send_message",
        arguments={"to": "different", "text": "hello"},
        risk_class="messaging",
        policy_grant="{not-json",
    )
    journal.after(failed_id, "ERROR synthetic")
    _, replay = journal.before(
        call_id="e",
        tool_name="send_message",
        arguments={"to": "different", "text": "hello"},
        risk_class="messaging",
    )
    assert replay and "external mutation failed" in replay

    read_id, replay = journal.before(
        call_id="read-1",
        tool_name="read_file",
        arguments={"path": "a"},
        risk_class="safe",
    )
    assert replay is None
    journal.after(
        read_id,
        json.dumps(
            {
                "ok": True,
                "sandboxed": False,
                "security_profile": "approval_fallback",
            }
        ),
    )
    metadata = json.loads(
        store.inspect_session(accepted.session_id)["session"]["metadata_json"]
    )
    assert metadata["unsandboxed"] is True
    assert attempt_id


def test_secret_is_redacted_before_runtime_persistence(store: RuntimeStore):
    secret = "Aa1+/syntheticCredential987654321XYZ"
    accepted, snapshot = store.start_turn(
        TurnRequest(objective=f"use api_key={secret}")
    )
    raw = store.path.read_bytes()
    assert secret.encode() not in raw
    assert secret not in snapshot.objective
    assert secret not in json.dumps([event.to_dict() for event in store.events(accepted.turn_id)])


def test_public_reference_url_survives_runtime_persistence(store: RuntimeStore):
    url = "https://www.instagram.com/reels/DbX-hGFyjD0/"
    accepted, snapshot = store.start_turn(
        TurnRequest(objective=f"inspect reference {url}")
    )

    assert url in snapshot.objective
    assert url in json.dumps([event.to_dict() for event in store.events(accepted.turn_id)])


def test_channel_queue_deduplicates_and_recovers_stale_claim(store: RuntimeStore):
    queue = DurableChannelQueue("telegram", store)
    first = queue.register_inbound(identity="42", external_id="m1", text="hello")
    duplicate = queue.register_inbound(identity="42", external_id="m1", text="hello")
    assert first.accepted is True
    assert duplicate.accepted is False
    queued = queue.enqueue_outbound(
        identity="42",
        text="reply",
        idempotency_key="out-1",
    )
    claimed = queue.claim_outbound(identity="42")
    assert claimed and claimed["id"] == queued.message_id
    store._conn.execute(  # noqa: SLF001
        "UPDATE channel_messages SET claimed_at=0 WHERE id=?",
        (queued.message_id,),
    )
    recovered = store.claim_channel_message(
        channel="telegram",
        direction="outbound",
        stale_after_s=1,
    )
    assert recovered and recovered["id"] == queued.message_id
    delivered = queue.finish_outbound(queued.message_id, delivered=True)
    assert delivered["status"] == "delivered"
    assert identity_hash("telegram", "42") != "42"


def test_validator_registry_structured_code_image_and_counts(tmp_path: Path):
    (tmp_path / "good.json").write_text('{"ok": true}')
    (tmp_path / "bad.json").write_text("{")
    (tmp_path / "good.py").write_text("def add(a, b):\n    return a + b\n")
    Image.new("RGB", (128, 128), "white").save(tmp_path / "blank.png")
    context = ValidationContext(
        objective="Create four files",
        workspace=tmp_path,
        artifacts=("good.json", "bad.json", "good.py", "blank.png"),
    )
    result = ValidatorRegistry().validate(context)
    assert result.deterministic_passed is False
    assert any("invalid json" in defect["problem"] for defect in result.defects)
    assert any("blank" in defect["problem"] for defect in result.defects)


def test_powerpoint_count_requirement_is_deterministic(tmp_path: Path):
    deck = tmp_path / "deck.pptx"
    with zipfile.ZipFile(deck, "w") as archive:
        archive.writestr("ppt/presentation.xml", "<p:presentation/>")
        archive.writestr("ppt/slides/slide1.xml", "<p:sld/>")
        archive.writestr("ppt/slides/slide2.xml", "<p:sld/>")
    result = ValidatorRegistry().validate(
        ValidationContext(
            objective="Create 3 slides",
            workspace=tmp_path,
            artifacts=("deck.pptx",),
        )
    )
    package = next(check for check in result.checks if check["validator"] == "powerpoint")
    assert package["passed"] is False
    assert "expected exactly 3" in package["defect"]


def test_requirement_compiler_handles_citations_and_browser():
    requirements = compile_requirements(
        "Research sources, create 5 slides, and capture a browser screenshot"
    )
    assert requirements["slides"] == 5
    assert requirements["citations"] is True
    assert requirements["browser_outcome"] is True


def test_security_profiles_fail_closed_without_isolation(monkeypatch):
    unavailable = SimpleNamespace(
        profile="off",
        requested="off",
        available=True,
        detail="disabled",
    )
    monkeypatch.setattr(
        "kageha.runtime.security.sandbox_status",
        lambda: unavailable,
    )
    strict = ExecutionSecurityPolicy(SecurityProfile.STRICT)
    fallback = ExecutionSecurityPolicy(SecurityProfile.APPROVAL_FALLBACK)
    assert strict.assess(risk_class="browser").allowed is False
    decision = fallback.assess(risk_class="browser")
    assert decision.allowed is True
    assert decision.sandboxed is False
    assert strict.assess(risk_class="safe").allowed is True


@pytest.mark.asyncio
async def test_run_shell_strict_refuses_unisolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("KAGEHA_SANDBOX", "off")
    result = await run_shell(
        "pwd",
        tmp_path,
        security_profile="strict",
    )
    assert result.exit_code == 126
    assert "strict security profile" in result.stderr


def test_provider_failure_taxonomy_and_capabilities(store: RuntimeStore):
    registry = ModelRegistry(
        providers={
            "openai": ProviderConfig(
                "openai",
                "openai_compat",
                "https://example.invalid",
                "OPENAI_API_KEY",
            )
        },
        models={
            "m": ModelConfig(
                "m",
                "openai",
                "model",
                capabilities=["tool_calling", "structured_output"],
                context_window=1000,
            )
        },
        roles={"default": ["m"]},
    )
    control = ProviderControlPlane(store, registry)
    assert control.supports("m", ProviderRequirement(tool_calling=True, minimum_context=999))
    assert not control.supports("m", ProviderRequirement(vision=True))
    # Burst 429s are rate limits; hard billing/quota strings stay QUOTA.
    assert classify_provider_failure("429 Too Many Requests") == FailureClass.RATE_LIMIT
    assert (
        classify_provider_failure("429 insufficient_quota") == FailureClass.QUOTA
    )
    assert classify_provider_failure("request timed out") == FailureClass.TIMEOUT
    control.record_route_failure(
        model_id="m",
        provider="openai",
        error="401 unauthorized",
        failure_class="auth",
    )
    assert control.is_model_healthy("m") is False
    control.record_route_success(model_id="m", provider="openai", latency_ms=12)
    assert control.is_model_healthy("m") is True


@pytest.mark.asyncio
async def test_provider_check_reports_missing_keys(
    store: RuntimeStore, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    registry = ModelRegistry(
        providers={
            "openai": ProviderConfig(
                "openai", "openai_compat", "", "OPENAI_API_KEY"
            )
        },
        models={"m": ModelConfig("m", "openai", "model")},
        roles={"default": ["m"]},
    )
    health = await ProviderControlPlane(store, registry).check_all(
        required=("openai",)
    )
    assert health[0].state == "key_missing"
    assert health[0].failure_class == FailureClass.AUTH


@pytest.mark.asyncio
async def test_provider_deep_health_success_and_failure(
    store: RuntimeStore,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    registry = ModelRegistry(
        providers={
            "openai": ProviderConfig(
                "openai", "openai_compat", "", "OPENAI_API_KEY"
            )
        },
        models={
            "m": ModelConfig(
                "m",
                "openai",
                "model",
                capabilities=["tool_calling"],
            )
        },
        roles={"default": ["m"]},
    )

    class HealthyModel:
        async def smoke(self):
            return "ok"

    monkeypatch.setattr(registry, "build", lambda model_id: HealthyModel())
    control = ProviderControlPlane(store, registry)
    healthy = await control.check_all(deep=True, required=("openai",))
    assert healthy[0].available is True
    assert healthy[0].state == "closed"

    class BrokenModel:
        async def smoke(self):
            raise TimeoutError("timed out")

    monkeypatch.setattr(registry, "build", lambda model_id: BrokenModel())
    broken = await control.check_all(deep=True, required=("openai",))
    assert broken[0].failure_class == FailureClass.TIMEOUT
    assert broken[0].state == "open"

    missing_model = await control._check_provider("missing", deep=False)  # noqa: SLF001
    assert missing_model.state == "missing"
    no_provider = ModelRegistry(
        providers={},
        models={"m": ModelConfig("m", "openai", "model")},
        roles={},
    )
    missing_provider = await ProviderControlPlane(
        store,
        no_provider,
    )._check_provider("openai", deep=False)  # noqa: SLF001
    assert missing_provider.error == "provider configuration is missing"


def test_scheduler_recovery_and_stop_policies(store: RuntimeStore):
    accepted, snapshot = store.start_turn(TurnRequest(objective="policy", max_steps=2))
    snapshot.plan = [{"id": "a"}, {"id": "b"}]
    snapshot.goals = [{"id": "a", "passes": True}]
    assert Scheduler().next(snapshot).task_id == "b"
    recovery = RecoveryPolicy()
    assert recovery.decide(FailureClass.QUOTA) == RecoveryAction.SWITCH_PROVIDER
    assert (
        recovery.decide(
            FailureClass.TIMEOUT,
            side_effect="external_mutation",
        )
        == RecoveryAction.RECONCILE
    )
    snapshot.usd_spent = snapshot.max_usd
    assert StopPolicy().decide(snapshot) == StopDecision.FAIL_BUDGET
    snapshot.usd_spent = 0
    snapshot.phase = RunPhase.EXECUTING
    snapshot.steps_used = snapshot.max_steps
    assert StopPolicy().decide(snapshot) == StopDecision.FAIL_STEPS
    assert accepted.turn_id == snapshot.turn_id


def test_all_recovery_stop_and_scheduler_policy_branches(store: RuntimeStore):
    _, snapshot = store.start_turn(TurnRequest(objective="branches"))
    snapshot.plan = []
    assert Scheduler().next(snapshot) is None
    snapshot.plan = [{"task": "next", "stage": "build"}]
    scheduled = Scheduler().next(snapshot)
    assert scheduled and scheduled.task_id == "p1" and scheduled.stage == "build"

    recovery = RecoveryPolicy()
    assert recovery.decide(FailureClass.TRANSIENT, attempts=0) == RecoveryAction.RETRY
    assert recovery.decide(FailureClass.RATE_LIMIT, attempts=0) == RecoveryAction.RETRY
    assert (
        recovery.decide(FailureClass.TIMEOUT, attempts=3)
        == RecoveryAction.SWITCH_TOOL
    )
    assert recovery.decide(FailureClass.INVALID_OUTPUT) == RecoveryAction.REPAIR
    assert recovery.decide(FailureClass.POLICY_DENIAL) == RecoveryAction.BLOCK
    assert (
        recovery.decide(
            FailureClass.UNKNOWN,
            reconciliation=ToolReconciliation.UNCERTAIN,
        )
        == RecoveryAction.RECONCILE
    )

    stop = StopPolicy()
    snapshot.phase = RunPhase.BLOCKED
    assert stop.decide(snapshot) == StopDecision.BLOCK
    snapshot.phase = RunPhase.VERIFYING
    snapshot.validated = True
    snapshot.open_tool_attempts = []
    assert stop.decide(snapshot) == StopDecision.COMPLETE
    snapshot.validated = False
    assert stop.decide(snapshot) == StopDecision.CONTINUE


def test_telemetry_is_local_and_correlated(store: RuntimeStore):
    telemetry = Telemetry(store)
    telemetry.metric("turn.success", 1, session_id="s", turn_id="t")
    with telemetry.span("unit", session_id="s", turn_id="t"):
        pass
    summary = {row["name"]: row for row in store.metric_summary()}
    assert summary["turn.success"]["total"] == 1
    assert summary["unit.duration"]["points"] == 1


def test_pending_approval_is_durable(store: RuntimeStore):
    accepted = _turn(store)
    approval_id = store.record_approval(
        session_id=accepted.session_id,
        turn_id=accepted.turn_id,
        action="tool:browser",
        decision="pending",
        security_profile="strict",
        sandboxed=True,
        detail={"channel_key": "private"},
    )
    pending = store.pending_approvals(accepted.session_id)
    assert pending[0]["id"] == approval_id
    store.record_approval(
        session_id=accepted.session_id,
        turn_id=accepted.turn_id,
        action="tool:browser",
        decision="approved",
        security_profile="strict",
        sandboxed=True,
        detail={"channel_key": "private"},
        approval_id=approval_id,
    )
    assert store.pending_approvals(accepted.session_id) == []


def test_supervisor_installs_platform_service_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    store = RuntimeStore(tmp_path / "runtime.db")
    supervisor = ServiceSupervisor(
        store=store,
        service_root=tmp_path / "services",
    )
    try:
        paths = supervisor.install()
        assert paths
        assert all(path.is_file() for path in paths)
        assert isinstance(supervisor.status(), dict)
        assert "No logs" in supervisor.logs("app-server")
    finally:
        supervisor.close()
        store.close()



def test_sqlite_reports_wal_and_integrity(store: RuntimeStore):
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"  # noqa: SLF001
    assert store._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"  # noqa: SLF001
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(  # noqa: SLF001
            """
            INSERT INTO channel_messages
                (id, channel, identity_key, direction, external_id, dedup_key,
                 session_id, turn_id, payload_json, status, attempts, available_at,
                 claimed_at, delivered_at, created_at, updated_at)
            VALUES ('1', 'c', 'i', 'inbound', '', 'd', '', '', '{}', 'pending',
                    0, 0, NULL, NULL, 0, 0),
                   ('2', 'c', 'i', 'inbound', '', 'd', '', '', '{}', 'pending',
                    0, 0, NULL, NULL, 0, 0)
            """
        )


def test_runtime_schema_rebuilds_divergent_versions(tmp_path: Path):
    path = tmp_path / "divergent.db"
    original = RuntimeStore(path)
    original.close()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=3")
    conn.close()
    rebuilt = RuntimeStore(path)
    try:
        assert rebuilt.status()["schema_version"] == 2
        cols = {
            row[1]
            for row in rebuilt._conn.execute("PRAGMA table_info(sessions)")  # noqa: SLF001
        }
        assert "runtime" not in cols
        assert "status" in cols
    finally:
        rebuilt.close()


def test_future_runtime_schema_refuses_absurd_versions(tmp_path: Path):
    path = tmp_path / "future.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=999")
    conn.close()
    with pytest.raises(RuntimeError, match="newer than supported"):
        RuntimeStore(path)


def test_store_operational_inspection_and_artifact_manifest(
    store: RuntimeStore,
    tmp_path: Path,
):
    accepted = _turn(store, "artifact")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("hello")
    store.add_artifacts(
        session_id=accepted.session_id,
        turn_id=accepted.turn_id,
        workspace=workspace,
        paths=["a.txt", "../escape.txt", "missing.txt"],
    )
    store.record_process(
        name="app-server",
        pid=123,
        state="running",
        executable="/python",
        config_hash="abc",
        detail={"ready": True},
    )
    benchmark_id = store.record_benchmark(
        suite="unit",
        configuration={"a": 1},
        environment={"b": 2},
        metrics={"success": 1},
        status="pass",
    )
    assert benchmark_id
    assert store.process_rows()[0]["detail"]["ready"] is True
    inspected = store.inspect_session(accepted.session_id)
    assert inspected["turns"][0]["id"] == accepted.turn_id
    assert store.latest_incomplete(accepted.session_id) is not None
    with pytest.raises(KeyError):
        store.inspect_session("missing")


def test_channel_delivery_retry_and_argument_validation(store: RuntimeStore):
    with pytest.raises(ValueError):
        store.enqueue_channel_message(
            channel="x",
            identity_key="i",
            direction="sideways",
            dedup_key="d",
            payload={},
        )
    row, _ = store.enqueue_channel_message(
        channel="x",
        identity_key="i",
        direction="outbound",
        dedup_key="d",
        payload={"text": "hello"},
    )
    claimed = store.claim_channel_message(channel="x", direction="outbound")
    assert claimed is not None
    pending = store.finish_channel_message(
        row["id"],
        delivered=False,
        retry_after_s=0,
    )
    assert pending["status"] == "pending"
    done = store.finish_channel_message(
        row["id"],
        delivered=True,
        external_id="remote-1",
    )
    assert done["external_id"] == "remote-1"
    assert (
        store.claim_channel_message(channel="x", direction="outbound")
        is None
    )
    with pytest.raises(KeyError):
        store.finish_channel_message("missing", delivered=True)


def test_provider_health_report_and_session_safety_mark(store: RuntimeStore):
    accepted = _turn(store)
    store.record_provider_health(
        ProviderHealth(
            provider="gemini",
            model_id="g",
            available=True,
            state="closed",
            capabilities=["tool_calling"],
        )
    )
    assert store.provider_health()[0]["capabilities"] == ["tool_calling"]
    store.mark_session_unsandboxed(
        accepted.session_id,
        tool_name="browser_open",
        reason="fallback",
    )
    metadata = json.loads(
        store.inspect_session(accepted.session_id)["session"]["metadata_json"]
    )
    assert metadata["unsandboxed"] is True


def test_more_reducer_transitions_and_invalid_sequences(store: RuntimeStore):
    accepted, snapshot = store.start_turn(TurnRequest(objective="states"))
    events = [
        (RunEventKind.PLANNING_STARTED, {}),
        (
            RunEventKind.PLANNED,
            {"plan": [{"id": "a"}], "goals": [{"id": "a"}]},
        ),
        (RunEventKind.TOOL_STARTED, {"attempt_id": "x", "step": 1}),
        (RunEventKind.TOOL_COMPLETED, {"attempt_id": "x", "usd_spent": 0.2}),
        (RunEventKind.REPAIR, {"action": "fix"}),
        (RunEventKind.CHECKPOINT, {}),
        (RunEventKind.VERIFICATION_STARTED, {}),
        (
            RunEventKind.VERIFICATION,
            {"status": "pass", "validated": True},
        ),
        (RunEventKind.COMPLETED, {"validated": True}),
    ]
    for kind, payload in events:
        _, snapshot = store.append_event(
            session_id=accepted.session_id,
            turn_id=accepted.turn_id,
            kind=kind,
            payload=payload,
        )
    assert snapshot.phase == RunPhase.COMPLETED
    assert store.latest_incomplete(accepted.session_id) is None
    with pytest.raises(Exception, match="terminal"):
        store.append_event(
            session_id=accepted.session_id,
            turn_id=accepted.turn_id,
            kind=RunEventKind.PROGRESS,
        )


def test_validator_pdf_video_browser_and_missing_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from pypdf import PdfWriter

    pdf = PdfWriter()
    pdf.add_blank_page(width=200, height=200)
    with (tmp_path / "one.pdf").open("wb") as output:
        pdf.write(output)
    monkeypatch.setattr(
        "kageha.runtime.validators.shutil.which",
        lambda name: None,
    )
    result = ValidatorRegistry().validate(
        ValidationContext(
            objective="Create a one page PDF and capture a browser screenshot",
            workspace=tmp_path,
            artifacts=("one.pdf", "missing.png"),
        )
    )
    assert result.status == "fail"
    assert any(
        defect["validator"] == "browser_outcome"
        for defect in result.defects
    )
    assert any("does not exist" in defect["problem"] for defect in result.defects)


def test_video_validator_uses_ffprobe_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    monkeypatch.setattr(
        "kageha.runtime.validators.shutil.which",
        lambda name: "/usr/bin/ffprobe" if name == "ffprobe" else None,
    )
    monkeypatch.setattr(
        "kageha.runtime.validators.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "format": {"duration": "3.5"},
                    "streams": [
                        {"codec_type": "video", "codec_name": "h264"},
                        {"codec_type": "audio", "codec_name": "aac"},
                    ],
                }
            ),
            stderr="",
        ),
    )
    result = ValidatorRegistry().validate(
        ValidationContext(
            objective="Create a video",
            workspace=tmp_path,
            artifacts=("video.mp4",),
        )
    )
    assert result.deterministic_passed is True


def test_citation_validator_checks_reachability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    (tmp_path / "report.md").write_text(
        "Claim with source: https://example.com/source"
    )

    class Response:
        status_code = 200

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def head(self, _url):
            return Response()

        def get(self, _url):
            return Response()

    monkeypatch.setattr("httpx.Client", Client)
    result = ValidatorRegistry().validate(
        ValidationContext(
            objective="Research and create a cited report",
            workspace=tmp_path,
            artifacts=("report.md",),
        )
    )
    assert result.deterministic_passed is True
    assert any(check["validator"] == "citations" for check in result.checks)



class _FakeController:
    """Small controller adapter that exercises the runtime event boundary."""

    def __init__(self, **kwargs):
        self.event_sink = kwargs["event_sink"]
        self.cancelled = False
        self.messages: list[str] = []

    def cancel(self) -> None:
        self.cancelled = True

    def inject(self, message: str) -> None:
        self.messages.append(message)

    async def run(
        self,
        task: str,
        *,
        run_id: str,
        workspace,
        fresh_turn: bool,
        turn_task: str | None,
        loop_mode: str = "full",
        agent_mode: str = "normal",
    ) -> RunResult:
        del loop_mode, agent_mode
        del fresh_turn, turn_task
        self.event_sink("run_start", {"task": task})
        (workspace.root / "plan.json").write_text(
            json.dumps({"steps": [{"id": "p1", "task": task}]})
        )
        (workspace.root / "goal_card.json").write_text(
            json.dumps({"items": [{"id": "p1", "description": task}]})
        )
        self.event_sink("plan", {"source": "fake"})
        self.event_sink("model", {"step": 1, "usd_spent": 0.01})
        self.event_sink("checkpoint", {"step": 1})
        await asyncio.sleep(0)
        status = "cancelled" if self.cancelled else "success"
        return RunResult(
            run_id=run_id,
            status=status,
            message="done",
            goal=GoalCard(
                task=task,
                items=[GoalItem("p1", task, passes=status == "success")],
            ),
            steps=1,
            spent_usd=0.01,
            validated=status == "success",
        )


class _FailingController(_FakeController):
    async def run(self, *args, **kwargs) -> RunResult:
        del args, kwargs
        raise RuntimeError("controller exploded")


@pytest.mark.asyncio
async def test_agent_runtime_submit_resume_events_and_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    store = RuntimeStore(tmp_path / "runtime.db")
    runtime = AgentRuntime(store=store, controller_factory=_FakeController)
    try:
        with pytest.raises(RuntimeError, match="has not started"):
            await RunHandle(runtime=runtime, session_id="s", turn_id="t").result()
        first = runtime.submit(TurnRequest(objective="Say hello"))
        with pytest.raises(RuntimeError, match="active"):
            runtime.close()
        result = await first.result()
        assert result.validated is True
        events = [event async for event in first.events()]
        assert events[0].kind == RunEventKind.ACCEPTED
        assert events[-1].kind == RunEventKind.COMPLETED
        assert runtime.active_handle(first.turn_id) is None

        second = runtime.resume(first.session_id, "Say hello again")
        assert (await second.result()).status == "success"
        assert second.turn_id != first.turn_id
        assert len(store.inspect_session(first.session_id)["turns"]) == 2
    finally:
        runtime.close()
        store.close()
    with pytest.raises(RuntimeError, match="closed"):
        runtime.submit(TurnRequest(objective="too late"))


@pytest.mark.asyncio
async def test_agent_runtime_records_controller_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    store = RuntimeStore(tmp_path / "runtime.db")
    runtime = AgentRuntime(store=store, controller_factory=_FailingController)
    try:
        handle = runtime.submit(TurnRequest(objective="Fail safely"))
        with pytest.raises(RuntimeError, match="exploded"):
            await handle.result()
        snapshot = store.get_snapshot(handle.turn_id)
        assert snapshot.phase == RunPhase.FAILED
        assert snapshot.last_error == "controller exploded"
    finally:
        runtime.close()
        store.close()


@pytest.mark.asyncio
async def test_agent_runtime_blocks_uncertain_external_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    store = RuntimeStore(tmp_path / "runtime.db")
    accepted = _turn(store, "send")
    SessionWorkspace.create(accepted.session_id)
    attempt, created = store.begin_tool_attempt(
        session_id=accepted.session_id,
        turn_id=accepted.turn_id,
        tool_call_id="call",
        tool_name="send_message",
        arguments={"to": "somebody"},
        side_effect="external_mutation",
        risk_class="messaging",
    )
    assert created and attempt.state == ToolReconciliation.IN_PROGRESS
    runtime = AgentRuntime(store=store, controller_factory=_FakeController)
    try:
        handle = runtime.resume(accepted.session_id, "continue")
        with pytest.raises(RuntimeError, match="Reconcile"):
            await handle.result()
        assert store.get_snapshot(accepted.turn_id).phase == RunPhase.BLOCKED
    finally:
        runtime.close()
        store.close()



def test_supervisor_linux_install_and_direct_process_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "home"))
    store = RuntimeStore(tmp_path / "runtime.db")
    supervisor = ServiceSupervisor(
        store=store,
        service_root=tmp_path / "services",
    )
    supervisor.platform = "Linux"
    try:
        installed = supervisor.install()
        assert installed
        assert "Restart=on-failure" in installed[0].read_text()

        monkeypatch.setattr(
            "kageha.runtime.supervisor.subprocess.Popen",
            lambda *args, **kwargs: SimpleNamespace(pid=4321),
        )
        monkeypatch.setattr(
            "kageha.runtime.supervisor._pid_alive",
            lambda pid: False,
        )
        started = supervisor.start("app-server")
        assert started == [
            {"name": "app-server", "pid": 4321, "already_running": False}
        ]
        assert supervisor.status()["services"][0]["pid"] == 4321

        monkeypatch.setattr(
            "kageha.runtime.supervisor._pid_alive",
            lambda pid: True,
        )
        assert supervisor.start("app-server")[0]["already_running"] is True

        (supervisor.logs_dir / "app-server.log").write_text("one\ntwo\n")
        (supervisor.logs_dir / "app-server.err.log").write_text("warning\n")
        output = supervisor.logs("app-server", lines=1)
        assert "two" in output and "warning" in output
        assert "one" not in output

        alive_calls = iter([True, False, False])
        monkeypatch.setattr(
            "kageha.runtime.supervisor._pid_alive",
            lambda pid: next(alive_calls, False),
        )
        killed: list[tuple[int, int]] = []
        monkeypatch.setattr(
            "kageha.runtime.supervisor.os.kill",
            lambda pid, sig: killed.append((pid, sig)),
        )
        assert supervisor.stop("app-server")[0]["stopped"] is True
        assert killed
        with pytest.raises(KeyError, match="unknown"):
            supervisor.start("missing")

        supervisor.platform = "unsupported"
        with pytest.raises(RuntimeError, match="macOS and Linux"):
            supervisor.install()
    finally:
        supervisor.close()
        store.close()
