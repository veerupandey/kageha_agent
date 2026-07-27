"""Provenance-aware memory service facade (capture / recall / mutations)."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict
from typing import Any

from kageha.config import kageha_home
from kageha.memory.capture import MemoryCaptureMixin
from kageha.memory.models import IndexReport, MemoryState, TurnMemoryInput
from kageha.memory.mutations import MemoryMutationsMixin
from kageha.memory.recall import MemoryRecallMixin
from kageha.memory.security import inspect_memory_text
from kageha.memory.store import MemoryStore
from kageha.memory.util import (
    _llm_extract_mode,
    memory_enabled,
    memory_learning_enabled,
    private_channel_key,
    project_key,
    set_runtime_memory_setting,
    turn_memory_input_from_result,
)
from kageha.memory.vector import MemoryVectorIndex, embedding_model_defined

# Re-export helpers used across the codebase.
__all__ = [
    "MemoryService",
    "get_memory_service",
    "memory_enabled",
    "memory_learning_enabled",
    "private_channel_key",
    "project_key",
    "reset_memory_service_for_tests",
    "set_runtime_memory_setting",
    "turn_memory_input_from_result",
]

_SERVICE: MemoryService | None = None
_SERVICE_LOCK = threading.Lock()


class MemoryService(MemoryCaptureMixin, MemoryRecallMixin, MemoryMutationsMixin):
    def __init__(
        self,
        store: MemoryStore | None = None,
        *,
        vector_index: MemoryVectorIndex | None = None,
        start_worker: bool = False,
    ) -> None:
        self.store = store or MemoryStore()
        self.vector = vector_index or MemoryVectorIndex()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._worker_lock = threading.Lock()
        if start_worker:
            self.start_worker()

    def start_worker(self) -> None:
        if not memory_enabled() or not memory_learning_enabled():
            return
        with self._worker_lock:
            if self._worker and self._worker.is_alive():
                return
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="kageha-memory-worker",
                daemon=True,
            )
            self._worker.start()

    def stop_worker(self, *, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=max(0.0, timeout))

    def _worker_loop(self) -> None:
        idle_ticks = 0
        while not self._stop.is_set():
            processed = self._process_one_job()
            if processed:
                idle_ticks = 0
                continue
            idle_ticks += 1
            # Soft-forget + consolidate on a slow cadence.
            if idle_ticks >= 40:
                try:
                    self.prune_idle()
                except Exception:
                    pass
                try:
                    self.consolidate(force=False)
                except Exception:
                    pass
                idle_ticks = 0
            self._stop.wait(0.25)

    def drain_jobs(self, *, max_seconds: float = 2.0) -> int:
        # Keep drain hot-path job-only. Prune/consolidate run on the idle worker.
        deadline = time.monotonic() + max(0.0, max_seconds)
        count = 0
        while time.monotonic() <= deadline:
            if not self._process_one_job():
                break
            count += 1
        return count

    def _process_one_job(self) -> bool:
        job = self.store.claim_job()
        if job is None:
            return False
        try:
            payload = TurnMemoryInput(**job["payload"])
            self._process_capture(payload)
            self.store.finish_job(job["id"])
            return True
        except Exception as exc:  # noqa: BLE001
            safe_error = inspect_memory_text(str(exc)).safe_text
            terminal = int(job.get("attempts") or 1) >= 3
            self.store.fail_job(job["id"], safe_error, terminal=terminal)
            self.store.add_event(
                "job_failure",
                {
                    "job_id": job["id"],
                    "attempts": job.get("attempts"),
                    "terminal": terminal,
                    "error": safe_error[:500],
                },
                session_id=job.get("session_id") or "",
            )
            return False

    def rebuild_index(self) -> IndexReport:
        lexical = self.store.rebuild_fts()
        confirmed = self.store.list_memories(
            state=MemoryState.CONFIRMED.value,
            limit=10000,
        )
        vector_count = self.vector.rebuild(confirmed)
        report = IndexReport(
            ok=True,
            lexical_records=lexical,
            vector_records=vector_count,
            engine=f"fts5+{self.vector.engine}" if self.vector.enabled else "fts5",
        )
        self.store.add_event("index_health", asdict(report))
        return report

    def status(self) -> dict[str, Any]:
        data = self.store.stats()
        data.update(
            {
                "enabled": memory_enabled(),
                "learning_enabled": memory_learning_enabled(),
                "vector_mode": self.vector.mode,
                "vector_mode_requested": getattr(
                    self.vector, "requested_mode", self.vector.mode
                ),
                "vector_engine": self.vector.engine if self.vector.enabled else "off",
                "embedding_model_defined": embedding_model_defined(),
                "semantic_accelerator": (
                    f"fts5+{self.vector.engine}" if self.vector.enabled else "fts5"
                ),
                "candidate_ttl_days": float(
                    os.environ.get("KAGEHA_MEMORY_CANDIDATE_TTL_DAYS", "14")
                ),
                "idle_ttl_days": float(
                    os.environ.get("KAGEHA_MEMORY_IDLE_TTL_DAYS", "90")
                ),
                "llm_extract": _llm_extract_mode(),
                "model_role": os.environ.get(
                    "KAGEHA_MEMORY_MODEL_ROLE", "fast_worker"
                ),
                "memory_digest": str(kageha_home() / "memory" / "MEMORY.md"),
            }
        )
        return data

    def export_markdown(self) -> str:
        return self.store.export_markdown()

    def consolidate(self, *, force: bool = False) -> dict[str, Any]:
        """Dedupe, age quarantine, and refresh Claude-style MEMORY.md digest."""
        from kageha.memory.consolidate import consolidate_store

        if not memory_enabled():
            return {"skipped": True, "reason": "memory_disabled"}
        report = consolidate_store(self.store, force=force)
        return report.to_dict()



def get_memory_service(*, start_worker: bool = False) -> MemoryService:
    global _SERVICE
    with _SERVICE_LOCK:
        from kageha.config import kageha_home

        if _SERVICE is None or _SERVICE.store.home != kageha_home().resolve():
            if _SERVICE is not None:
                _SERVICE.stop_worker(timeout=0.2)
            _SERVICE = MemoryService(start_worker=start_worker)
        elif start_worker:
            _SERVICE.start_worker()
        return _SERVICE


def reset_memory_service_for_tests() -> None:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is not None:
            _SERVICE.stop_worker(timeout=0.2)
        _SERVICE = None



