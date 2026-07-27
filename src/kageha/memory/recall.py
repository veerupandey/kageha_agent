"""recall operations for MemoryService."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, TypeVar

from kageha.memory.models import (
    EpisodeRecord,
    MemoryContext,
    MemoryKind,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
    MemorySensitivity,
    MemoryState,
    RecallItem,
    RecallTrace,
    _one_line,
)
from kageha.memory.security import inspect_memory_text
from kageha.memory.util import (
    _SCOPE_AUTHORITY,
    _SUCCESS,
    _fts_query,
    _tokens,
    memory_enabled,
    project_key,
)

RecallBoundT = TypeVar("RecallBoundT", MemoryRecord, EpisodeRecord)


class MemoryRecallMixin:
    def _eligible(self, record: MemoryRecord, query: MemoryQuery) -> bool:
        if record.state != MemoryState.CONFIRMED.value:
            return False
        if record.expires_at is not None and record.expires_at <= time.time():
            return False
        if record.user_id != (query.user_id or "local"):
            return False
        if record.agent_id != (query.agent_id or "main"):
            return False
        if record.channel_key and record.channel_key != query.channel_key:
            return False
        if record.scope_type == MemoryScope.PROJECT.value:
            return record.project_key == project_key(query.project_root)
        if record.scope_type == MemoryScope.SESSION.value:
            return record.source_session_id == query.session_id
        if record.scope_type == MemoryScope.CHANNEL.value:
            return bool(query.channel_key) and record.channel_key == query.channel_key
        return True

    @staticmethod
    def _rrf_score(lex_rank: int | None, vec_rank: int | None) -> float:
        raw = 0.0
        if lex_rank is not None:
            raw += 1.0 / (60 + lex_rank)
        if vec_rank is not None:
            raw += 1.0 / (60 + vec_rank)
        return min(1.0, raw / (2.0 / 61.0))

    @staticmethod
    def _usage_multiplier(record: MemoryRecord, *, now: float | None = None) -> float:
        """Boost recently used claims; gently decay long-idle non-instructions."""
        ts = now if now is not None else time.time()
        protected = record.kind in {
            MemoryKind.INSTRUCTION.value,
            MemoryKind.PREFERENCE.value,
            MemoryKind.USER_FACT.value,
        }
        accessed = record.last_accessed
        if accessed is None:
            age_days = max(
                0.0, (ts - (record.updated_at or record.created_at)) / 86400.0
            )
            # Fresh writes must remain recallable; only age unused claims.
            if protected or age_days < 7:
                return 1.0
            if age_days > 30:
                return 0.85
            return 0.95
        days_since = max(0.0, (ts - accessed) / 86400.0)
        if days_since < 7:
            # Mild boost — must not overturn clear lexical/vector ranking.
            return 1.05
        if days_since < 30 or protected:
            return 1.0
        return max(0.75, 0.95 ** (days_since / 30.0))

    def recall(self, query: MemoryQuery) -> MemoryContext:
        if not memory_enabled() or not (query.query or "").strip():
            return MemoryContext()
        safe_query = inspect_memory_text(query.query).safe_text
        query_tokens = set(_tokens(safe_query))
        max_results = query.max_results or int(
            os.environ.get("KAGEHA_MEMORY_MAX_RESULTS", "6")
        )
        max_results = max(1, min(20, max_results))
        min_score = float(os.environ.get("KAGEHA_MEMORY_MIN_SCORE", "0.45"))
        trace_id = uuid.uuid4().hex
        candidates: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []

        # Hot path: only load standing user instructions/preferences, not all facts.
        standing = self.store.list_memories(
            state=MemoryState.CONFIRMED.value,
            user_id=query.user_id or "local",
            agent_id=query.agent_id or "main",
            kinds=(
                MemoryKind.INSTRUCTION.value,
                MemoryKind.PREFERENCE.value,
            ),
            source_role="user",
            limit=200,
        )
        instructions: list[RecallItem[MemoryRecord]] = []
        instruction_ids: set[str] = set()
        for rec in standing:
            if not self._eligible(rec, query):
                continue
            if rec.sensitivity in {
                MemorySensitivity.PROMPT_INJECTION.value,
                MemorySensitivity.SECRET.value,
            }:
                continue
            instructions.append(RecallItem(rec, 1.0, "confirmed_user"))
            instruction_ids.add(rec.id)
        instruction_groups: dict[str, list[RecallItem[MemoryRecord]]] = {}
        for item in instructions:
            instruction_groups.setdefault(item.record.claim_key, []).append(item)
        instructions = []
        for items in instruction_groups.values():
            highest = max(
                _SCOPE_AUTHORITY.get(item.record.scope_type, 0)
                for item in items
            )
            authoritative = [
                item
                for item in items
                if _SCOPE_AUTHORITY.get(item.record.scope_type, 0) == highest
            ]
            if len({item.record.content_hash for item in authoritative}) > 1:
                excluded.extend(
                    {
                        "id": item.record.id,
                        "reason": "unresolved_instruction_conflict",
                    }
                    for item in authoritative
                )
                continue
            authoritative.sort(
                key=lambda item: item.record.updated_at,
                reverse=True,
            )
            instructions.append(authoritative[0])
            excluded.extend(
                {
                    "id": item.record.id,
                    "reason": "lower_or_older_instruction_claim",
                }
                for item in items
                if item.record.id != authoritative[0].record.id
            )
        instructions = self._bounded(instructions, max_chars=1600)[:max_results]

        fts = _fts_query(safe_query)
        lexical = self.store.search_memories_fts(fts, limit=40) if fts else []
        lexical = [rec for rec in lexical if self._eligible(rec, query)]
        lex_rank = {rec.id: rank for rank, rec in enumerate(lexical, start=1)}

        vector_hits = self.vector.search(safe_query, top_k=24)
        vec_rank: dict[str, int] = {}
        vec_similarity: dict[str, float] = {}
        for rank, hit in enumerate(vector_hits, start=1):
            score = float(hit.get("score") or 0.0)
            if score < 0.65:
                excluded.append(
                    {
                        "id": hit.get("memory_id"),
                        "reason": "vector_similarity_below_0.65",
                        "score": score,
                    }
                )
                continue
            memory_id = str(hit.get("memory_id") or "")
            rec = self.store.get_memory(memory_id)
            if rec is None or not self._eligible(rec, query):
                continue
            vec_rank[memory_id] = rank
            vec_similarity[memory_id] = score

        ids = list(dict.fromkeys([*lex_rank, *vec_rank]))
        ranked: list[RecallItem[MemoryRecord]] = []
        for memory_id in ids:
            rec = self.store.get_memory(memory_id)
            if rec is None:
                continue
            score = self._rrf_score(lex_rank.get(memory_id), vec_rank.get(memory_id))
            sources = []
            if memory_id in lex_rank:
                sources.append("fts5")
            if memory_id in vec_rank:
                sources.append("vector")
            lexical_quality = 0.0
            if memory_id in lex_rank and query_tokens:
                lexical_quality = len(
                    query_tokens & set(_tokens(rec.content))
                ) / max(1, min(len(query_tokens), 4))
            if memory_id in lex_rank and memory_id not in vec_rank:
                # Prefer rows that share distinctive query tokens over shared
                # boilerplate matches, without dropping near-threshold FTS hits.
                score *= 0.65 + (0.35 * min(1.0, lexical_quality))
            usage = self._usage_multiplier(rec)
            score = min(1.0, score * usage)
            candidates.append(
                {
                    "id": memory_id,
                    "kind": rec.kind,
                    "score": score,
                    "sources": sources,
                    "vector_similarity": vec_similarity.get(memory_id),
                    "lexical_quality": lexical_quality,
                    "usage_multiplier": usage,
                }
            )
            if score < min_score:
                excluded.append(
                    {"id": memory_id, "reason": "score_below_threshold", "score": score}
                )
                continue
            if memory_id in instruction_ids:
                excluded.append({"id": memory_id, "reason": "already_in_user_memory"})
                continue
            ranked.append(RecallItem(rec, score, "+".join(sources)))

        # Group by claim authority. Lower-authority duplicates are excluded,
        # newest equivalent claims collapse to one, and equal-authority
        # contradictions are suppressed.
        by_claim: dict[str, list[RecallItem[MemoryRecord]]] = {}
        for item in ranked:
            rec = item.record
            by_claim.setdefault(rec.claim_key, []).append(item)
        grouped: list[RecallItem[MemoryRecord]] = []
        for items in by_claim.values():
            highest = max(
                _SCOPE_AUTHORITY.get(item.record.scope_type, 0)
                for item in items
            )
            authoritative = [
                item
                for item in items
                if _SCOPE_AUTHORITY.get(item.record.scope_type, 0) == highest
            ]
            lower = [item for item in items if item not in authoritative]
            excluded.extend(
                {
                    "id": item.record.id,
                    "reason": "lower_authority_claim",
                }
                for item in lower
            )
            contents = {item.record.content_hash for item in authoritative}
            if len(contents) > 1:
                excluded.extend(
                    {
                        "id": item.record.id,
                        "reason": "unresolved_claim_conflict",
                    }
                    for item in authoritative
                )
                continue
            authoritative.sort(
                key=lambda item: item.record.updated_at,
                reverse=True,
            )
            grouped.append(authoritative[0])
            excluded.extend(
                {
                    "id": item.record.id,
                    "reason": "equivalent_older_claim",
                }
                for item in authoritative[1:]
            )
        ranked = grouped

        ranked.sort(key=lambda item: (item.score, item.record.updated_at), reverse=True)
        remaining = max(0, max_results - len(instructions))
        project_items = self._bounded(ranked[:remaining], max_chars=2000)

        episodes: list[RecallItem[EpisodeRecord]] = []
        remaining = max(0, max_results - len(instructions) - len(project_items))
        if fts and remaining:
            for rank, episode in enumerate(
                self.store.search_episodes_fts(fts, limit=max_results * 4),
                start=1,
            ):
                if episode.status not in _SUCCESS or not episode.verified:
                    excluded.append(
                        {"id": episode.id, "reason": "episode_not_verified_success"}
                    )
                    continue
                if episode.user_id != (query.user_id or "local"):
                    continue
                if episode.agent_id != (query.agent_id or "main"):
                    continue
                if episode.channel_key and episode.channel_key != query.channel_key:
                    continue
                if episode.project_key != project_key(query.project_root):
                    continue
                age_days = max(0.0, (time.time() - episode.created_at) / 86400.0)
                overlap = (
                    len(
                        query_tokens
                        & set(_tokens(f"{episode.task} {episode.summary}"))
                    )
                    / max(1, min(len(query_tokens), 4))
                    if query_tokens
                    else 0.0
                )
                score = (
                    (1.0 / rank)
                    * min(1.0, overlap)
                    * (0.5 ** (age_days / 30.0))
                )
                if score < min_score:
                    excluded.append(
                        {"id": episode.id, "reason": "episode_score_below_threshold", "score": score}
                    )
                    continue
                episodes.append(RecallItem(episode, score, "episodes_fts"))
                if len(episodes) >= remaining:
                    break
        episodes = self._bounded(episodes, max_chars=1200)

        context = MemoryContext(
            instructions=instructions,
            project=project_items,
            episodes=episodes,
            trace_id=trace_id,
        )
        self._attach_memory_index(
            context,
            query=query,
            shown_ids=instruction_ids
            | {item.record.id for item in project_items},
        )
        selected = [
            {
                "id": item.record.id,
                "score": item.score,
                "section": section,
            }
            for section, items in (
                ("instructions", instructions),
                ("project", project_items),
                ("episodes", episodes),
            )
            for item in items
        ]
        trace = RecallTrace(
            id=trace_id,
            query=safe_query,
            session_id=query.session_id,
            candidates=candidates,
            selected=selected,
            excluded=excluded,
            created_at=time.time(),
        )
        self.store.add_event(
            "recall",
            trace.to_dict(),
            session_id=query.session_id,
            event_id=trace_id,
        )
        self.store.touch(
            item.record.id
            for item in [*instructions, *project_items]
            if isinstance(item.record, MemoryRecord)
        )
        if query.trace_root:
            root = Path(query.trace_root) / "_memory"
            root.mkdir(parents=True, exist_ok=True)
            (root / f"recall_{trace_id}.json").write_text(
                json.dumps(trace.to_dict(), indent=2) + "\n",
                encoding="utf-8",
            )
            (root / "latest_recall").write_text(trace_id + "\n", encoding="utf-8")
        return context

    def _attach_memory_index(
        self,
        context: MemoryContext,
        *,
        query: MemoryQuery,
        shown_ids: set[str],
    ) -> None:
        """Claude-style compact index pointers for claims not already in the digest."""
        from kageha.config import kageha_home

        path = kageha_home() / "memory" / "MEMORY.md"
        context.index_path = str(path)
        pointers: list[str] = []
        rows = self.store.list_memories(
            state=MemoryState.CONFIRMED.value,
            user_id=query.user_id or "local",
            agent_id=query.agent_id or "main",
            limit=120,
        )
        rows.sort(key=lambda r: r.updated_at or r.created_at, reverse=True)
        for rec in rows:
            if rec.id in shown_ids:
                continue
            if not self._eligible(rec, query):
                continue
            if rec.sensitivity in {
                MemorySensitivity.PROMPT_INJECTION.value,
                MemorySensitivity.SECRET.value,
            }:
                continue
            # Prefer project facts / decisions in the overflow index.
            if rec.kind in {
                MemoryKind.INSTRUCTION.value,
                MemoryKind.PREFERENCE.value,
            } and rec.source_role == "user":
                # Standing instructions already prefer the digest; skip unless overflow.
                if len(context.instructions) < 10:
                    continue
            pointers.append(
                f"- [{rec.kind}] {_one_line(rec.content, 100)} [memory:{rec.id}]"
            )
            if len(pointers) >= 12:
                break
        context.index_pointers = pointers

    @staticmethod
    def _bounded(
        items: list[RecallItem[RecallBoundT]],
        *,
        max_chars: int,
    ) -> list[RecallItem[RecallBoundT]]:
        out: list[RecallItem[RecallBoundT]] = []
        used = 0
        for item in items:
            record = item.record
            text = (
                record.content
                if isinstance(record, MemoryRecord)
                else f"{record.task} {record.summary}"
            )
            size = len(text) + 80
            if out and used + size > max_chars:
                break
            out.append(item)
            used += size
        return out



    def inspect(
        self,
        *,
        state: str = "",
        scope_type: str = "",
        project_root: str = "",
        session_id: str = "",
        user_id: str = "local",
        agent_id: str = "main",
        channel_key: str = "",
        limit: int = 100,
    ) -> list[MemoryRecord]:
        return self.store.list_memories(
            state=state,
            scope_type=scope_type,
            user_id=user_id,
            agent_id=agent_id,
            project_key=project_key(project_root) if project_root else "",
            session_id=session_id,
            channel_key=channel_key,
            limit=limit,
        )



    def explain(self, trace_id: str) -> RecallTrace:
        event = self.store.get_event(trace_id)
        if not event or event["event_type"] != "recall":
            raise ValueError(f"recall trace not found: {trace_id}")
        payload = dict(event["payload"])
        return RecallTrace(
            id=payload.get("id") or trace_id,
            query=payload.get("query") or "",
            session_id=payload.get("session_id") or event.get("session_id") or "",
            candidates=list(payload.get("candidates") or []),
            selected=list(payload.get("selected") or []),
            excluded=list(payload.get("excluded") or []),
            created_at=float(payload.get("created_at") or event.get("created_at") or 0.0),
        )



    def latest_trace(self, *, session_id: str = "") -> RecallTrace | None:
        event = self.store.latest_event("recall", session_id=session_id)
        if not event:
            return None
        return self.explain(event["id"])



    def fetch(self, target: str) -> dict[str, Any]:
        """Deep-fetch one memory or episode by id (Claude topic-file style)."""
        target_s = (target or "").strip()
        # Accept digest markers: memory:<id> / episode:<id> / [memory:<id>]
        target_s = re.sub(
            r"^\[?(?:memory|episode):|#?",
            "",
            target_s,
            flags=re.IGNORECASE,
        ).rstrip("]")
        target_s = target_s.strip()
        if not target_s:
            raise ValueError("target id required")
        record = self.store.get_memory(target_s)
        if record is not None:
            if record.state == MemoryState.CONFIRMED.value:
                self.store.touch([record.id])
            return {"type": "memory", "record": record.to_dict()}
        episode = self.store.get_episode(target_s)
        if episode is not None:
            return {
                "type": "episode",
                "record": {
                    "id": episode.id,
                    "session_id": episode.session_id,
                    "turn_id": episode.turn_id,
                    "task": episode.task,
                    "summary": episode.summary,
                    "status": episode.status,
                    "verified": episode.verified,
                    "project_key": episode.project_key,
                    "created_at": episode.created_at,
                },
            }
        raise ValueError(f"memory/episode not found: {target_s}")



    def forgotten(self, *, limit: int = 30) -> list[dict[str, Any]]:
        """Explain recent automatic/manual forgetting (visible prune trail)."""
        out: list[dict[str, Any]] = []
        for event in self.store.list_events(limit=max(1, min(100, limit * 3))):
            et = event.get("event_type") or ""
            payload = dict(event.get("payload") or {})
            if et == "prune_idle":
                for item in payload.get("forgotten") or []:
                    out.append(
                        {
                            "when": event.get("created_at"),
                            "action": "prune",
                            "id": item.get("id"),
                            "reason": item.get("reason"),
                            "content": item.get("content"),
                        }
                    )
            elif et == "consolidate":
                for item in payload.get("details") or []:
                    if item.get("action") in {
                        "supersede_claim_duplicate",
                        "supersede_near_duplicate",
                        "expire_quarantine",
                    }:
                        out.append(
                            {
                                "when": event.get("created_at"),
                                "action": item.get("action"),
                                "id": item.get("dropped") or item.get("id"),
                                "reason": item.get("action"),
                                "kept": item.get("kept"),
                            }
                        )
            elif et == "correction" and payload.get("action") == "forget":
                out.append(
                    {
                        "when": event.get("created_at"),
                        "action": "forget",
                        "id": payload.get("memory_id"),
                        "reason": "user_forget",
                    }
                )
            if len(out) >= limit:
                break
        return out[:limit]



