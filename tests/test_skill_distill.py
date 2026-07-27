from types import SimpleNamespace

from kageha.memory.learning_loop import (
    maybe_prompt_skill_distill,
    proposal_from_run,
    should_distill,
)
from kageha.memory.skills import SkillRegistry


def test_should_distill_thresholds():
    assert should_distill(tool_calls=5, recovered_error=False, user_correction=False)
    assert not should_distill(tool_calls=4, recovered_error=False, user_correction=False)
    assert should_distill(tool_calls=1, recovered_error=True, user_correction=False)


def test_proposal_from_run_success_long():
    p = proposal_from_run(
        task="Build a pdf report",
        message="Wrote report.pdf",
        status="success",
        steps=6,
        verification_evidence="report.pdf exists",
    )
    assert p is not None
    assert "pdf" in p.name or "build" in p.name
    assert "Wrote report.pdf" in p.content


def test_proposal_from_run_skips_short():
    assert (
        proposal_from_run(
            task="hi",
            message="ok",
            status="success",
            steps=2,
        )
        is None
    )


def test_maybe_prompt_skips_noninteractive(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    monkeypatch.setenv("KAGEHA_DISTILL", "0")
    result = SimpleNamespace(
        run_id="r1",
        status="success",
        message="done",
        steps=8,
        recovered_failures=[],
        verification_evidence="ok",
        validated=True,
    )
    out = maybe_prompt_skill_distill(
        result, task="long task", registry=SkillRegistry(), interactive=True
    )
    assert out is None


def test_maybe_prompt_creates_on_yes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KAGEHA_HOME", str(home))
    monkeypatch.delenv("KAGEHA_DISTILL", raising=False)
    monkeypatch.setattr("kageha.memory.learning_loop.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "y")
    result = SimpleNamespace(
        run_id="r2",
        status="success",
        message="Built carousel",
        steps=7,
        recovered_failures=[],
        verification_evidence="3 png files",
        validated=True,
    )
    reg = SkillRegistry()
    out = maybe_prompt_skill_distill(result, task="Make carousel", registry=reg)
    assert out is not None
    assert "Created skill" in out
    assert any("carousel" in n or "make" in n for n in reg.skills)
