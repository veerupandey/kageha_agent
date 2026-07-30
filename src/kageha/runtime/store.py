"""SQLite WAL authority for durable runtime state."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from kageha.config import kageha_home
from kageha.runtime.reducer import reduce_event
from kageha.runtime.types import (
    ProviderHealth,
    RunEvent,
    RunEventKind,
    RunSnapshot,
    ToolAttempt,
    ToolReconciliation,
    TurnRequest,
)


SCHEMA_VERSION = 1


def default_runtime_db() -> Path:
    """Return ``~/.kageha/runtime/runtime.db``."""
    path = kageha_home() / "runtime" / "runtime.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _json(value: Any) -> str:
    from kageha.obs.events import redact

    return json.dumps(
        redact(value),
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )


class RuntimeStore:
    """Transactional event journal plus rebuildable materialized projections."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or default_runtime_db()).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._ensure_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            if version > 100:
                raise RuntimeError(
                    f"runtime database schema {version} is newer than supported "
                    f"{SCHEMA_VERSION}"
                )
            if version not in (0, SCHEMA_VERSION):
                # Early development: rebuild when the on-disk schema diverges.
                tables = self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                self._conn.execute("PRAGMA foreign_keys=OFF")
                self._conn.execute("BEGIN IMMEDIATE")
                for (name,) in tables:
                    self._conn.execute(f'DROP TABLE IF EXISTS "{name}"')
                self._conn.execute("PRAGMA user_version=0")
                self._conn.execute("COMMIT")
                self._conn.execute("PRAGMA foreign_keys=ON")
                version = 0
            if version == 0:
                self._conn.executescript(
                    """
                    BEGIN IMMEDIATE;
                    CREATE TABLE sessions (
                        id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        metadata_json TEXT NOT NULL
                    );
                    CREATE TABLE turns (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL REFERENCES sessions(id),
                        objective TEXT NOT NULL,
                        status TEXT NOT NULL,
                        phase TEXT NOT NULL,
                        snapshot_json TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        UNIQUE(session_id, id)
                    );
                    CREATE TABLE events (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL REFERENCES sessions(id),
                        turn_id TEXT NOT NULL REFERENCES turns(id),
                        sequence INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        idempotency_key TEXT,
                        UNIQUE(turn_id, sequence),
                        UNIQUE(idempotency_key)
                    );
                    CREATE INDEX events_session_turn
                        ON events(session_id, turn_id, sequence);
                    CREATE TABLE plans (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        plan_json TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        UNIQUE(turn_id, version)
                    );
                    CREATE TABLE goals (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        goal_key TEXT NOT NULL,
                        goal_json TEXT NOT NULL,
                        state TEXT NOT NULL,
                        updated_at REAL NOT NULL,
                        UNIQUE(turn_id, goal_key)
                    );
                    CREATE TABLE tool_attempts (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        tool_call_id TEXT NOT NULL,
                        tool_name TEXT NOT NULL,
                        arguments_hash TEXT NOT NULL,
                        arguments_json TEXT NOT NULL,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        side_effect TEXT NOT NULL,
                        risk_class TEXT NOT NULL,
                        policy_grant TEXT NOT NULL,
                        deadline_at REAL,
                        state TEXT NOT NULL,
                        result TEXT NOT NULL,
                        error TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    );
                    CREATE INDEX tool_attempts_turn
                        ON tool_attempts(session_id, turn_id, updated_at);
                    CREATE TABLE checkpoints (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        snapshot_json TEXT NOT NULL,
                        artifact_path TEXT NOT NULL,
                        created_at REAL NOT NULL
                    );
                    CREATE TABLE artifact_manifest (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        path TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        media_type TEXT NOT NULL,
                        validation_json TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        UNIQUE(session_id, path)
                    );
                    CREATE TABLE provider_health (
                        provider TEXT NOT NULL,
                        model_id TEXT NOT NULL,
                        available INTEGER NOT NULL,
                        state TEXT NOT NULL,
                        latency_ms REAL NOT NULL,
                        failure_class TEXT,
                        error TEXT NOT NULL,
                        capabilities_json TEXT NOT NULL,
                        checked_at REAL NOT NULL,
                        circuit_open_until REAL,
                        PRIMARY KEY(provider, model_id)
                    );
                    CREATE TABLE approvals (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        action TEXT NOT NULL,
                        decision TEXT NOT NULL,
                        security_profile TEXT NOT NULL,
                        sandboxed INTEGER NOT NULL,
                        detail_json TEXT NOT NULL,
                        created_at REAL NOT NULL
                    );
                    CREATE TABLE processes (
                        name TEXT PRIMARY KEY,
                        pid INTEGER,
                        state TEXT NOT NULL,
                        executable TEXT NOT NULL,
                        config_hash TEXT NOT NULL,
                        restart_count INTEGER NOT NULL,
                        last_heartbeat REAL,
                        detail_json TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    );
                    CREATE TABLE benchmark_runs (
                        id TEXT PRIMARY KEY,
                        suite TEXT NOT NULL,
                        configuration_json TEXT NOT NULL,
                        environment_json TEXT NOT NULL,
                        metrics_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at REAL NOT NULL
                    );
                    CREATE TABLE channel_messages (
                        id TEXT PRIMARY KEY,
                        channel TEXT NOT NULL,
                        identity_key TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        external_id TEXT NOT NULL,
                        dedup_key TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        attempts INTEGER NOT NULL,
                        available_at REAL NOT NULL,
                        claimed_at REAL,
                        delivered_at REAL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        UNIQUE(channel, direction, dedup_key)
                    );
                    CREATE INDEX channel_messages_ready
                        ON channel_messages(channel, direction, status, available_at);
                    CREATE INDEX channel_messages_identity
                        ON channel_messages(channel, identity_key, created_at);
                    CREATE TABLE metric_points (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        value REAL NOT NULL,
                        unit TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        tool_attempt_id TEXT NOT NULL,
                        labels_json TEXT NOT NULL,
                        created_at REAL NOT NULL
                    );
                    CREATE INDEX metric_points_name_time
                        ON metric_points(name, created_at);
                    CREATE TABLE trace_spans (
                        id TEXT PRIMARY KEY,
                        trace_id TEXT NOT NULL,
                        parent_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        status TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        attributes_json TEXT NOT NULL,
                        started_at REAL NOT NULL,
                        ended_at REAL
                    );
                    CREATE INDEX trace_spans_trace ON trace_spans(trace_id, started_at);
                    PRAGMA user_version=1;
                    COMMIT;
                    """
                )

    def start_turn(
        self,
        request: TurnRequest,
        *,
        session_id: str | None = None,
        turn_id: str | None = None,
        max_usd: float = 2.0,
    ) -> tuple[RunEvent, RunSnapshot]:
        objective = (request.objective or "").strip()
        if not objective:
            raise ValueError("turn objective cannot be empty")
        from kageha.obs.events import redact

        persisted_objective = str(redact(objective))
        sid = session_id or request.session_id or uuid.uuid4().hex[:12]
        tid = turn_id or uuid.uuid4().hex[:16]
        now = time.time()
        idempotency_key = request.idempotency_key or f"turn:{sid}:{tid}"
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    "SELECT * FROM events WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    event = self._row_event(existing)
                    snapshot = self.get_snapshot(event.turn_id, connection=self._conn)
                    self._conn.execute("COMMIT")
                    return event, snapshot
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO sessions
                        (id, status, created_at, updated_at, metadata_json)
                    VALUES (?, 'active', ?, ?, ?)
                    """,
                    (
                        sid,
                        now,
                        now,
                        _json(request.metadata),
                    ),
                )
                event = RunEvent.create(
                    session_id=sid,
                    turn_id=tid,
                    sequence=1,
                    kind=RunEventKind.ACCEPTED,
                    payload={
                        "objective": persisted_objective,
                        "max_steps": request.max_steps or 40,
                        "max_usd": max_usd,
                        "metadata": {
                            **request.metadata,
                            "user_id": request.user_id,
                            "agent_id": request.agent_id,
                            "channel_key": request.channel_key,
                            "project_root": request.project_root,
                            "security_profile": request.security_profile.value,
                        },
                    },
                    idempotency_key=idempotency_key,
                )
                snapshot = reduce_event(None, event)
                self._conn.execute(
                    """
                    INSERT INTO turns
                        (id, session_id, objective, status, phase, snapshot_json,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tid,
                        sid,
                        persisted_objective,
                        snapshot.status,
                        snapshot.phase.value,
                        _json(snapshot.to_dict()),
                        now,
                        now,
                    ),
                )
                self._insert_event(event, self._conn)
                self._insert_checkpoint(snapshot, self._conn)
                self._conn.execute(
                    "UPDATE sessions SET updated_at=? WHERE id=?",
                    (now, sid),
                )
                self._conn.execute("COMMIT")
                self._maybe_inject_crash(event.kind)
                return event, snapshot
            except Exception:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def append_event(
        self,
        *,
        session_id: str,
        turn_id: str,
        kind: RunEventKind,
        payload: dict[str, Any] | None = None,
        idempotency_key: str = "",
    ) -> tuple[RunEvent, RunSnapshot]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if idempotency_key:
                    existing = self._conn.execute(
                        "SELECT * FROM events WHERE idempotency_key=?",
                        (idempotency_key,),
                    ).fetchone()
                    if existing is not None:
                        event = self._row_event(existing)
                        snapshot = self.get_snapshot(turn_id, connection=self._conn)
                        self._conn.execute("COMMIT")
                        return event, snapshot
                snapshot = self.get_snapshot(turn_id, connection=self._conn)
                sequence = snapshot.last_sequence + 1
                event = RunEvent.create(
                    session_id=session_id,
                    turn_id=turn_id,
                    sequence=sequence,
                    kind=kind,
                    payload=payload,
                    idempotency_key=idempotency_key,
                )
                updated = reduce_event(snapshot, event)
                self._insert_event(event, self._conn)
                self._insert_checkpoint(updated, self._conn)
                self._conn.execute(
                    """
                    UPDATE turns
                    SET status=?, phase=?, snapshot_json=?, updated_at=?
                    WHERE id=? AND session_id=?
                    """,
                    (
                        updated.status,
                        updated.phase.value,
                        _json(updated.to_dict()),
                        updated.updated_at,
                        turn_id,
                        session_id,
                    ),
                )
                self._conn.execute(
                    "UPDATE sessions SET status=?, updated_at=? WHERE id=?",
                    (
                        "complete" if updated.terminal else "active",
                        updated.updated_at,
                        session_id,
                    ),
                )
                if kind == RunEventKind.PLANNED:
                    self._persist_plan(updated, self._conn)
                self._conn.execute("COMMIT")
                self._maybe_inject_crash(event.kind)
                return event, updated
            except Exception:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def _insert_event(self, event: RunEvent, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            INSERT INTO events
                (id, session_id, turn_id, sequence, kind, payload_json,
                 created_at, idempotency_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULLIF(?, ''))
            """,
            (
                event.id,
                event.session_id,
                event.turn_id,
                event.sequence,
                event.kind.value,
                _json(event.payload),
                event.created_at,
                event.idempotency_key,
            ),
        )

    def _insert_checkpoint(
        self,
        snapshot: RunSnapshot,
        conn: sqlite3.Connection,
    ) -> None:
        checkpoint_id = hashlib.sha256(
            f"{snapshot.turn_id}:{snapshot.last_sequence}".encode()
        ).hexdigest()[:32]
        conn.execute(
            """
            INSERT OR REPLACE INTO checkpoints
                (id, session_id, turn_id, sequence, snapshot_json,
                 artifact_path, created_at)
            VALUES (?, ?, ?, ?, ?, '', ?)
            """,
            (
                checkpoint_id,
                snapshot.session_id,
                snapshot.turn_id,
                snapshot.last_sequence,
                _json(snapshot.to_dict()),
                snapshot.updated_at,
            ),
        )

    @staticmethod
    def _maybe_inject_crash(kind: RunEventKind) -> None:
        """Test hook: raise only after the transition is durably committed."""
        requested = (os.environ.get("KAGEHA_CRASH_AFTER_EVENT") or "").strip()
        if requested in {"*", kind.value}:
            raise RuntimeError(f"injected crash after durable {kind.value}")

    def _persist_plan(
        self,
        snapshot: RunSnapshot,
        conn: sqlite3.Connection,
    ) -> None:
        plan_id = hashlib.sha256(
            f"{snapshot.turn_id}:{snapshot.plan_version}".encode()
        ).hexdigest()[:32]
        conn.execute(
            """
            INSERT OR REPLACE INTO plans
                (id, session_id, turn_id, version, plan_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                snapshot.session_id,
                snapshot.turn_id,
                snapshot.plan_version,
                _json(snapshot.plan),
                snapshot.updated_at,
            ),
        )
        for index, goal in enumerate(snapshot.goals):
            key = str(goal.get("id") or f"g{index + 1}")
            gid = hashlib.sha256(f"{snapshot.turn_id}:{key}".encode()).hexdigest()[:32]
            conn.execute(
                """
                INSERT OR REPLACE INTO goals
                    (id, session_id, turn_id, goal_key, goal_json, state, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gid,
                    snapshot.session_id,
                    snapshot.turn_id,
                    key,
                    _json(goal),
                    "passed" if goal.get("passes") else "pending",
                    snapshot.updated_at,
                ),
            )

    def get_snapshot(
        self,
        turn_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> RunSnapshot:
        conn = connection or self._conn
        row = conn.execute(
            "SELECT snapshot_json FROM turns WHERE id=?",
            (turn_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown turn: {turn_id}")
        return RunSnapshot.from_dict(json.loads(row["snapshot_json"]))

    def latest_snapshot(self, session_id: str) -> RunSnapshot:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT snapshot_json FROM turns
                WHERE session_id=?
                ORDER BY created_at DESC LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown session: {session_id}")
            return RunSnapshot.from_dict(json.loads(row["snapshot_json"]))

    def latest_incomplete(self, session_id: str) -> RunSnapshot | None:
        try:
            snapshot = self.latest_snapshot(session_id)
        except KeyError:
            return None
        return None if snapshot.terminal else snapshot

    def events(
        self,
        turn_id: str,
        *,
        after_sequence: int = 0,
    ) -> list[RunEvent]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM events
                WHERE turn_id=? AND sequence>?
                ORDER BY sequence
                """,
                (turn_id, after_sequence),
            ).fetchall()
            return [self._row_event(row) for row in rows]

    def _row_event(self, row: sqlite3.Row) -> RunEvent:
        return RunEvent(
            id=row["id"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            sequence=int(row["sequence"]),
            kind=RunEventKind(row["kind"]),
            payload=json.loads(row["payload_json"]),
            created_at=float(row["created_at"]),
            idempotency_key=str(row["idempotency_key"] or ""),
        )

    def rebuild(self, session_id: str) -> dict[str, RunSnapshot]:
        """Recompute every materialized turn snapshot from append-only events."""
        rebuilt: dict[str, RunSnapshot] = {}
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                turn_rows = self._conn.execute(
                    "SELECT id FROM turns WHERE session_id=? ORDER BY created_at",
                    (session_id,),
                ).fetchall()
                for turn_row in turn_rows:
                    turn_id = str(turn_row["id"])
                    snapshot: RunSnapshot | None = None
                    rows = self._conn.execute(
                        "SELECT * FROM events WHERE turn_id=? ORDER BY sequence",
                        (turn_id,),
                    ).fetchall()
                    for row in rows:
                        snapshot = reduce_event(snapshot, self._row_event(row))
                    if snapshot is None:
                        raise RuntimeError(f"turn {turn_id} has no events")
                    self._conn.execute(
                        """
                        UPDATE turns
                        SET status=?, phase=?, snapshot_json=?, updated_at=?
                        WHERE id=?
                        """,
                        (
                            snapshot.status,
                            snapshot.phase.value,
                            _json(snapshot.to_dict()),
                            snapshot.updated_at,
                            turn_id,
                        ),
                    )
                    rebuilt[turn_id] = snapshot
                self._conn.execute("COMMIT")
                return rebuilt
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def begin_tool_attempt(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        side_effect: str,
        risk_class: str,
        policy_grant: str = "",
        deadline_at: float | None = None,
    ) -> tuple[ToolAttempt, bool]:
        rendered = _json(arguments)
        args_hash = hashlib.sha256(rendered.encode()).hexdigest()
        idem_material = (
            f"{turn_id}:{tool_call_id}:{tool_name}:{args_hash}"
            if side_effect == "read"
            else f"{turn_id}:{tool_name}:{args_hash}"
        )
        idem = hashlib.sha256(idem_material.encode()).hexdigest()
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM tool_attempts WHERE idempotency_key=?",
                    (idem,),
                ).fetchone()
                if row is not None:
                    attempt = self._row_tool_attempt(row)
                    self._conn.execute("COMMIT")
                    return attempt, False
                attempt = ToolAttempt(
                    id=uuid.uuid4().hex,
                    session_id=session_id,
                    turn_id=turn_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments_hash=args_hash,
                    idempotency_key=idem,
                    side_effect=side_effect,
                    risk_class=risk_class,
                    policy_grant=policy_grant,
                    deadline_at=deadline_at,
                    state=ToolReconciliation.IN_PROGRESS,
                    created_at=now,
                    updated_at=now,
                )
                self._conn.execute(
                    """
                    INSERT INTO tool_attempts
                        (id, session_id, turn_id, tool_call_id, tool_name,
                         arguments_hash, arguments_json, idempotency_key,
                         side_effect, risk_class, policy_grant, deadline_at,
                         state, result, error, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, ?)
                    """,
                    (
                        attempt.id,
                        session_id,
                        turn_id,
                        tool_call_id,
                        tool_name,
                        args_hash,
                        rendered,
                        idem,
                        side_effect,
                        risk_class,
                        policy_grant,
                        deadline_at,
                        attempt.state.value,
                        now,
                        now,
                    ),
                )
                self._conn.execute("COMMIT")
                return attempt, True
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def complete_tool_attempt(
        self,
        attempt_id: str,
        *,
        result: str = "",
        error: str = "",
    ) -> ToolAttempt:
        state = (
            ToolReconciliation.FAILED
            if error or result.startswith(("ERROR", "DENIED:"))
            else ToolReconciliation.COMPLETED
        )
        with self._lock:
            self._conn.execute(
                """
                UPDATE tool_attempts
                SET state=?, result=?, error=?, updated_at=?
                WHERE id=?
                """,
                (state.value, result, error, time.time(), attempt_id),
            )
            row = self._conn.execute(
                "SELECT * FROM tool_attempts WHERE id=?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown tool attempt: {attempt_id}")
            return self._row_tool_attempt(row)

    def tool_attempt_arguments(self, attempt_id: str) -> dict[str, Any]:
        """Return stored tool arguments for card previews (not emitted raw on SSE)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT arguments_json FROM tool_attempts WHERE id=?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            return {}
        try:
            data = json.loads(row["arguments_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def reconcile_inflight(self, session_id: str) -> list[ToolAttempt]:
        """Mark interrupted reads retryable and interrupted mutations uncertain."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM tool_attempts
                WHERE session_id=? AND state=?
                """,
                (session_id, ToolReconciliation.IN_PROGRESS.value),
            ).fetchall()
            out: list[ToolAttempt] = []
            now = time.time()
            for row in rows:
                state = (
                    ToolReconciliation.RETRYABLE
                    if row["side_effect"] == "read"
                    else ToolReconciliation.UNCERTAIN
                )
                self._conn.execute(
                    "UPDATE tool_attempts SET state=?, updated_at=? WHERE id=?",
                    (state.value, now, row["id"]),
                )
                refreshed = self._conn.execute(
                    "SELECT * FROM tool_attempts WHERE id=?",
                    (row["id"],),
                ).fetchone()
                out.append(self._row_tool_attempt(refreshed))
            return out

    def find_expired_in_progress(
        self, *, now: float | None = None
    ) -> list[ToolAttempt]:
        """Tool attempts still IN_PROGRESS whose deadline has passed.

        Scans across *all* sessions — the watchdog's job is to catch attempts
        orphaned by a crashed/restarted process, which can belong to any
        session, not just the one currently resuming.
        """
        cutoff = now if now is not None else time.time()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM tool_attempts
                WHERE state=? AND deadline_at IS NOT NULL AND deadline_at < ?
                """,
                (ToolReconciliation.IN_PROGRESS.value, cutoff),
            ).fetchall()
            return [self._row_tool_attempt(row) for row in rows]

    def reap_expired_tool_attempt(self, attempt: ToolAttempt) -> ToolAttempt:
        """Resolve one expired IN_PROGRESS attempt: reads → RETRYABLE, mutations
        → UNCERTAIN (mirrors ``reconcile_inflight``'s crash-recovery semantics).

        Idempotent against races with a live process: only transitions rows
        still IN_PROGRESS at the time of update.
        """
        state = (
            ToolReconciliation.RETRYABLE
            if attempt.side_effect == "read"
            else ToolReconciliation.UNCERTAIN
        )
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                UPDATE tool_attempts SET state=?, updated_at=?
                WHERE id=? AND state=?
                """,
                (state.value, now, attempt.id, ToolReconciliation.IN_PROGRESS.value),
            )
            row = self._conn.execute(
                "SELECT * FROM tool_attempts WHERE id=?",
                (attempt.id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown tool attempt: {attempt.id}")
            return self._row_tool_attempt(row)

    def _row_tool_attempt(self, row: sqlite3.Row) -> ToolAttempt:
        return ToolAttempt(
            id=row["id"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            tool_call_id=row["tool_call_id"],
            tool_name=row["tool_name"],
            arguments_hash=row["arguments_hash"],
            idempotency_key=row["idempotency_key"],
            side_effect=row["side_effect"],
            risk_class=row["risk_class"],
            policy_grant=row["policy_grant"],
            deadline_at=row["deadline_at"],
            state=ToolReconciliation(row["state"]),
            result=row["result"],
            error=row["error"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def record_provider_health(self, health: ProviderHealth) -> None:
        data = health.to_dict()
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO provider_health
                    (provider, model_id, available, state, latency_ms,
                     failure_class, error, capabilities_json, checked_at,
                     circuit_open_until)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    health.provider,
                    health.model_id,
                    int(health.available),
                    health.state,
                    health.latency_ms,
                    data["failure_class"],
                    health.error,
                    _json(health.capabilities),
                    health.checked_at,
                    health.circuit_open_until,
                ),
            )

    def provider_health(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM provider_health ORDER BY provider, model_id"
            ).fetchall()
            return [
                {
                    **dict(row),
                    "available": bool(row["available"]),
                    "capabilities": json.loads(row["capabilities_json"]),
                }
                for row in rows
            ]

    def enqueue_channel_message(
        self,
        *,
        channel: str,
        identity_key: str,
        direction: str,
        dedup_key: str,
        payload: dict[str, Any],
        external_id: str = "",
        session_id: str = "",
        turn_id: str = "",
        available_at: float | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Persist an inbound/outbound message exactly once."""
        if direction not in {"inbound", "outbound"}:
            raise ValueError("direction must be inbound or outbound")
        channel = channel.strip().lower()
        identity_key = identity_key.strip()
        dedup_key = dedup_key.strip()
        if not channel or not identity_key or not dedup_key:
            raise ValueError("channel, identity_key and dedup_key are required")
        now = time.time()
        message_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    """
                    SELECT * FROM channel_messages
                    WHERE channel=? AND direction=? AND dedup_key=?
                    """,
                    (channel, direction, dedup_key),
                ).fetchone()
                if existing is not None:
                    self._conn.execute("COMMIT")
                    return self._row_channel_message(existing), False
                self._conn.execute(
                    """
                    INSERT INTO channel_messages
                        (id, channel, identity_key, direction, external_id,
                         dedup_key, session_id, turn_id, payload_json, status,
                         attempts, available_at, claimed_at, delivered_at,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?,
                            NULL, NULL, ?, ?)
                    """,
                    (
                        message_id,
                        channel,
                        identity_key,
                        direction,
                        external_id,
                        dedup_key,
                        session_id,
                        turn_id,
                        _json(payload),
                        available_at if available_at is not None else now,
                        now,
                        now,
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM channel_messages WHERE id=?",
                    (message_id,),
                ).fetchone()
                self._conn.execute("COMMIT")
                return self._row_channel_message(row), True
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def claim_channel_message(
        self,
        *,
        channel: str,
        direction: str,
        identity_key: str = "",
        stale_after_s: float = 300.0,
    ) -> dict[str, Any] | None:
        """Atomically claim the oldest ready message, recovering stale claims."""
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    UPDATE channel_messages
                    SET status='pending', claimed_at=NULL, updated_at=?
                    WHERE channel=? AND direction=? AND status='processing'
                      AND claimed_at<?
                    """,
                    (now, channel, direction, now - max(1.0, stale_after_s)),
                )
                identity_clause = " AND identity_key=?" if identity_key else ""
                params: list[Any] = [channel, direction, now]
                if identity_key:
                    params.append(identity_key)
                row = self._conn.execute(
                    f"""
                    SELECT * FROM channel_messages
                    WHERE channel=? AND direction=? AND status='pending'
                      AND available_at<=? {identity_clause}
                    ORDER BY created_at LIMIT 1
                    """,
                    params,
                ).fetchone()
                if row is None:
                    self._conn.execute("COMMIT")
                    return None
                self._conn.execute(
                    """
                    UPDATE channel_messages
                    SET status='processing', attempts=attempts+1,
                        claimed_at=?, updated_at=?
                    WHERE id=? AND status='pending'
                    """,
                    (now, now, row["id"]),
                )
                claimed = self._conn.execute(
                    "SELECT * FROM channel_messages WHERE id=?",
                    (row["id"],),
                ).fetchone()
                self._conn.execute("COMMIT")
                return self._row_channel_message(claimed)
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def finish_channel_message(
        self,
        message_id: str,
        *,
        delivered: bool,
        retry_after_s: float = 0.0,
        external_id: str = "",
    ) -> dict[str, Any]:
        """Acknowledge delivery or make a failed delivery retryable."""
        now = time.time()
        status = "delivered" if delivered else "pending"
        with self._lock:
            self._conn.execute(
                """
                UPDATE channel_messages
                SET status=?, available_at=?, claimed_at=NULL,
                    delivered_at=?, external_id=CASE WHEN ?='' THEN external_id ELSE ? END,
                    updated_at=?
                WHERE id=?
                """,
                (
                    status,
                    now + max(0.0, retry_after_s),
                    now if delivered else None,
                    external_id,
                    external_id,
                    now,
                    message_id,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM channel_messages WHERE id=?",
                (message_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown channel message: {message_id}")
            return self._row_channel_message(row)

    def _row_channel_message(self, row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        return value

    def status(self) -> dict[str, Any]:
        with self._lock:
            counts = {}
            for table in (
                "sessions",
                "turns",
                "events",
                "tool_attempts",
                "provider_health",
                "processes",
                "benchmark_runs",
                "channel_messages",
                "metric_points",
                "trace_spans",
            ):
                counts[table] = int(
                    self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
            return {
                "database": str(self.path),
                "schema_version": int(
                    self._conn.execute("PRAGMA user_version").fetchone()[0]
                ),
                "wal": str(
                    self._conn.execute("PRAGMA journal_mode").fetchone()[0]
                ).lower()
                == "wal",
                "counts": counts,
            }

    def inspect_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._conn.execute(
                "SELECT * FROM sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise KeyError(f"unknown session: {session_id}")
            turns = self._conn.execute(
                """
                SELECT id, objective, status, phase, created_at, updated_at
                FROM turns WHERE session_id=? ORDER BY created_at
                """,
                (session_id,),
            ).fetchall()
            uncertain = self._conn.execute(
                """
                SELECT id, tool_name, state FROM tool_attempts
                WHERE session_id=? AND state IN (?, ?)
                """,
                (
                    session_id,
                    ToolReconciliation.IN_PROGRESS.value,
                    ToolReconciliation.UNCERTAIN.value,
                ),
            ).fetchall()
            return {
                "session": dict(session),
                "turns": [dict(row) for row in turns],
                "uncertain_tools": [dict(row) for row in uncertain],
            }

    def mark_session_unsandboxed(
        self,
        session_id: str,
        *,
        tool_name: str,
        reason: str,
    ) -> None:
        """Persist the safety-rating cap for approval-fallback execution."""
        with self._lock:
            row = self._conn.execute(
                "SELECT metadata_json FROM sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown session: {session_id}")
            metadata = json.loads(row["metadata_json"] or "{}")
            metadata["unsandboxed"] = True
            events = list(metadata.get("unsandboxed_events") or [])
            events.append(
                {
                    "tool": tool_name,
                    "reason": reason[:500],
                    "at": time.time(),
                }
            )
            metadata["unsandboxed_events"] = events[-50:]
            self._conn.execute(
                "UPDATE sessions SET metadata_json=?, updated_at=? WHERE id=?",
                (_json(metadata), time.time(), session_id),
            )

    def record_approval(
        self,
        *,
        session_id: str,
        turn_id: str,
        action: str,
        decision: str,
        security_profile: str,
        sandboxed: bool,
        detail: dict[str, Any],
        approval_id: str = "",
    ) -> str:
        approval_id = approval_id or uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO approvals
                    (id, session_id, turn_id, action, decision,
                     security_profile, sandboxed, detail_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    decision=excluded.decision,
                    sandboxed=excluded.sandboxed,
                    detail_json=excluded.detail_json
                """,
                (
                    approval_id,
                    session_id,
                    turn_id,
                    action,
                    decision,
                    security_profile,
                    int(sandboxed),
                    _json(detail),
                    time.time(),
                ),
            )
        return approval_id

    def pending_approvals(self, session_id: str = "") -> list[dict[str, Any]]:
        with self._lock:
            where = "WHERE decision='pending'"
            params: tuple[Any, ...] = ()
            if session_id:
                where += " AND session_id=?"
                params = (session_id,)
            rows = self._conn.execute(
                f"SELECT * FROM approvals {where} ORDER BY created_at",
                params,
            ).fetchall()
            return [
                {**dict(row), "detail": json.loads(row["detail_json"])}
                for row in rows
            ]

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    sessions.id,
                    sessions.status,
                    sessions.created_at,
                    sessions.updated_at,
                    COALESCE(
                        (
                            SELECT turns.objective
                            FROM turns
                            WHERE turns.session_id = sessions.id
                            ORDER BY turns.created_at DESC
                            LIMIT 1
                        ),
                        ''
                    ) AS objective,
                    COALESCE(
                        (
                            SELECT turns.status
                            FROM turns
                            WHERE turns.session_id = sessions.id
                            ORDER BY turns.created_at DESC
                            LIMIT 1
                        ),
                        ''
                    ) AS turn_status,
                    COALESCE(
                        (
                            SELECT turns.phase
                            FROM turns
                            WHERE turns.session_id = sessions.id
                            ORDER BY turns.created_at DESC
                            LIMIT 1
                        ),
                        ''
                    ) AS turn_phase,
                    COALESCE(
                        (
                            SELECT turns.id
                            FROM turns
                            WHERE turns.session_id = sessions.id
                            ORDER BY turns.created_at DESC
                            LIMIT 1
                        ),
                        ''
                    ) AS turn_id
                FROM sessions
                ORDER BY sessions.updated_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            return [dict(row) for row in rows]

    def record_process(
        self,
        *,
        name: str,
        pid: int | None,
        state: str,
        executable: str,
        config_hash: str,
        restart_count: int = 0,
        detail: dict[str, Any] | None = None,
    ) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO processes
                    (name, pid, state, executable, config_hash,
                     restart_count, last_heartbeat, detail_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    pid,
                    state,
                    executable,
                    config_hash,
                    restart_count,
                    now,
                    _json(detail or {}),
                    now,
                ),
            )

    def process_rows(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM processes ORDER BY name"
            ).fetchall()
            return [
                {**dict(row), "detail": json.loads(row["detail_json"])}
                for row in rows
            ]

    def record_benchmark(
        self,
        *,
        suite: str,
        configuration: dict[str, Any],
        environment: dict[str, Any],
        metrics: dict[str, Any],
        status: str,
    ) -> str:
        benchmark_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO benchmark_runs
                    (id, suite, configuration_json, environment_json,
                     metrics_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    benchmark_id,
                    suite,
                    _json(configuration),
                    _json(environment),
                    _json(metrics),
                    status,
                    time.time(),
                ),
            )
        return benchmark_id

    def record_metric(
        self,
        name: str,
        value: float,
        *,
        unit: str = "1",
        session_id: str = "",
        turn_id: str = "",
        tool_attempt_id: str = "",
        labels: dict[str, Any] | None = None,
    ) -> str:
        metric_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO metric_points
                    (id, name, value, unit, session_id, turn_id,
                     tool_attempt_id, labels_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metric_id,
                    name,
                    float(value),
                    unit,
                    session_id,
                    turn_id,
                    tool_attempt_id,
                    _json(labels or {}),
                    time.time(),
                ),
            )
        return metric_id

    def start_span(
        self,
        name: str,
        *,
        trace_id: str,
        parent_id: str = "",
        session_id: str = "",
        turn_id: str = "",
        attributes: dict[str, Any] | None = None,
    ) -> str:
        span_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO trace_spans
                    (id, trace_id, parent_id, name, status, session_id,
                     turn_id, attributes_json, started_at, ended_at)
                VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, NULL)
                """,
                (
                    span_id,
                    trace_id,
                    parent_id,
                    name,
                    session_id,
                    turn_id,
                    _json(attributes or {}),
                    time.time(),
                ),
            )
        return span_id

    def finish_span(
        self,
        span_id: str,
        *,
        status: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT attributes_json FROM trace_spans WHERE id=?",
                (span_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown trace span: {span_id}")
            existing = json.loads(row["attributes_json"] or "{}")
            existing.update(attributes or {})
            self._conn.execute(
                """
                UPDATE trace_spans
                SET status=?, attributes_json=?, ended_at=?
                WHERE id=?
                """,
                (status, _json(existing), time.time(), span_id),
            )

    def metric_summary(self, *, since: float = 0.0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT name, unit, COUNT(*) AS points, SUM(value) AS total,
                       AVG(value) AS average, MIN(value) AS minimum,
                       MAX(value) AS maximum
                FROM metric_points
                WHERE created_at>=?
                GROUP BY name, unit
                ORDER BY name
                """,
                (since,),
            ).fetchall()
            return [dict(row) for row in rows]

    def add_artifacts(
        self,
        *,
        session_id: str,
        turn_id: str,
        workspace: Path,
        paths: Iterable[str],
    ) -> None:
        now = time.time()
        with self._lock:
            for raw in paths:
                rel = str(raw)
                path = (workspace / rel).resolve()
                if not path.is_file() or not str(path).startswith(str(workspace.resolve())):
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                artifact_id = hashlib.sha256(
                    f"{session_id}:{rel}".encode()
                ).hexdigest()[:32]
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO artifact_manifest
                        (id, session_id, turn_id, path, sha256, size_bytes,
                         media_type, validation_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, '', '{}', ?)
                    """,
                    (
                        artifact_id,
                        session_id,
                        turn_id,
                        rel,
                        digest,
                        path.stat().st_size,
                        now,
                    ),
                )
