"""capture operations for MemoryService."""

from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
from dataclasses import asdict
from typing import Any

from kageha.memory.models import (
    CaptureReceipt,
    EpisodeRecord,
    MemoryKind,
    MemoryRecord,
    MemorySensitivity,
    MemoryState,
    TurnMemoryInput,
)
from kageha.memory.security import inspect_memory_text
from kageha.memory.util import (
    _INSTRUCTION_RE,
    _PREFERENCE_RE,
    _USER_FACT_RE,
    _DECISION_RE,
    _PROJECT_FACT_RE,
    _REMEMBER_RE,
    _SUCCESS,
    _claim_key,
    _content_hash,
    _default_scope,
    _infer_kind,
    _scope_key,
    memory_enabled,
    memory_learning_enabled,
    project_key,
)


class MemoryCaptureMixin:
    def capture_turn(self, turn: TurnMemoryInput) -> CaptureReceipt:
        if not memory_enabled():
            return CaptureReceipt("", "", False, memory_enabled=False)

        safe_task = inspect_memory_text(turn.task).safe_text
        safe_user = inspect_memory_text(turn.user_text).safe_text
        safe_assistant = inspect_memory_text(turn.assistant_text).safe_text
        safe_facts = [
            result.safe_text
            for value in turn.verified_facts
            if not (result := inspect_memory_text(value)).blocked
        ]
        safe_artifacts = [
            result.safe_text
            for value in turn.artifacts
            if not (result := inspect_memory_text(value)).blocked
        ]
        safe_recoveries = [
            result.safe_text
            for value in turn.recovered_failures
            if not (result := inspect_memory_text(value)).blocked
        ]
        evidence_result = inspect_memory_text(turn.verification_evidence)
        safe_evidence = evidence_result.safe_text
        pkey = turn.project_scope_key or project_key(turn.project_root)
        turn_id = turn.turn_id or hashlib.sha256(
            f"{turn.session_id}:{safe_task}:{time.time_ns()}".encode()
        ).hexdigest()[:12]
        episode_id = f"{turn.session_id}:{turn_id}"
        episode = EpisodeRecord(
            id=episode_id,
            session_id=turn.session_id,
            turn_id=turn_id,
            task=safe_task[:4000],
            summary=safe_assistant[:8000],
            status=turn.status,
            verified=bool(turn.verified and turn.status in _SUCCESS),
            user_id=turn.user_id or "local",
            agent_id=turn.agent_id or "main",
            project_key=pkey,
            channel_key=turn.channel_key,
            created_at=time.time(),
        )
        raw_job_key = turn.idempotency_key or f"capture:{episode_id}"
        job_key = hashlib.sha256(raw_job_key.encode()).hexdigest()
        payload = asdict(turn)
        payload.update(
            {
                "turn_id": turn_id,
                "task": safe_task,
                "user_text": safe_user,
                "assistant_text": safe_assistant,
                "verified_facts": safe_facts,
                "verification_evidence": safe_evidence,
                "artifacts": safe_artifacts,
                "recovered_failures": safe_recoveries,
                "project_root": "",
                "project_scope_key": pkey,
            }
        )
        inserted, queued = self.store.capture_episode_and_job(
            episode,
            job_id=job_key,
            payload=payload,
            queue_job=memory_learning_enabled() and turn.learn,
        )
        self.store.add_event(
            "capture",
            {
                "episode_id": episode_id,
                "status": turn.status,
                "verified": episode.verified,
                "queued": queued,
            },
            session_id=turn.session_id,
        )
        return CaptureReceipt(episode_id, job_key if queued else "", queued)



    def prune_idle(self) -> dict[str, Any]:
        """Expire unused candidates and long-idle non-instruction claims."""
        if not memory_enabled():
            return {
                "expired_candidates": 0,
                "expired_idle": 0,
                "forgotten": [],
            }
        candidate_ttl = float(
            os.environ.get("KAGEHA_MEMORY_CANDIDATE_TTL_DAYS", "14")
        )
        idle_ttl = float(os.environ.get("KAGEHA_MEMORY_IDLE_TTL_DAYS", "90"))
        now = time.time()
        expired_candidates = 0
        expired_idle = 0
        forgotten: list[dict[str, str]] = []
        protected = {
            MemoryKind.INSTRUCTION.value,
            MemoryKind.PREFERENCE.value,
            MemoryKind.USER_FACT.value,
        }

        for rec in self.store.list_memories(
            state=MemoryState.CANDIDATE.value,
            limit=1000,
        ):
            ref = rec.last_accessed or rec.updated_at or rec.created_at
            if (now - ref) / 86400.0 < max(1.0, candidate_ttl):
                continue
            self.store.update_state(rec.id, MemoryState.EXPIRED.value)
            expired_candidates += 1
            forgotten.append(
                {
                    "id": rec.id,
                    "reason": "candidate_ttl",
                    "content": rec.content[:160],
                }
            )

        if idle_ttl > 0:
            for rec in self.store.list_memories(
                state=MemoryState.CONFIRMED.value,
                limit=1000,
            ):
                if rec.kind in protected:
                    continue
                ref = rec.last_accessed or rec.created_at
                if (now - ref) / 86400.0 < max(7.0, idle_ttl):
                    continue
                self.store.update_state(rec.id, MemoryState.EXPIRED.value)
                expired_idle += 1
                forgotten.append(
                    {
                        "id": rec.id,
                        "reason": "idle_ttl",
                        "content": rec.content[:160],
                    }
                )

        if expired_candidates or expired_idle:
            self.store.add_event(
                "prune_idle",
                {
                    "expired_candidates": expired_candidates,
                    "expired_idle": expired_idle,
                    "candidate_ttl_days": candidate_ttl,
                    "idle_ttl_days": idle_ttl,
                    "forgotten": forgotten[:100],
                },
            )
        return {
            "expired_candidates": expired_candidates,
            "expired_idle": expired_idle,
            "forgotten": forgotten,
        }



    def _process_capture(self, turn: TurnMemoryInput) -> None:
        if turn.status not in _SUCCESS or not turn.verified:
            self.store.add_event(
                "promotion_rejected",
                {"turn_id": turn.turn_id, "reason": "turn_not_successfully_verified"},
                session_id=turn.session_id,
            )
            return

        candidates = self._extract_candidates(turn)
        for candidate in candidates:
            self._promote_candidate(**candidate)



    def _extract_candidates(self, turn: TurnMemoryInput) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(
            content: str,
            *,
            kind: str,
            confidence: float,
            source_role: str = "user",
            artifact: str = "",
        ) -> None:
            text = re.sub(r"\s+", " ", (content or "").strip().strip("-* "))
            if len(text) < 8:
                return
            key = _content_hash(text)
            if key in seen:
                return
            seen.add(key)
            out.append(
                {
                    "content": text[:1000],
                    "kind": kind,
                    "confidence": confidence,
                    "turn": turn,
                    "source_role": source_role,
                    "artifact": artifact,
                }
            )

        user = re.sub(r"\s+", " ", turn.user_text.strip())
        remember = _REMEMBER_RE.match(user)
        if remember:
            text = remember.group(1).strip()
            add(text, kind=_infer_kind(text), confidence=0.99)
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", user):
            sentence = sentence.strip()
            if not sentence:
                continue
            pref = _PREFERENCE_RE.search(sentence)
            if pref:
                add(
                    f"User prefers {pref.group(1).strip()}",
                    kind=MemoryKind.PREFERENCE.value,
                    confidence=0.94,
                )
            elif _INSTRUCTION_RE.search(sentence):
                add(sentence, kind=MemoryKind.INSTRUCTION.value, confidence=0.92)
            elif _USER_FACT_RE.search(sentence):
                add(sentence, kind=MemoryKind.USER_FACT.value, confidence=0.90)
            elif _DECISION_RE.search(sentence):
                add(sentence, kind=MemoryKind.DECISION.value, confidence=0.86)
            elif _PROJECT_FACT_RE.search(sentence):
                add(sentence, kind=MemoryKind.PROJECT_FACT.value, confidence=0.84)

        for fact in turn.verified_facts:
            add(
                fact,
                kind=_infer_kind(fact),
                confidence=0.93,
                source_role="verifier",
            )

        for artifact in turn.artifacts:
            if artifact:
                add(
                    f"Validated artifact produced: {artifact}",
                    kind=MemoryKind.ARTIFACT_FACT.value,
                    confidence=0.88,
                    source_role="verifier",
                    artifact=artifact,
                )
        for recovery in turn.recovered_failures:
            add(
                f"Recovered procedure candidate: {recovery}",
                kind=MemoryKind.PROCEDURE_CANDIDATE.value,
                confidence=0.70,
                source_role="verifier",
            )

        # Claude/Codex-style LLM extract (union with regex). Bounded so the
        # capture worker never stalls the job queue on model latency.
        try:
            from kageha.memory.extract import extract_memories_llm, llm_extract_enabled

            if llm_extract_enabled() and len(out) < 2:
                # Enrich when regex/verifier found little; skip when already rich
                # so the job worker stays fast under load.
                llm_items = extract_memories_llm(turn)
                for item in llm_items:
                    add(
                        item["content"],
                        kind=item["kind"],
                        confidence=float(item["confidence"]),
                        source_role=item.get("source_role") or "user",
                        artifact=item.get("artifact") or "",
                    )
                if llm_items:
                    self.store.add_event(
                        "llm_extract",
                        {
                            "turn_id": turn.turn_id,
                            "count": len(llm_items),
                            "kinds": [i["kind"] for i in llm_items],
                        },
                        session_id=turn.session_id,
                    )
        except Exception as exc:  # noqa: BLE001
            self.store.add_event(
                "llm_extract_error",
                {"turn_id": turn.turn_id, "error": str(exc)[:300]},
                session_id=turn.session_id,
            )
        return out



    def _record_for(
        self,
        *,
        content: str,
        kind: str,
        state: str,
        confidence: float,
        sensitivity: str,
        source_role: str,
        session_id: str,
        turn_id: str,
        artifact: str,
        evidence: str,
        scope_type: str,
        user_id: str,
        agent_id: str,
        pkey: str,
        channel_key: str,
        supersedes_id: str = "",
    ) -> MemoryRecord:
        now = time.time()
        skey = _scope_key(
            scope_type,
            user_id=user_id,
            agent_id=agent_id,
            project=pkey,
            session_id=session_id,
            channel_key=channel_key,
        )
        return MemoryRecord(
            id=uuid.uuid4().hex,
            kind=kind,
            content=content,
            claim_key=_claim_key(kind, content),
            content_hash=_content_hash(content),
            scope_type=scope_type,
            scope_key=skey,
            state=state,
            source_role=source_role,
            source_session_id=session_id,
            source_turn_id=turn_id,
            source_artifact=artifact,
            verification_evidence=evidence[:2000],
            confidence=max(0.0, min(1.0, confidence)),
            sensitivity=sensitivity,
            user_id=user_id,
            agent_id=agent_id,
            project_key=pkey,
            channel_key=channel_key,
            created_at=now,
            updated_at=now,
            supersedes_id=supersedes_id,
        )



    def _promote_candidate(
        self,
        *,
        content: str,
        kind: str,
        confidence: float,
        turn: TurnMemoryInput,
        source_role: str,
        artifact: str = "",
    ) -> MemoryRecord | list[str] | None:
        security = inspect_memory_text(content)
        if security.blocked:
            self.store.add_event(
                "redaction",
                {"findings": security.findings, "action": "discarded"},
                session_id=turn.session_id,
            )
            return None
        if confidence < 0.55:
            self.store.add_event(
                "promotion_rejected",
                {"reason": "low_confidence", "confidence": confidence},
                session_id=turn.session_id,
            )
            return None

        sensitive = security.sensitivity not in {
            MemorySensitivity.NORMAL.value,
        }
        state = (
            MemoryState.CONFIRMED.value
            if confidence >= 0.80 and not sensitive
            else MemoryState.CANDIDATE.value
        )
        if security.sensitivity == MemorySensitivity.PROMPT_INJECTION.value:
            state = MemoryState.QUARANTINED.value
        scope = _default_scope(kind)
        pkey = turn.project_scope_key or project_key(turn.project_root)
        record = self._record_for(
            content=security.safe_text,
            kind=kind,
            state=state,
            confidence=confidence,
            sensitivity=security.sensitivity,
            source_role=source_role,
            session_id=turn.session_id,
            turn_id=turn.turn_id,
            artifact=artifact,
            evidence=turn.verification_evidence,
            scope_type=scope,
            user_id=turn.user_id or "local",
            agent_id=turn.agent_id or "main",
            pkey=pkey,
            channel_key=turn.channel_key,
        )

        active = self.store.active_claims(
            record.claim_key,
            scope_type=record.scope_type,
            scope_key=record.scope_key,
        )
        if any(item.content_hash == record.content_hash for item in active):
            self.store.add_event(
                "promotion_rejected",
                {"reason": "duplicate", "claim_key": record.claim_key},
                session_id=turn.session_id,
            )
            return active[0]
        if active and state == MemoryState.CONFIRMED.value:
            old_has_user_authority = any(item.source_role == "user" for item in active)
            if source_role == "user" and not old_has_user_authority:
                record.supersedes_id = active[0].id
                stored = self.store.supersede(active[0].id, record)
                for old in active[1:]:
                    self.store.update_state(old.id, MemoryState.SUPERSEDED.value)
                self.vector.index(stored)
                self.store.add_event(
                    "promotion",
                    {
                        "memory_id": stored.id,
                        "kind": stored.kind,
                        "state": stored.state,
                        "confidence": stored.confidence,
                        "sensitivity": stored.sensitivity,
                        "superseded_lower_authority": [item.id for item in active],
                    },
                    session_id=turn.session_id,
                )
                return stored
            if source_role != "user" and old_has_user_authority:
                record.state = MemoryState.QUARANTINED.value
                self.store.add_event(
                    "promotion_rejected",
                    {
                        "reason": "older_user_evidence_has_higher_authority",
                        "claim_key": record.claim_key,
                        "existing": [item.id for item in active],
                    },
                    session_id=turn.session_id,
                )
            else:
                # Equal-authority contradictions are quarantined rather than
                # silently choosing one side.
                for old in active:
                    self.store.update_state(old.id, MemoryState.QUARANTINED.value)
                record.state = MemoryState.QUARANTINED.value
                self.store.add_event(
                    "conflict",
                    {
                        "claim_key": record.claim_key,
                        "existing": [item.id for item in active],
                        "new": record.id,
                    },
                    session_id=turn.session_id,
                )

        stored = self.store.insert_memory(record)
        if stored.state == MemoryState.CONFIRMED.value:
            self.vector.index(stored)
        self.store.add_event(
            "promotion",
            {
                "memory_id": stored.id,
                "kind": stored.kind,
                "state": stored.state,
                "confidence": stored.confidence,
                "sensitivity": stored.sensitivity,
            },
            session_id=turn.session_id,
        )
        return stored



