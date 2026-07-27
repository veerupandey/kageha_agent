"""SQLite authority for Kageha memory, episodes, jobs, and audit events."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from kageha.config import kageha_home
from kageha.memory.models import EpisodeRecord, MemoryRecord


SCHEMA_VERSION = 1


class MemoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.home = kageha_home()
        self.path = path or (self.home / "memory" / "memory.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self.recover_jobs()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_schema(self) -> None:
        with self._conn() as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"memory database schema {version} is newer than supported {SCHEMA_VERSION}"
                )
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    claim_key TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    source_role TEXT NOT NULL,
                    source_session_id TEXT NOT NULL,
                    source_turn_id TEXT NOT NULL,
                    source_artifact TEXT NOT NULL,
                    verification_evidence TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    sensitivity TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    project_key TEXT NOT NULL,
                    channel_key TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_accessed REAL,
                    expires_at REAL,
                    supersedes_id TEXT NOT NULL DEFAULT ''
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_dedupe
                    ON memories(content_hash, scope_type, scope_key, state);
                CREATE INDEX IF NOT EXISTS idx_memories_scope
                    ON memories(user_id, agent_id, project_key, channel_key, state);
                CREATE INDEX IF NOT EXISTS idx_memories_claim
                    ON memories(claim_key, scope_type, scope_key, state);

                CREATE TABLE IF NOT EXISTS episodes (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    task TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    verified INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    project_key TEXT NOT NULL,
                    channel_key TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(session_id, turn_id)
                );
                CREATE INDEX IF NOT EXISTS idx_episodes_scope
                    ON episodes(user_id, agent_id, project_key, channel_key, status, verified);

                CREATE TABLE IF NOT EXISTS memory_jobs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_jobs_status
                    ON memory_jobs(status, created_at);

                CREATE TABLE IF NOT EXISTS memory_events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_events_session
                    ON memory_events(session_id, event_type, created_at);
                """
            )
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                        memory_id UNINDEXED,
                        content,
                        claim_key,
                        tokenize='unicode61'
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
                        episode_id UNINDEXED,
                        task,
                        summary,
                        tokenize='unicode61'
                    )
                    """
                )
            except sqlite3.OperationalError as exc:
                raise RuntimeError("Kageha memory requires SQLite FTS5 support") from exc
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @staticmethod
    def _memory_from_row(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(**{key: row[key] for key in MemoryRecord.__dataclass_fields__})

    @staticmethod
    def _episode_from_row(row: sqlite3.Row) -> EpisodeRecord:
        data = {key: row[key] for key in EpisodeRecord.__dataclass_fields__}
        data["verified"] = bool(data["verified"])
        return EpisodeRecord(**data)

    def add_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        session_id: str = "",
        event_id: str = "",
    ) -> str:
        eid = event_id or uuid.uuid4().hex
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO memory_events VALUES (?,?,?,?,?)",
                (
                    eid,
                    event_type,
                    session_id,
                    json.dumps(payload, sort_keys=True),
                    time.time(),
                ),
            )
        return eid

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM memory_events WHERE id=?", (event_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "event_type": row["event_type"],
            "session_id": row["session_id"],
            "payload": json.loads(row["payload_json"] or "{}"),
            "created_at": row["created_at"],
        }

    def latest_event(self, event_type: str, *, session_id: str = "") -> dict[str, Any] | None:
        sql = "SELECT * FROM memory_events WHERE event_type=?"
        args: list[Any] = [event_type]
        if session_id:
            sql += " AND session_id=?"
            args.append(session_id)
        sql += " ORDER BY created_at DESC LIMIT 1"
        with self._conn() as conn:
            row = conn.execute(sql, args).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "event_type": row["event_type"],
            "session_id": row["session_id"],
            "payload": json.loads(row["payload_json"] or "{}"),
            "created_at": row["created_at"],
        }

    def list_events(
        self,
        event_type: str = "",
        *,
        session_id: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        args: list[Any] = []
        if event_type:
            clauses.append("event_type=?")
            args.append(event_type)
        if session_id:
            clauses.append("session_id=?")
            args.append(session_id)
        args.append(max(1, min(200, limit)))
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM memory_events
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                args,
            ).fetchall()
        return [
            {
                "id": row["id"],
                "event_type": row["event_type"],
                "session_id": row["session_id"],
                "payload": json.loads(row["payload_json"] or "{}"),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def add_episode(self, episode: EpisodeRecord) -> bool:
        data = asdict(episode)
        data["verified"] = int(episode.verified)
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO episodes(
                    id, session_id, turn_id, task, summary, status, verified,
                    user_id, agent_id, project_key, channel_key, created_at
                ) VALUES (
                    :id, :session_id, :turn_id, :task, :summary, :status, :verified,
                    :user_id, :agent_id, :project_key, :channel_key, :created_at
                )
                """,
                data,
            )
            if cur.rowcount:
                conn.execute(
                    "INSERT INTO episodes_fts(episode_id, task, summary) VALUES (?,?,?)",
                    (episode.id, episode.task, episode.summary),
                )
        return bool(cur.rowcount)

    def capture_episode_and_job(
        self,
        episode: EpisodeRecord,
        *,
        job_id: str,
        payload: dict[str, Any],
        queue_job: bool,
    ) -> tuple[bool, bool]:
        """Atomically persist the immutable episode and its idempotent job."""
        data = asdict(episode)
        data["verified"] = int(episode.verified)
        now = time.time()
        with self._conn() as conn:
            episode_cur = conn.execute(
                """
                INSERT OR IGNORE INTO episodes(
                    id, session_id, turn_id, task, summary, status, verified,
                    user_id, agent_id, project_key, channel_key, created_at
                ) VALUES (
                    :id, :session_id, :turn_id, :task, :summary, :status, :verified,
                    :user_id, :agent_id, :project_key, :channel_key, :created_at
                )
                """,
                data,
            )
            inserted = bool(episode_cur.rowcount)
            if inserted:
                conn.execute(
                    "INSERT INTO episodes_fts(episode_id, task, summary) VALUES (?,?,?)",
                    (episode.id, episode.task, episode.summary),
                )
            queued = False
            if queue_job:
                job_cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO memory_jobs(
                        id, session_id, turn_id, payload_json, status,
                        attempts, last_error, created_at, updated_at
                    ) VALUES (?,?,?,?, 'pending', 0, '', ?, ?)
                    """,
                    (
                        job_id,
                        episode.session_id,
                        episode.turn_id,
                        json.dumps(payload),
                        now,
                        now,
                    ),
                )
                queued = bool(job_cur.rowcount)
        return inserted, queued

    def get_episode(self, episode_id: str) -> EpisodeRecord | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        return self._episode_from_row(row) if row else None

    def list_episodes(self, *, limit: int = 20) -> list[EpisodeRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM episodes ORDER BY created_at DESC LIMIT ?",
                (max(1, min(500, limit)),),
            ).fetchall()
        return [self._episode_from_row(row) for row in rows]

    def search_episodes_fts(self, fts_query: str, *, limit: int = 30) -> list[EpisodeRecord]:
        if not fts_query:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT e.*
                FROM episodes_fts f
                JOIN episodes e ON e.id=f.episode_id
                WHERE episodes_fts MATCH ?
                ORDER BY bm25(episodes_fts)
                LIMIT ?
                """,
                (fts_query, max(1, min(200, limit))),
            ).fetchall()
        return [self._episode_from_row(row) for row in rows]

    def insert_memory(self, record: MemoryRecord) -> MemoryRecord:
        data = asdict(record)
        with self._conn() as conn:
            existing = conn.execute(
                """
                SELECT * FROM memories
                WHERE content_hash=? AND scope_type=? AND scope_key=? AND state=?
                LIMIT 1
                """,
                (
                    record.content_hash,
                    record.scope_type,
                    record.scope_key,
                    record.state,
                ),
            ).fetchone()
            if existing:
                return self._memory_from_row(existing)
            conn.execute(
                """
                INSERT INTO memories(
                    id, kind, content, claim_key, content_hash, scope_type, scope_key,
                    state, source_role, source_session_id, source_turn_id,
                    source_artifact, verification_evidence, confidence, sensitivity,
                    user_id, agent_id, project_key, channel_key, created_at, updated_at,
                    last_accessed, expires_at, supersedes_id
                ) VALUES (
                    :id, :kind, :content, :claim_key, :content_hash, :scope_type, :scope_key,
                    :state, :source_role, :source_session_id, :source_turn_id,
                    :source_artifact, :verification_evidence, :confidence, :sensitivity,
                    :user_id, :agent_id, :project_key, :channel_key, :created_at, :updated_at,
                    :last_accessed, :expires_at, :supersedes_id
                )
                """,
                data,
            )
            conn.execute(
                "INSERT INTO memories_fts(memory_id, content, claim_key) VALUES (?,?,?)",
                (record.id, record.content, record.claim_key),
            )
        return record

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        return self._memory_from_row(row) if row else None

    def find_memories(self, target: str, *, include_inactive: bool = False) -> list[MemoryRecord]:
        target_s = (target or "").strip()
        if not target_s:
            return []
        states = "" if include_inactive else "AND state IN ('confirmed','candidate')"
        with self._conn() as conn:
            exact = conn.execute("SELECT * FROM memories WHERE id=?", (target_s,)).fetchone()
            if exact and (
                include_inactive
                or exact["state"] in {"confirmed", "candidate"}
            ):
                return [self._memory_from_row(exact)]
            rows = conn.execute(
                f"""
                SELECT * FROM memories
                WHERE lower(content) LIKE lower(?) {states}
                ORDER BY updated_at DESC
                LIMIT 20
                """,
                (f"%{target_s}%",),
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def list_memories(
        self,
        *,
        state: str = "",
        scope_type: str = "",
        user_id: str = "",
        agent_id: str = "",
        project_key: str = "",
        session_id: str = "",
        channel_key: str | None = None,
        kinds: list[str] | tuple[str, ...] | None = None,
        source_role: str = "",
        limit: int = 100,
    ) -> list[MemoryRecord]:
        clauses = ["1=1"]
        args: list[Any] = []
        for column, value in (
            ("state", state),
            ("scope_type", scope_type),
            ("user_id", user_id),
            ("agent_id", agent_id),
            ("project_key", project_key),
            ("source_session_id", session_id),
            ("source_role", source_role),
        ):
            if value:
                clauses.append(f"{column}=?")
                args.append(value)
        if channel_key is not None:
            clauses.append("channel_key=?")
            args.append(channel_key)
        kind_list = [k for k in (kinds or []) if k]
        if kind_list:
            placeholders = ",".join("?" for _ in kind_list)
            clauses.append(f"kind IN ({placeholders})")
            args.extend(kind_list)
        args.append(max(1, min(1000, limit)))
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM memories
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                args,
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def active_claims(
        self, claim_key: str, *, scope_type: str, scope_key: str
    ) -> list[MemoryRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memories
                WHERE claim_key=? AND scope_type=? AND scope_key=?
                  AND state='confirmed'
                ORDER BY updated_at DESC
                """,
                (claim_key, scope_type, scope_key),
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def update_state(
        self,
        memory_id: str,
        state: str,
        *,
        supersedes_id: str | None = None,
    ) -> MemoryRecord | None:
        now = time.time()
        with self._conn() as conn:
            if supersedes_id is None:
                conn.execute(
                    "UPDATE memories SET state=?, updated_at=? WHERE id=?",
                    (state, now, memory_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE memories
                    SET state=?, supersedes_id=?, updated_at=?
                    WHERE id=?
                    """,
                    (state, supersedes_id, now, memory_id),
                )
        return self.get_memory(memory_id)

    def supersede(self, old_id: str, replacement: MemoryRecord) -> MemoryRecord:
        """Atomically supersede one record and insert its replacement."""
        data = asdict(replacement)
        with self._conn() as conn:
            old = conn.execute("SELECT id FROM memories WHERE id=?", (old_id,)).fetchone()
            if old is None:
                raise KeyError(f"memory not found: {old_id}")
            conn.execute(
                """
                INSERT INTO memories(
                    id, kind, content, claim_key, content_hash, scope_type, scope_key,
                    state, source_role, source_session_id, source_turn_id,
                    source_artifact, verification_evidence, confidence, sensitivity,
                    user_id, agent_id, project_key, channel_key, created_at, updated_at,
                    last_accessed, expires_at, supersedes_id
                ) VALUES (
                    :id, :kind, :content, :claim_key, :content_hash, :scope_type, :scope_key,
                    :state, :source_role, :source_session_id, :source_turn_id,
                    :source_artifact, :verification_evidence, :confidence, :sensitivity,
                    :user_id, :agent_id, :project_key, :channel_key, :created_at, :updated_at,
                    :last_accessed, :expires_at, :supersedes_id
                )
                """,
                data,
            )
            conn.execute(
                "INSERT INTO memories_fts(memory_id, content, claim_key) VALUES (?,?,?)",
                (replacement.id, replacement.content, replacement.claim_key),
            )
            conn.execute(
                """
                UPDATE memories
                SET state='superseded', updated_at=?
                WHERE id=?
                """,
                (replacement.updated_at, old_id),
            )
        return replacement

    def touch(self, memory_ids: Iterable[str]) -> None:
        ids = [mid for mid in memory_ids if mid]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE memories SET last_accessed=? WHERE id IN ({placeholders})",
                [time.time(), *ids],
            )

    def search_memories_fts(self, fts_query: str, *, limit: int = 40) -> list[MemoryRecord]:
        if not fts_query:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT m.*
                FROM memories_fts f
                JOIN memories m ON m.id=f.memory_id
                WHERE memories_fts MATCH ?
                ORDER BY bm25(memories_fts)
                LIMIT ?
                """,
                (fts_query, max(1, min(200, limit))),
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def enqueue_job(
        self,
        *,
        job_id: str,
        session_id: str,
        turn_id: str,
        payload: dict[str, Any],
    ) -> bool:
        now = time.time()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO memory_jobs(
                    id, session_id, turn_id, payload_json, status,
                    attempts, last_error, created_at, updated_at
                ) VALUES (?,?,?,?, 'pending', 0, '', ?, ?)
                """,
                (job_id, session_id, turn_id, json.dumps(payload), now, now),
            )
        return bool(cur.rowcount)

    def recover_jobs(self, *, stale_seconds: float = 300.0) -> None:
        cutoff = time.time() - stale_seconds
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE memory_jobs
                SET status='pending', updated_at=?
                WHERE status='processing' AND updated_at<?
                """,
                (time.time(), cutoff),
            )

    def claim_job(self) -> dict[str, Any] | None:
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM memory_jobs
                WHERE status='pending'
                ORDER BY created_at, rowid
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            now = time.time()
            conn.execute(
                """
                UPDATE memory_jobs
                SET status='processing', attempts=attempts+1, updated_at=?
                WHERE id=? AND status='pending'
                """,
                (now, row["id"]),
            )
            conn.commit()
            return {
                "id": row["id"],
                "session_id": row["session_id"],
                "turn_id": row["turn_id"],
                "payload": json.loads(row["payload_json"]),
                "attempts": int(row["attempts"]) + 1,
            }
        finally:
            conn.close()

    def finish_job(self, job_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE memory_jobs SET status='done', updated_at=? WHERE id=?",
                (time.time(), job_id),
            )

    def fail_job(self, job_id: str, error: str, *, terminal: bool = False) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE memory_jobs
                SET status=?, last_error=?, updated_at=?
                WHERE id=?
                """,
                ("failed" if terminal else "pending", error[:1000], time.time(), job_id),
            )

    def job_counts(self) -> dict[str, int]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, count(*) AS n FROM memory_jobs GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}

    def stats(self) -> dict[str, Any]:
        with self._conn() as conn:
            memories = int(conn.execute("SELECT count(*) FROM memories").fetchone()[0])
            episodes = int(conn.execute("SELECT count(*) FROM episodes").fetchone()[0])
            states = {
                str(row["state"]): int(row["n"])
                for row in conn.execute(
                    "SELECT state, count(*) AS n FROM memories GROUP BY state"
                ).fetchall()
            }
        return {
            "schema_version": SCHEMA_VERSION,
            "database": str(self.path),
            "memories": memories,
            "episodes": episodes,
            "states": states,
            "jobs": self.job_counts(),
        }

    def rebuild_fts(self) -> int:
        with self._conn() as conn:
            conn.execute("DELETE FROM memories_fts")
            rows = conn.execute("SELECT id, content, claim_key FROM memories").fetchall()
            conn.executemany(
                "INSERT INTO memories_fts(memory_id, content, claim_key) VALUES (?,?,?)",
                [(row["id"], row["content"], row["claim_key"]) for row in rows],
            )
            conn.execute("DELETE FROM episodes_fts")
            eps = conn.execute("SELECT id, task, summary FROM episodes").fetchall()
            conn.executemany(
                "INSERT INTO episodes_fts(episode_id, task, summary) VALUES (?,?,?)",
                [(row["id"], row["task"], row["summary"]) for row in eps],
            )
        return len(rows)

    def export_markdown(self) -> str:
        rows = self.list_memories(state="confirmed", limit=1000)
        lines = [
            "# Kageha memory export",
            "",
            "Generated from the canonical memory database. Do not edit as an authority.",
            "",
        ]
        current = ""
        for rec in sorted(rows, key=lambda row: (row.scope_type, row.kind, row.created_at)):
            heading = f"{rec.scope_type} / {rec.kind}"
            if heading != current:
                lines.extend([f"## {heading}", ""])
                current = heading
            lines.append(f"- {rec.content} `[{rec.id}]`")
        return "\n".join(lines).rstrip() + "\n"
