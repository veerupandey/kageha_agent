"""Tests for plan-mode improvements: parallel stages, estimates, anti-loop exhaustion."""
from kageha.loop.task_state import (
    TaskState, PlanStage, StageStatus, FailureRecord, ClaimCertainty, Fact,
)


def test_parallel_stages_activate_together():
    """Stages with no inter-dependency should both go ACTIVE when deps are met."""
    ts = TaskState()
    ts.stages = [
        PlanStage("s1", "setup", status=StageStatus.DONE.value),
        PlanStage("s2", "fetch A", depends_on=["s1"]),
        PlanStage("s3", "fetch B", depends_on=["s1"]),  # parallel to s2
        PlanStage("s4", "merge", depends_on=["s2", "s3"]),
    ]
    ts.current_stage_id = "s1"
    ts.advance_stage()
    active = {s.id for s in ts.stages if s.status == StageStatus.ACTIVE.value}
    assert active == {"s2", "s3"}, f"expected s2+s3 active, got {active}"


def test_estimates_surface_in_projection():
    ts = TaskState()
    ts.stages = [
        PlanStage("s1", "render", estimated_steps=5, estimated_usd=0.30),
    ]
    proj = ts.projection()
    assert "~5 steps" in proj
    assert "~$0.30" in proj


def test_verified_facts_prioritized():
    ts = TaskState()
    for i in range(10):
        ts.facts.append(Fact(text=f"inferred-{i}", certainty="inferred"))
    ts.facts.append(Fact(text="VERIFIED-KEY", certainty="verified"))
    proj = ts.projection()
    assert "VERIFIED-KEY" in proj


def test_anti_loop_tool_exhaustion():
    """Same tool failing 3x with different causes should trigger anti-loop."""
    ts = TaskState()
    for i in range(3):
        ts.failures.append(FailureRecord(
            step=i, action="web_fetch", result="err", cause=f"cause_{i}", kind="tool_error",
        ))
    assert ts.anti_loop_hit("web_fetch", "cause_2") is True


def test_anti_loop_exact_still_works():
    ts = TaskState()
    ts.failures.append(FailureRecord(step=1, action="bash", result="e", cause="perm"))
    ts.failures.append(FailureRecord(step=2, action="bash", result="e", cause="perm"))
    assert ts.anti_loop_hit("bash", "perm") is True


def test_set_stages_from_plan_respects_explicit_deps():
    ts = TaskState()
    steps = [
        type("S", (), {"id": "a", "description": "do A", "tools": [],
                        "depends_on": [], "parallel_group": "", "estimated_steps": 0,
                        "estimated_usd": 0.0})(),
        type("S", (), {"id": "b", "description": "do B", "tools": [],
                        "depends_on": ["a"], "parallel_group": "g1", "estimated_steps": 3,
                        "estimated_usd": 0.1})(),
        type("S", (), {"id": "c", "description": "do C", "tools": [],
                        "depends_on": ["a"], "parallel_group": "g1", "estimated_steps": 0,
                        "estimated_usd": 0.0})(),
    ]
    ts.set_stages_from_plan(steps)
    assert ts.stages[1].parallel_group == "g1"
    assert ts.stages[1].estimated_steps == 3
    # explicit deps respected: b and c both depend on a -> parallel
    assert ts.stages[1].depends_on == ["a"]
    assert ts.stages[2].depends_on == ["a"]
