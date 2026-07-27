"""Followup Normal Q&A must pass on a chat answer without tools (no Defect crash)."""

from __future__ import annotations

from kageha.loop.task_state import Defect
from kageha.loop.controller import _is_user_facing_reply


def test_defect_requires_artifact_and_accepts_repair_fields():
    d = Defect(
        artifact="deliverable",
        severity="major",
        problem="Claimed done without tool use or new deliverables",
        evidence="All good",
        repair="Call write_file",
    )
    assert d.artifact == "deliverable"


def test_chat_answer_counts_as_user_facing():
    assert _is_user_facing_reply("All good — I'm here and ready.")
    assert _is_user_facing_reply("2 + 2 equals 4.")
    assert not _is_user_facing_reply("Goals validated")
    assert not _is_user_facing_reply("init")
