"""mutations operations for MemoryService."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from kageha.memory.models import (
    MemoryKind,
    MemoryMutation,
    MemoryRecord,
    MemoryScope,
    MemorySensitivity,
    MemoryState,
)
from kageha.memory.security import inspect_memory_text
from kageha.memory.util import (
    _CORRECT_TARGET_RE,
    _CORRECTION_RE,
    _FORGET_TARGET_RE,
    _REMEMBER_RE,
    _content_hash,
    _default_scope,
    _infer_kind,
    memory_enabled,
    project_key,
)


class MemoryMutationsMixin:
    def mutate(self, mutation: MemoryMutation) -> MemoryRecord:
        if not memory_enabled():
            raise RuntimeError("memory is disabled")
        action = (mutation.action or "").strip().lower()
        if action not in {"remember", "correct", "forget"}:
            raise ValueError("action must be remember|correct|forget")
        if action == "forget":
            matches = [
                record
                for record in self.store.find_memories(
                    mutation.target or mutation.content
                )
                if self._mutation_allowed(record, mutation)
            ]
            target = self._unique_target(matches, mutation.target or mutation.content)
            updated = self.store.update_state(target.id, MemoryState.RETRACTED.value)
            assert updated is not None
            self.store.add_event(
                "correction",
                {"action": "forget", "memory_id": target.id},
                session_id=mutation.session_id,
            )
            return updated

        security = inspect_memory_text(mutation.content)
        if security.blocked:
            self.store.add_event(
                "redaction",
                {"findings": security.findings, "action": "mutation_blocked"},
                session_id=mutation.session_id,
            )
            raise ValueError("memory contains a credential or secret and was not stored")
        kind = mutation.kind or _infer_kind(security.safe_text)
        scope = mutation.scope_type or _default_scope(kind)
        if kind not in {item.value for item in MemoryKind}:
            raise ValueError(f"unsupported memory kind: {kind}")
        if scope not in {item.value for item in MemoryScope}:
            raise ValueError(f"unsupported memory scope: {scope}")
        pkey = project_key(mutation.project_root)
        state = MemoryState.CONFIRMED.value
        if action == "remember" and (mutation.source_role or "user") != "user":
            state = MemoryState.CANDIDATE.value
        if security.sensitivity == MemorySensitivity.PROMPT_INJECTION.value:
            state = MemoryState.QUARANTINED.value

        old: MemoryRecord | None = None
        if action == "correct":
            old = self._unique_target(
                [
                    record
                    for record in self.store.find_memories(mutation.target)
                    if self._mutation_allowed(record, mutation)
                ],
                mutation.target,
            )
            kind = mutation.kind or old.kind
            scope = old.scope_type
            pkey = old.project_key
            if old.content_hash == _content_hash(security.safe_text):
                self.store.add_event(
                    "promotion_rejected",
                    {"reason": "correction_is_identical", "memory_id": old.id},
                    session_id=mutation.session_id,
                )
                return old

        record = self._record_for(
            content=security.safe_text,
            kind=kind,
            state=state,
            confidence=mutation.confidence,
            sensitivity=security.sensitivity,
            source_role=mutation.source_role or "user",
            session_id=mutation.session_id,
            turn_id="explicit",
            artifact="",
            evidence=mutation.verification_evidence or "explicit user memory mutation",
            scope_type=scope,
            user_id=mutation.user_id or "local",
            agent_id=mutation.agent_id or "main",
            pkey=pkey,
            channel_key=mutation.channel_key,
            supersedes_id=old.id if old else "",
        )
        if old:
            record.claim_key = old.claim_key
            stored = self.store.supersede(old.id, record)
        else:
            active = self.store.active_claims(
                record.claim_key,
                scope_type=record.scope_type,
                scope_key=record.scope_key,
            )
            if active and all(item.content_hash != record.content_hash for item in active):
                # An explicit user-authored memory has authority to replace the
                # previous value for the same claim.
                record.supersedes_id = active[0].id
                stored = self.store.supersede(active[0].id, record)
            else:
                stored = self.store.insert_memory(record)
        if stored.state == MemoryState.CONFIRMED.value:
            self.vector.index(stored)
        self.store.add_event(
            "correction" if old else "remember",
            {
                "memory_id": stored.id,
                "supersedes_id": stored.supersedes_id,
                "state": stored.state,
            },
            session_id=mutation.session_id,
        )
        return stored



    @staticmethod
    def _mutation_allowed(
        record: MemoryRecord,
        mutation: MemoryMutation,
    ) -> bool:
        if record.user_id != (mutation.user_id or "local"):
            return False
        if record.agent_id != (mutation.agent_id or "main"):
            return False
        if record.channel_key != (mutation.channel_key or ""):
            return False
        if record.scope_type == MemoryScope.PROJECT.value:
            return record.project_key == project_key(mutation.project_root)
        if record.scope_type == MemoryScope.SESSION.value:
            return record.source_session_id == mutation.session_id
        if record.scope_type == MemoryScope.CHANNEL.value:
            return bool(mutation.channel_key) and (
                record.channel_key == mutation.channel_key
            )
        return True



    @staticmethod
    def _unique_target(matches: list[MemoryRecord], target: str) -> MemoryRecord:
        if not matches:
            raise ValueError(f"no active memory matched {target!r}")
        if len(matches) > 1:
            ids = ", ".join(item.id for item in matches[:5])
            raise ValueError(f"memory target is ambiguous; use one id: {ids}")
        return matches[0]



    def import_project_rules(
        self,
        project_root: str,
        *,
        session_id: str = "",
        user_id: str = "local",
        agent_id: str = "main",
        channel_key: str = "",
        sync: bool = False,
    ) -> dict[str, Any]:
        """Import AGENTS.md / CLAUDE.md / .cursor/rules into project instructions."""
        from kageha.memory.import_rules import (
            collect_rule_chunks,
            discover_rule_files,
            rule_files_fingerprint,
        )

        if not memory_enabled():
            raise RuntimeError("memory is disabled")
        root = str(Path(project_root).expanduser().resolve())
        chunks = collect_rule_chunks(root)
        fingerprint = rule_files_fingerprint(root)
        existing_rows = self.store.list_memories(
            state=MemoryState.CONFIRMED.value,
            user_id=user_id or "local",
            agent_id=agent_id or "main",
            project_key=project_key(root),
            limit=1000,
        )
        existing = {rec.content_hash: rec for rec in existing_rows}
        imported: list[str] = []
        skipped = 0
        seen_hashes: set[str] = set()
        for chunk in chunks:
            content = (
                f"Standing project rule from {chunk.source}"
                + (f" ({chunk.title})" if chunk.title else "")
                + f": {chunk.content}"
            )
            digest = _content_hash(content)
            seen_hashes.add(digest)
            if digest in existing:
                skipped += 1
                continue
            record = self.mutate(
                MemoryMutation(
                    action="remember",
                    content=content,
                    kind=MemoryKind.INSTRUCTION.value,
                    scope_type=MemoryScope.PROJECT.value,
                    project_root=root,
                    session_id=session_id or "import-rules",
                    user_id=user_id or "local",
                    agent_id=agent_id or "main",
                    channel_key=channel_key,
                    source_role="user",
                    confidence=0.99,
                    verification_evidence=f"imported from {chunk.source}",
                )
            )
            existing[record.content_hash] = record
            imported.append(record.id)

        retracted = 0
        if sync:
            # Retract prior imports from this project that are no longer in files.
            for rec in existing_rows:
                if not rec.content.startswith("Standing project rule from "):
                    continue
                if rec.content_hash in seen_hashes:
                    continue
                self.store.update_state(rec.id, MemoryState.RETRACTED.value)
                retracted += 1

        mtimes = {
            str(path.relative_to(Path(root))): path.stat().st_mtime
            for path in discover_rule_files(root)
            if path.is_file()
        }
        report = {
            "project_root": root,
            "project_key": project_key(root),
            "fingerprint": fingerprint,
            "chunks_seen": len(chunks),
            "imported": len(imported),
            "skipped_duplicates": skipped,
            "retracted_stale": retracted,
            "synced": sync,
            "sources": mtimes,
            "memory_ids": imported,
        }
        self.store.add_event("import_rules", report, session_id=session_id)
        return report



    def apply_natural_correction(
        self,
        text: str,
        *,
        session_id: str,
        project_root: str = "",
        user_id: str = "local",
        agent_id: str = "main",
        channel_key: str = "",
    ) -> MemoryRecord | list[str] | None:
        """Retract one uniquely recalled memory for a deterministic correction."""
        if not _CORRECTION_RE.match(text or ""):
            return None
        trace = self.latest_trace(session_id=session_id)
        if trace is None:
            return None
        ids = [
            str(item.get("id") or "")
            for item in trace.selected
            if str(item.get("id") or "")
            and self.store.get_memory(str(item.get("id") or "")) is not None
        ]
        ids = list(dict.fromkeys(ids))
        if len(ids) > 1:
            for memory_id in ids:
                self.store.update_state(memory_id, MemoryState.QUARANTINED.value)
            self.store.add_event(
                "correction_ambiguous",
                {
                    "memory_ids": ids,
                    "reason": "multiple recalled claims matched the user's correction",
                },
                session_id=session_id,
            )
            return ids
        if not ids:
            return None
        return self.mutate(
            MemoryMutation(
                action="forget",
                target=ids[0],
                project_root=project_root,
                session_id=session_id,
                user_id=user_id,
                agent_id=agent_id,
                channel_key=channel_key,
            )
        )



    def apply_explicit_user_action(
        self,
        text: str,
        *,
        session_id: str,
        project_root: str = "",
        user_id: str = "local",
        agent_id: str = "main",
        channel_key: str = "",
    ) -> MemoryRecord | None:
        """Synchronously apply a standalone user-authored memory command."""
        raw = (text or "").strip()
        remember = _REMEMBER_RE.match(raw)
        if remember:
            content = remember.group(1).strip()
            if re.search(
                r"(?i)\b(?:and|then)\s+(?:create|make|build|run|open|browse|research|"
                r"write|edit|deploy|send)\b",
                content,
            ):
                return None
            return self.mutate(
                MemoryMutation(
                    action="remember",
                    content=content,
                    project_root=project_root,
                    session_id=session_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    channel_key=channel_key,
                    source_role="user",
                    verification_evidence="explicit natural-language user action",
                )
            )
        correction = _CORRECT_TARGET_RE.match(raw)
        if correction:
            return self.mutate(
                MemoryMutation(
                    action="correct",
                    target=correction.group(1),
                    content=correction.group(2).strip(),
                    project_root=project_root,
                    session_id=session_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    channel_key=channel_key,
                    source_role="user",
                    verification_evidence="explicit natural-language user correction",
                )
            )
        forget = _FORGET_TARGET_RE.match(raw)
        if forget:
            return self.mutate(
                MemoryMutation(
                    action="forget",
                    target=forget.group(1).strip(),
                    project_root=project_root,
                    session_id=session_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    channel_key=channel_key,
                    source_role="user",
                )
            )
        return None



