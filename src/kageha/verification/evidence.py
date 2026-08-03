"""EvidenceRecord and Evidence_Ledger (REL-020) — immutable proof of criterion outcomes.

Types defined here first (used by RuntimeStore persistence in REL-013's
migration); EvidenceLedger orchestration (redaction, staleness) added in
REL-021/REL-022.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kageha.runtime.store import RuntimeStore


class EvidenceSource(str, Enum):
    COMMAND_OUTPUT = "command_output"
    ARTIFACT_DIGEST = "artifact_digest"
    BROWSER_PROBE = "browser_probe"
    COMPUTER_PROBE = "computer_probe"
    RESEARCH_RETRIEVAL = "research_retrieval"
    MODEL_JUDGMENT = "model_judgment"


class EvidenceCertainty(str, Enum):
    VERIFIED = "verified"  # reproducible probe confirms outcome
    PROBABLE = "probable"  # tool reported success, no independent probe
    UNVERIFIABLE = "unverifiable"
    STALE = "stale"  # from a prior turn, not re-confirmed this turn


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    session_id: str
    turn_id: str
    criterion_id: str
    source: EvidenceSource
    source_ref: str  # command string, URL, artifact path, probe id
    timestamp: float
    digest: str  # sha256 of bounded content
    certainty: EvidenceCertainty
    producer: str  # e.g. "PythonValidator", "command_tool", "browser_probe"
    tool_attempt_id: str = ""
    artifact_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    probe: str = ""  # reproducible command/check, when available

    @classmethod
    def new(
        cls,
        *,
        session_id: str,
        turn_id: str,
        criterion_id: str,
        source: EvidenceSource,
        source_ref: str,
        digest: str,
        certainty: EvidenceCertainty,
        producer: str,
        tool_attempt_id: str = "",
        artifact_path: str = "",
        metadata: dict[str, Any] | None = None,
        probe: str = "",
    ) -> "EvidenceRecord":
        return cls(
            id=uuid.uuid4().hex,
            session_id=session_id,
            turn_id=turn_id,
            criterion_id=criterion_id,
            source=source,
            source_ref=source_ref,
            timestamp=time.time(),
            digest=digest,
            certainty=certainty,
            producer=producer,
            tool_attempt_id=tool_attempt_id,
            artifact_path=artifact_path,
            metadata=metadata or {},
            probe=probe,
        )

    def redacted(self) -> "EvidenceRecord":
        """Return a copy with string fields passed through kageha.obs.events.redact().

        Reuses the existing secret-pattern/memory-security inspection already
        applied to events and stored JSON blobs — no new redaction logic
        (REL-020.4).
        """
        from kageha.obs.events import redact

        return EvidenceRecord(
            id=self.id,
            session_id=self.session_id,
            turn_id=self.turn_id,
            criterion_id=self.criterion_id,
            source=self.source,
            source_ref=str(redact(self.source_ref)),
            timestamp=self.timestamp,
            digest=self.digest,
            certainty=self.certainty,
            producer=self.producer,
            tool_attempt_id=self.tool_attempt_id,
            artifact_path=self.artifact_path,
            metadata=redact(dict(self.metadata)),
            probe=str(redact(self.probe)),
        )


class EvidenceLedger:
    """Immutable, redacted, append-only proof store (REL-020)."""

    def __init__(self, store: "RuntimeStore") -> None:
        self.store = store

    def append(self, record: EvidenceRecord) -> EvidenceRecord:
        """Redact, then INSERT. Duplicate ids are rejected, never overwritten."""
        redacted = record.redacted()
        self.store.append_evidence(redacted)
        return redacted

    def for_criterion(
        self, session_id: str, turn_id: str, criterion_id: str
    ) -> list[EvidenceRecord]:
        return self.store.evidence_for_criterion(session_id, turn_id, criterion_id)

    def for_turn(self, session_id: str, turn_id: str) -> list[EvidenceRecord]:
        return self.store.evidence_for_turn(session_id, turn_id)

    def with_staleness(
        self, records: list[EvidenceRecord], *, current_turn_id: str
    ) -> list[EvidenceRecord]:
        """Mark records from a different turn as STALE unless a fresh record
        in the current turn reconfirms them via metadata={"reconfirms": old_id}
        (REL-020.3, Property 19).
        """
        reconfirmed_ids = {
            str(r.metadata.get("reconfirms"))
            for r in records
            if r.turn_id == current_turn_id and r.metadata.get("reconfirms")
        }
        out: list[EvidenceRecord] = []
        for r in records:
            if r.turn_id != current_turn_id and r.id not in reconfirmed_ids:
                out.append(
                    EvidenceRecord(
                        id=r.id,
                        session_id=r.session_id,
                        turn_id=r.turn_id,
                        criterion_id=r.criterion_id,
                        source=r.source,
                        source_ref=r.source_ref,
                        timestamp=r.timestamp,
                        digest=r.digest,
                        certainty=EvidenceCertainty.STALE,
                        producer=r.producer,
                        tool_attempt_id=r.tool_attempt_id,
                        artifact_path=r.artifact_path,
                        metadata=r.metadata,
                        probe=r.probe,
                    )
                )
            else:
                out.append(r)
        return out


def render_evidence_text(records: list[EvidenceRecord]) -> str:
    """Pure projection of EvidenceRecord entries into the legacy string-evidence
    shape (Requirement 11.1, Property 23). RunResult.verification_evidence and
    TaskState tool-result notes are derived from EvidenceLedger.for_turn()
    exclusively via this function.
    """
    if not records:
        return ""
    parts: list[str] = []
    for r in records:
        bit = f"{r.producer}:{r.source.value}"
        if r.source_ref:
            bit += f"={r.source_ref[:120]}"
        if r.certainty != EvidenceCertainty.VERIFIED:
            bit += f" [{r.certainty.value}]"
        parts.append(bit)
    return "; ".join(parts)[:4000]

