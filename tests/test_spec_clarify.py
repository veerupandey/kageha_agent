"""Unit tests for Spec clarify phase helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from kageha.loop.spec_clarify import (
    SKIP_CONTINUE_LABEL,
    SPEC_DESIGN_PHASES,
    ClarifyProposal,
    heuristic_questions,
    open_questions_for_artifacts,
    parse_open_question_answers,
    render_requirements_markdown,
    run_spec_clarify_phase,
    task_needs_clarify,
    write_requirements_draft,
)


def test_spec_phases_include_clarify():
    assert SPEC_DESIGN_PHASES == (
        "requirements",
        "clarify",
        "plan",
        "skill_gaps",
        "build",
    )


def test_task_needs_clarify_heuristic():
    assert task_needs_clarify("Build a checkout flow for our shop")
    assert task_needs_clarify("Improve our auth system")
    assert not task_needs_clarify("Create hello.py that prints hello")
    assert not task_needs_clarify(
        "Write status.md with the word ready and confirm it exists"
    )


def test_heuristic_questions_concrete():
    qs = heuristic_questions("Build a checkout flow for our shop")
    assert len(qs) >= 1
    assert any("payment" in q.lower() or "stripe" in q.lower() for q in qs)


def test_open_questions_not_stub_when_answered():
    lines = open_questions_for_artifacts(
        questions=["Which payment provider?"],
        answers=["Stripe"],
        assumptions=[],
        skipped=False,
    )
    blob = "\n".join(lines)
    assert "Q: Which payment provider?" in blob
    assert "A: Stripe" in blob
    assert "None recorded" not in blob


def test_skip_label_when_unambiguous():
    lines = open_questions_for_artifacts(
        questions=[],
        answers=[],
        assumptions=["Proceed as stated"],
        skipped=True,
    )
    assert SKIP_CONTINUE_LABEL in lines[0]
    assert any("Assumption:" in x for x in lines)


def test_parse_and_render_roundtrip(tmp_path: Path):
    text = render_requirements_markdown(
        task="Build checkout",
        phase="clarify",
        open_questions=["Which payment provider?"],
        answers=["Stripe"],
    )
    write_requirements_draft(
        tmp_path,
        task="Build checkout",
        questions=["Which payment provider?"],
        answers=["Stripe"],
    )
    disk = (tmp_path / "requirements.md").read_text(encoding="utf-8")
    assert disk == text or "Stripe" in disk
    qs, ans = parse_open_question_answers(disk)
    assert qs
    assert any("payment" in q.lower() for q in qs)
    assert any("Stripe" in a for a in ans)


@pytest.mark.asyncio
async def test_run_clarify_skip_writes_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    async def fake_propose(task, **_k):
        return ClarifyProposal(
            questions=[],
            assumptions=["Use hello.py as stated"],
            skip=True,
            source="test",
        )

    monkeypatch.setattr(
        "kageha.loop.spec_clarify.propose_clarify", fake_propose
    )
    result = await run_spec_clarify_phase(
        task="Create hello.py that prints hello",
        workspace_root=tmp_path,
        auto_continue=True,
    )

    assert result.skipped
    assert SKIP_CONTINUE_LABEL in "\n".join(result.open_questions)
    assert (tmp_path / "requirements.md").is_file()
    assert (tmp_path / "clarify_status.json").is_file()
    assert "No questions — Continue" in (
        tmp_path / "requirements.md"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_run_clarify_interactive_records_design_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from kageha.harness.approvals import ApprovalGate, ApprovalRequest

    async def fake_propose(task, **_k):
        return ClarifyProposal(
            questions=["Which payment provider should checkout use?"],
            assumptions=[],
            skip=False,
            source="test",
        )

    async def approver(req: ApprovalRequest) -> bool:
        assert req.risk_class == "clarify"
        text = (tmp_path / "requirements.md").read_text(encoding="utf-8")
        text = text.replace(
            "- Q: Which payment provider should checkout use?",
            "- Q: Which payment provider should checkout use?\n  - A: Stripe",
        )
        (tmp_path / "requirements.md").write_text(text, encoding="utf-8")
        return True

    monkeypatch.setattr(
        "kageha.loop.spec_clarify.propose_clarify", fake_propose
    )
    gate = ApprovalGate(approver=approver, auto_approve=False)
    result = await run_spec_clarify_phase(
        task="Build a checkout flow for our shop",
        workspace_root=tmp_path,
        approvals=gate,
        auto_continue=False,
        defer_human_input=True,
    )

    assert not result.skipped
    assert result.interactive
    assert result.answers
    assert "Stripe" in result.answers[0]
    req = (tmp_path / "requirements.md").read_text(encoding="utf-8")
    assert "Stripe" in req
    assert "None recorded" not in req
