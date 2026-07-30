"""Stress tests for the 7 gap-closing modules implemented from research.

Covers:
- Spec pipeline (models, state machine, persistence)
- Adaptive context budget (allocation, scoring, selection)
- Verifier agent (deterministic checks)
- Hooks (extended events, loading, execution)
- Steering (parsing, registry, context resolution)
- Decision trace (recording, querying, export)
- Replay (timeline loading, rendering)
"""

from __future__ import annotations

import json
import random
import string
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kageha.context.adaptive_budget import (
    AdaptiveBudget,
    score_message_importance,
    select_messages_by_importance,
)
from kageha.loop.verifier_agent import (
    DeterministicCheck,
    DeterministicVerifyResult,
    check_files_exist,
    check_python_syntax,
    run_deterministic_verification,
)
from kageha.obs.decision_trace import (
    DecisionTracer,
    TraceCategory,
)
from kageha.project.hooks import (
    HOOK_EVENTS,
    HookRunner,
    HookSpec,
    _load_hooks_file,
    normalize_hook_event,
)
from kageha.project.steering import (
    SteeringRule,
    load_steering_registry,
    parse_steering_file,
)
from kageha.specs.models import (
    SpecStage,
    SpecState,
    SpecTask,
    load_spec_state,
    save_spec_state,
    spec_dir,
)



# ═══════════════════════════════════════════════════════════════════════
# SECTION 1: Spec Pipeline Stress Tests
# ═══════════════════════════════════════════════════════════════════════



class TestSpecStateStress:
    """Stress the spec state machine with rapid transitions and edge cases."""

    def test_full_lifecycle(self, tmp_path: Path):
        """Walk through the full requirements → complete lifecycle."""
        state = SpecState(name="auth-feature", prompt="Add OAuth2 login")
        assert state.current_stage == SpecStage.REQUIREMENTS

        # Approve and advance through all stages
        for expected_next in [SpecStage.DESIGN, SpecStage.TASKS, SpecStage.BUILD, SpecStage.COMPLETE]:
            state.gate_for(state.current_stage).approve("looks good")
            advanced = state.advance()
            assert advanced
            assert state.current_stage == expected_next

        # Can't advance past complete
        state.gate_for(state.current_stage).approve()
        assert not state.advance()

    def test_reject_does_not_advance(self):
        state = SpecState(name="test", prompt="test")
        state.gate_for(state.current_stage).reject("needs work")
        assert not state.can_advance()
        assert not state.advance()
        assert state.current_stage == SpecStage.REQUIREMENTS

    def test_revision_requested_does_not_advance(self):
        state = SpecState(name="test", prompt="test")
        state.gate_for(state.current_stage).request_revision("fix section 3")
        assert not state.can_advance()


    @given(st.text(min_size=1, max_size=200))
    @settings(max_examples=100)
    def test_spec_name_sanitization(self, name: str):
        """spec_dir must handle arbitrary unicode names."""
        root = Path("/tmp/test-specs")
        result = spec_dir(root, name)
        # Must produce a valid path component
        assert result.parent == root / ".kageha" / "specs"
        # Name part should only contain safe chars
        safe_part = result.name
        assert all(c.isalnum() or c in "-_" for c in safe_part)

    def test_persistence_roundtrip(self, tmp_path: Path):
        """Save and load state, verify perfect roundtrip."""
        state = SpecState(
            name="big-feature",
            prompt="Implement the entire payment system",
            current_stage=SpecStage.TASKS,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-07-29T00:00:00Z",
        )
        # Add many tasks
        for i in range(50):
            state.tasks.append(SpecTask(
                id=f"task-{i}",
                title=f"Task {i}",
                description=f"Description for task {i}" * 10,
                acceptance_criteria=[f"AC-{j}" for j in range(5)],
                depends_on=[f"task-{i-1}"] if i > 0 else [],
                estimated_complexity=random.choice(["low", "medium", "high"]),
                files_to_modify=[f"src/module_{i}.py"],
            ))
        # Add gates
        state.gate_for(SpecStage.REQUIREMENTS).approve("ok")
        state.gate_for(SpecStage.DESIGN).approve("ok")

        save_spec_state(tmp_path, state)
        loaded = load_spec_state(tmp_path, "big-feature")
        assert loaded is not None
        assert loaded.name == state.name
        assert loaded.current_stage == state.current_stage
        assert len(loaded.tasks) == 50
        assert loaded.tasks[49].depends_on == ["task-48"]


    def test_concurrent_save_load(self, tmp_path: Path):
        """Simulate rapid concurrent saves (no corruption)."""
        for i in range(100):
            state = SpecState(
                name="concurrent-spec",
                prompt=f"iteration {i}",
                current_stage=SpecStage.REQUIREMENTS,
            )
            save_spec_state(tmp_path, state)
        loaded = load_spec_state(tmp_path, "concurrent-spec")
        assert loaded is not None
        assert loaded.prompt == "iteration 99"

    def test_corrupted_state_file(self, tmp_path: Path):
        """Load from a corrupted JSON file returns None."""
        directory = tmp_path / ".kageha" / "specs" / "broken"
        directory.mkdir(parents=True)
        (directory / "state.json").write_text("NOT JSON AT ALL {{{", encoding="utf-8")
        result = load_spec_state(tmp_path, "broken")
        assert result is None

    def test_to_dict_from_dict_fuzz(self):
        """Round-trip through dict representation."""
        for _ in range(50):
            state = SpecState(
                name=f"fuzz-{''.join(random.choices(string.ascii_lowercase, k=8))}",
                prompt="test" * random.randint(1, 100),
                current_stage=random.choice(list(SpecStage)),
            )
            d = state.to_dict()
            restored = SpecState.from_dict(d)
            assert restored.name == state.name
            assert restored.current_stage == state.current_stage


# ═══════════════════════════════════════════════════════════════════════
# SECTION 2: Adaptive Budget Stress Tests
# ═══════════════════════════════════════════════════════════════════════




@dataclass
class FakeMessage:
    role: str = "user"
    content: str = ""


class TestAdaptiveBudgetStress:
    """Stress test budget allocation and message selection."""

    @given(
        complexity=st.floats(min_value=0.1, max_value=5.0, allow_nan=False),
        tool_count=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=200)
    def test_allocate_never_exceeds_total(self, complexity: float, tool_count: int):
        """Budget allocation must never exceed total_tokens."""
        budget = AdaptiveBudget(total_tokens=24000, complexity=complexity)
        alloc = budget.allocate(has_plan=True, has_tools=tool_count)
        total = alloc.system + alloc.tools + alloc.skills + alloc.kb + alloc.history + alloc.working
        # Should never exceed total budget
        assert total <= budget.total_tokens

    @given(model=st.sampled_from(["claude-sonnet-5", "gpt-4o", "gemini-2.0-flash", "o3-mini", "unknown-model"]))
    @settings(max_examples=50)
    def test_for_model_produces_valid_budget(self, model: str):
        """for_model() always produces a usable budget."""
        budget = AdaptiveBudget.for_model(model, complexity=1.5)
        assert budget.total_tokens >= 16000
        assert budget.total_tokens <= 60000
        alloc = budget.allocate(has_plan=False, has_tools=5)
        assert alloc.system >= budget.min_system
        assert alloc.history >= budget.min_history

    def test_high_complexity_boosts_history(self):
        """Complex tasks should get more history budget."""
        simple = AdaptiveBudget(total_tokens=24000, complexity=0.5)
        complex_ = AdaptiveBudget(total_tokens=24000, complexity=2.0)
        simple_alloc = simple.allocate(has_plan=False, has_tools=5)
        complex_alloc = complex_.allocate(has_plan=False, has_tools=5)
        assert complex_alloc.history > simple_alloc.history

    def test_plan_mode_boosts_working(self):
        """Plan mode should boost working notes budget."""
        no_plan = AdaptiveBudget(total_tokens=24000).allocate(has_plan=False)
        with_plan = AdaptiveBudget(total_tokens=24000).allocate(has_plan=True)
        assert with_plan.working > no_plan.working



class TestImportanceScoringStress:
    """Stress importance scoring with varied inputs."""

    @given(content=st.text(min_size=0, max_size=5000))
    @settings(max_examples=200)
    def test_score_always_in_range(self, content: str):
        """Score must always be between 0.0 and 1.0."""
        score = score_message_importance(content, role="assistant")
        assert 0.0 <= score <= 1.0

    def test_system_messages_always_max(self):
        for _ in range(100):
            content = "".join(random.choices(string.printable, k=200))
            score = score_message_importance(content, role="system")
            assert score == 1.0

    def test_user_messages_high_priority(self):
        for _ in range(100):
            content = "".join(random.choices(string.ascii_letters + " ", k=100))
            score = score_message_importance(content, role="user")
            assert score >= 0.5

    def test_high_signal_words_boost_score(self):
        high_signal = "There is a critical error that must be fixed immediately"
        low_signal = "okay sure thanks got it no problem"
        high_score = score_message_importance(high_signal, role="assistant")
        low_score = score_message_importance(low_signal, role="assistant")
        assert high_score > low_score

    def test_large_tool_result_penalized(self):
        short_result = "File written successfully"
        long_result = "listing directory contents:\n" + "file.py\n" * 500
        short_score = score_message_importance(short_result, is_tool_result=True)
        long_score = score_message_importance(long_result, is_tool_result=True)
        assert short_score >= long_score


class TestMessageSelectionStress:
    """Stress the message selection algorithm."""

    def test_select_with_many_messages(self):
        """Select from a large conversation preserving budget."""
        messages = [FakeMessage(role="user", content=f"msg {i} " * 50) for i in range(200)]
        selected = select_messages_by_importance(
            messages, max_tokens=5000, preserve_last_n=4
        )
        # Must include last 4
        assert selected[-4:] == messages[-4:]
        # Must be within budget (approximately)
        assert len(selected) < len(messages)

    def test_select_preserves_all_when_within_budget(self):
        """If messages fit in budget, keep all."""
        messages = [FakeMessage(role="user", content="short") for _ in range(5)]
        selected = select_messages_by_importance(
            messages, max_tokens=100000, preserve_last_n=2
        )
        assert len(selected) == 5

    def test_select_empty_list(self):
        result = select_messages_by_importance([], max_tokens=1000)
        assert result == []

    def test_select_single_message(self):
        msgs = [FakeMessage(role="user", content="hello")]
        result = select_messages_by_importance(msgs, max_tokens=1000, preserve_last_n=4)
        assert len(result) == 1

    @given(
        n_messages=st.integers(min_value=1, max_value=100),
        budget=st.integers(min_value=100, max_value=50000),
    )
    @settings(max_examples=50)
    def test_select_never_exceeds_input(self, n_messages: int, budget: int):
        """Never return more messages than provided."""
        msgs = [FakeMessage(role="user", content=f"m{i}") for i in range(n_messages)]
        result = select_messages_by_importance(msgs, max_tokens=budget, preserve_last_n=2)
        assert len(result) <= n_messages



# ═══════════════════════════════════════════════════════════════════════
# SECTION 3: Verifier Agent Stress Tests
# ═══════════════════════════════════════════════════════════════════════



class TestVerifierStress:
    """Stress the deterministic verifier with many files and edge cases."""

    @pytest.mark.asyncio
    async def test_check_many_files_exist(self, tmp_path: Path):
        """Verify 100 files at once."""
        files = []
        for i in range(100):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content {i}")
            files.append(f"file_{i}.txt")
        result = await check_files_exist(files, workspace=tmp_path)
        assert result.passed
        assert "100 files present" in result.evidence

    @pytest.mark.asyncio
    async def test_check_mixed_existing_missing(self, tmp_path: Path):
        """Some exist, some don't."""
        (tmp_path / "exists.py").write_text("x = 1")
        result = await check_files_exist(
            ["exists.py", "missing.py", "also_missing.py"],
            workspace=tmp_path,
        )
        assert not result.passed
        assert "missing.py" in result.error
        assert "also_missing.py" in result.error

    @pytest.mark.asyncio
    async def test_syntax_check_valid_python(self, tmp_path: Path):
        """Valid Python files should pass."""
        for i in range(20):
            (tmp_path / f"mod_{i}.py").write_text(
                f"def func_{i}(x: int) -> int:\n    return x + {i}\n"
            )
        files = [f"mod_{i}.py" for i in range(20)]
        result = await check_python_syntax(files, workspace=tmp_path)
        assert result.passed

    @pytest.mark.asyncio
    async def test_syntax_check_invalid_python(self, tmp_path: Path):
        """Invalid Python should be caught."""
        (tmp_path / "bad.py").write_text("def broken(\n    x = [1, 2,\n")
        result = await check_python_syntax(["bad.py"], workspace=tmp_path)
        assert not result.passed
        assert "bad.py" in result.error


    @pytest.mark.asyncio
    async def test_syntax_check_non_python_ignored(self, tmp_path: Path):
        """Non-.py files should be skipped."""
        (tmp_path / "data.json").write_text("{invalid")
        result = await check_python_syntax(["data.json"], workspace=tmp_path)
        assert result.passed  # Skipped

    @pytest.mark.asyncio
    async def test_full_verification_pipeline(self, tmp_path: Path):
        """Run all checks together."""
        (tmp_path / "main.py").write_text("print('hello')\n")
        (tmp_path / "helper.py").write_text("def add(a, b): return a + b\n")
        result = await run_deterministic_verification(
            workspace=tmp_path,
            expected_files=["main.py", "helper.py"],
            modified_files=["main.py", "helper.py"],
            run_tests=False,  # No test runner in tmp
            run_lint=False,   # No ruff in tmp context
        )
        assert result.all_passed
        assert len(result.checks) >= 2  # files_exist + python_syntax

    @pytest.mark.asyncio
    async def test_verification_with_missing_files(self, tmp_path: Path):
        """Verification fails fast on missing files."""
        result = await run_deterministic_verification(
            workspace=tmp_path,
            expected_files=["nonexistent.py"],
            modified_files=[],
            run_tests=False,
            run_lint=False,
        )
        assert not result.all_passed
        assert len(result.critical_failures) == 1

    def test_result_summary_formatting(self):
        """Verify summary renders cleanly."""
        result = DeterministicVerifyResult(checks=[
            DeterministicCheck(name="files", passed=True, evidence="All present"),
            DeterministicCheck(name="syntax", passed=False, error="SyntaxError in x.py"),
            DeterministicCheck(name="tests", passed=True, evidence="12 passed"),
        ])
        summary = result.summary()
        assert "[PASS] files" in summary
        assert "[FAIL] syntax" in summary
        assert "SyntaxError" in summary


# ═══════════════════════════════════════════════════════════════════════
# SECTION 4: Hooks Stress Tests
# ═══════════════════════════════════════════════════════════════════════




class TestHooksStress:
    """Stress the extended hook system."""

    def test_all_21_events_recognized(self):
        """All 21 declared events should normalize correctly."""
        assert len(HOOK_EVENTS) >= 21
        for event in HOOK_EVENTS:
            assert normalize_hook_event(event) == event

    @given(event_name=st.text(min_size=1, max_size=50))
    @settings(max_examples=100)
    def test_normalize_never_crashes(self, event_name: str):
        """normalize_hook_event should handle any input without exception."""
        result = normalize_hook_event(event_name)
        assert isinstance(result, str)

    def test_pascal_case_aliases(self):
        """PascalCase aliases should map to camelCase."""
        assert normalize_hook_event("PreToolUse") == "preToolUse"
        assert normalize_hook_event("PostFileCreate") == "postFileCreate"
        assert normalize_hook_event("SessionStart") == "sessionStart"
        assert normalize_hook_event("AgentStuck") == "agentStuck"
        assert normalize_hook_event("ContextOverflow") == "contextOverflow"

    def test_load_hooks_nested_format(self, tmp_path: Path):
        """Load hooks from nested dict format."""
        hooks_data = {
            "hooks": {
                "preToolUse": [
                    {"command": "echo pre-tool", "matcher": "shell"},
                    {"command": "echo all-tools"},
                ],
                "postFileCreate": [
                    {"command": "echo file created"}
                ],
            }
        }
        path = tmp_path / "hooks.json"
        path.write_text(json.dumps(hooks_data))
        hooks = _load_hooks_file(path)
        assert len(hooks) == 3
        pre_tool = [h for h in hooks if h.event == "preToolUse"]
        assert len(pre_tool) == 2
        assert pre_tool[0].matcher == "shell"

    def test_load_hooks_flat_format(self, tmp_path: Path):
        """Load hooks from flat list format."""
        hooks_data = [
            {"event": "sessionStart", "command": "echo started"},
            {"event": "preCommit", "command": "make lint"},
            {"event": "postCommit", "http": "http://localhost:9000/notify"},
        ]
        path = tmp_path / "hooks.json"
        path.write_text(json.dumps(hooks_data))
        hooks = _load_hooks_file(path)
        assert len(hooks) == 3

    def test_load_hooks_invalid_json(self, tmp_path: Path):
        """Corrupted file returns empty list."""
        path = tmp_path / "hooks.json"
        path.write_text("NOT JSON {{{")
        assert _load_hooks_file(path) == []


    def test_hook_runner_deny_static(self):
        """Static deny blocks without executing command."""
        runner = HookRunner(hooks=[
            HookSpec(event="preToolUse", deny_message="blocked!", matcher="rm"),
        ])
        result = runner.run("preToolUse", tool_name="rm -rf")
        assert not result.allowed
        assert "blocked" in result.message

    def test_hook_runner_matcher_mismatch(self):
        """Matcher that doesn't match allows through."""
        runner = HookRunner(hooks=[
            HookSpec(event="preToolUse", deny_message="blocked!", matcher="rm"),
        ])
        result = runner.run("preToolUse", tool_name="read_file")
        assert result.allowed

    def test_hook_runner_command_success(self, tmp_path: Path):
        """Command hook that exits 0 should allow."""
        runner = HookRunner(hooks=[
            HookSpec(event="postFileCreate", command="echo ok"),
        ], project_root=tmp_path)
        result = runner.run("postFileCreate", payload={"file": "test.py"})
        assert result.allowed
        assert result.ran == 1

    def test_hook_runner_command_blocks_exit2(self, tmp_path: Path):
        """Command hook that exits 2 should block."""
        runner = HookRunner(hooks=[
            HookSpec(event="beforeShell", command="exit 2"),
        ], project_root=tmp_path)
        result = runner.run("beforeShell", payload={"cmd": "rm -rf /"})
        assert not result.allowed

    def test_many_hooks_same_event(self, tmp_path: Path):
        """Multiple hooks on same event all execute."""
        runner = HookRunner(hooks=[
            HookSpec(event="postCommit", command="echo hook1"),
            HookSpec(event="postCommit", command="echo hook2"),
            HookSpec(event="postCommit", command="echo hook3"),
        ], project_root=tmp_path)
        result = runner.run("postCommit")
        assert result.ran == 3
        assert result.allowed

    def test_hook_runner_unknown_event_noop(self):
        """Unknown events are ignored."""
        runner = HookRunner(hooks=[
            HookSpec(event="preToolUse", command="echo x"),
        ])
        result = runner.run("nonExistentEvent")
        assert result.allowed
        assert result.ran == 0


# ═══════════════════════════════════════════════════════════════════════
# SECTION 5: Steering Stress Tests
# ═══════════════════════════════════════════════════════════════════════




class TestSteeringStress:
    """Stress the steering system with many rules and edge cases."""

    def _make_steering_dir(self, tmp_path: Path, rules: list[tuple[str, str]]) -> Path:
        steering_dir = tmp_path / ".kageha" / "steering"
        steering_dir.mkdir(parents=True)
        for name, content in rules:
            (steering_dir / f"{name}.md").write_text(content, encoding="utf-8")
        return tmp_path

    def test_parse_frontmatter(self, tmp_path: Path):
        """Parse all inclusion modes correctly."""
        content = (
            "---\n"
            "inclusion: fileMatch\n"
            "fileMatchPattern: '**/*.py'\n"
            "description: Python rules\n"
            "priority: 80\n"
            "---\n"
            "Always use type hints."
        )
        path = tmp_path / "python.md"
        path.write_text(content)
        rule = parse_steering_file(path)
        assert rule is not None
        assert rule.inclusion == "fileMatch"
        assert rule.file_match_pattern == "**/*.py"
        assert rule.description == "Python rules"
        assert rule.priority == 80
        assert "type hints" in rule.content

    def test_parse_no_frontmatter(self, tmp_path: Path):
        """File without frontmatter defaults to always."""
        path = tmp_path / "simple.md"
        path.write_text("Just raw content here")
        rule = parse_steering_file(path)
        assert rule is not None
        assert rule.inclusion == "always"
        assert rule.content == "Just raw content here"

    def test_parse_invalid_frontmatter(self, tmp_path: Path):
        """Invalid YAML frontmatter doesn't crash."""
        path = tmp_path / "broken.md"
        path.write_text("---\n{not: [valid: yaml\n---\nContent here")
        rule = parse_steering_file(path)
        assert rule is not None  # Should still parse, just defaults

    def test_registry_with_50_rules(self, tmp_path: Path):
        """Load and query a large registry."""
        rules = []
        for i in range(50):
            mode = ["always", "fileMatch", "manual"][i % 3]
            content = f"---\ninclusion: {mode}\n"
            if mode == "fileMatch":
                content += f"fileMatchPattern: '**/*_{i}.py'\n"
            content += f"priority: {i}\n---\nRule {i} content."
            rules.append((f"rule_{i:02d}", content))
        root = self._make_steering_dir(tmp_path, rules)
        registry = load_steering_registry(project_root=root)
        assert len(registry.rules) == 50
        assert len(registry.always_rules()) > 0
        assert len(registry.manual_rules()) > 0


    def test_file_match_glob_patterns(self):
        """Various glob patterns should match correctly."""
        rule = SteeringRule(
            name="python",
            path=Path("x.md"),
            inclusion="fileMatch",
            file_match_pattern="**/*.py",
        )
        assert rule.matches_file("src/main.py")
        assert rule.matches_file("tests/test_foo.py")
        assert not rule.matches_file("src/main.js")
        assert not rule.matches_file("README.md")

    def test_resolve_context_all_modes(self, tmp_path: Path):
        """Resolve context including all three modes."""
        rules = [
            ("always-rule", "---\ninclusion: always\npriority: 90\n---\nAlways included."),
            ("python-rule", "---\ninclusion: fileMatch\nfileMatchPattern: '**/*.py'\npriority: 50\n---\nPython specific."),
            ("manual-rule", "---\ninclusion: manual\n---\nManual content."),
        ]
        root = self._make_steering_dir(tmp_path, rules)
        registry = load_steering_registry(project_root=root)

        # With just always
        ctx = registry.resolve_context()
        assert "Always included" in ctx
        assert "Python specific" not in ctx
        assert "Manual content" not in ctx

        # With file match
        ctx = registry.resolve_context(active_files=["src/foo.py"])
        assert "Always included" in ctx
        assert "Python specific" in ctx

        # With manual
        ctx = registry.resolve_context(manual_names=["manual-rule"])
        assert "Manual content" in ctx

    def test_catalog_rendering(self, tmp_path: Path):
        """Catalog should list all rules."""
        rules = [
            ("coding", "---\ninclusion: always\ndescription: Coding standards\n---\nContent"),
            ("testing", "---\ninclusion: manual\ndescription: Test guidelines\n---\nContent"),
        ]
        root = self._make_steering_dir(tmp_path, rules)
        registry = load_steering_registry(project_root=root)
        catalog = registry.catalog()
        assert "coding" in catalog
        assert "testing" in catalog
        assert "[always]" in catalog
        assert "[manual]" in catalog

    @given(filepath=st.text(min_size=1, max_size=100))
    @settings(max_examples=100)
    def test_matches_file_never_crashes(self, filepath: str):
        """matches_file should handle any input."""
        rule = SteeringRule(
            name="test",
            path=Path("x.md"),
            inclusion="fileMatch",
            file_match_pattern="**/*.py",
        )
        # Should not raise
        result = rule.matches_file(filepath)
        assert isinstance(result, bool)



# ═══════════════════════════════════════════════════════════════════════
# SECTION 6: Decision Trace Stress Tests
# ═══════════════════════════════════════════════════════════════════════



class TestDecisionTraceStress:
    """Stress the decision trace system."""

    def test_record_2000_entries(self):
        """Record max entries and verify circular buffer behavior."""
        tracer = DecisionTracer(max_entries=2000)
        for i in range(3000):
            tracer.trace_model_selection(
                chosen=f"model-{i % 5}",
                role="coding",
                reasoning=f"Reason {i}",
                alternatives=[f"alt-{j}" for j in range(3)],
            )
        # Should cap at max_entries
        assert len(tracer.entries) == 2000
        # Most recent should be the last recorded
        assert "2999" in tracer.entries[-1].reasoning

    def test_all_trace_methods(self):
        """Every trace method should work without error."""
        tracer = DecisionTracer()
        tracer.set_step(1)
        tracer.set_task_id("task-001")

        tracer.trace_model_selection(chosen="gpt-4", role="coding", reasoning="best for code")
        tracer.trace_adaptive_control(decision="REPAIR", reasoning="test failed")
        tracer.trace_verifier(verdict="pass", reasoning="all checks green", evidence="3/3 passed")
        tracer.trace_tool_dispatch(tool_name="shell", decision="allowed", reasoning="safe command")
        tracer.trace_stop_rule(rule="max_steps", triggered=False, reasoning="at step 5/40")
        tracer.trace_hook(event="preToolUse", hook_name="lint", decision="allowed", reasoning="passed")
        tracer.trace_permission(tool_name="rm", decision="blocked", reasoning="destructive")
        tracer.trace_context(decision="truncated history", reasoning="over budget")
        tracer.trace_plan(decision="replan", reasoning="stuck on step 3")
        tracer.trace_spec(decision="advance to design", reasoning="requirements approved", spec_name="auth")

        assert len(tracer.entries) == 10
        # All should have step=1 and task_id set
        for entry in tracer.entries:
            assert entry.step == 1
            assert entry.task_id == "task-001"

    def test_query_by_category(self):
        """Filter entries by category."""
        tracer = DecisionTracer()
        for i in range(100):
            if i % 3 == 0:
                tracer.trace_model_selection(chosen="m", role="r", reasoning="x")
            elif i % 3 == 1:
                tracer.trace_adaptive_control(decision="REPAIR", reasoning="x")
            else:
                tracer.trace_verifier(verdict="pass", reasoning="x")

        models = tracer.by_category(TraceCategory.MODEL_SELECTION)
        assert len(models) == 34  # ceil(100/3)
        adaptive = tracer.by_category(TraceCategory.ADAPTIVE_CONTROL)
        assert len(adaptive) == 33

    def test_query_by_step(self):
        """Filter entries by step number."""
        tracer = DecisionTracer()
        for step in range(10):
            tracer.set_step(step)
            tracer.trace_model_selection(chosen="m", role="r", reasoning=f"step {step}")
            tracer.trace_tool_dispatch(tool_name="t", decision="ok", reasoning="x")

        step_5 = tracer.by_step(5)
        assert len(step_5) == 2


    def test_export_jsonl(self, tmp_path: Path):
        """Export to JSONL and verify it's parseable."""
        tracer = DecisionTracer()
        for i in range(500):
            tracer.trace_model_selection(
                chosen=f"model-{i}",
                role="coding",
                reasoning=f"reason-{i}",
            )
        out_path = tmp_path / "trace.jsonl"
        count = tracer.export_jsonl(out_path)
        assert count == 500

        # Verify each line is valid JSON
        lines = out_path.read_text().strip().split("\n")
        assert len(lines) == 500
        for line in lines:
            parsed = json.loads(line)
            assert "category" in parsed
            assert "decision" in parsed
            assert "reasoning" in parsed

    def test_summary_rendering(self):
        """Summary should render last N entries compactly."""
        tracer = DecisionTracer()
        for i in range(20):
            tracer.trace_model_selection(chosen=f"m-{i}", role="r", reasoning=f"r-{i}")
        summary = tracer.summary(last_n=5)
        assert "Decision Trace" in summary
        assert "m-19" in summary  # Most recent
        assert "m-14" not in summary  # Older than last 5

    def test_clear(self):
        """Clear empties all entries."""
        tracer = DecisionTracer()
        for i in range(100):
            tracer.trace_plan(decision="x", reasoning="y")
        assert len(tracer.entries) == 100
        tracer.clear()
        assert len(tracer.entries) == 0

    def test_sink_integration(self):
        """Sink receives every recorded entry."""
        received = []

        class FakeSink:
            def append(self, kind: str, data: dict):
                received.append((kind, data))

        tracer = DecisionTracer()
        tracer.set_sink(FakeSink())
        for i in range(50):
            tracer.trace_context(decision=f"d-{i}", reasoning="r")
        assert len(received) == 50
        assert all(kind == "decision_trace" for kind, _ in received)

    @given(
        n=st.integers(min_value=1, max_value=500),
        max_entries=st.integers(min_value=10, max_value=2000),
    )
    @settings(max_examples=30)
    def test_max_entries_cap(self, n: int, max_entries: int):
        """Buffer never exceeds max_entries."""
        tracer = DecisionTracer(max_entries=max_entries)
        for i in range(n):
            tracer.trace_plan(decision=f"d-{i}", reasoning="r")
        assert len(tracer.entries) <= max_entries


# ═══════════════════════════════════════════════════════════════════════
# SECTION 7: Cross-Module Integration Stress
# ═══════════════════════════════════════════════════════════════════════


class TestCrossModuleIntegration:
    """Test interactions between the new modules."""

    def test_decision_trace_with_verifier_result(self):
        """Decision trace records verifier outcomes correctly."""
        tracer = DecisionTracer()
        result = DeterministicVerifyResult(checks=[
            DeterministicCheck(name="files", passed=True, evidence="ok"),
            DeterministicCheck(name="syntax", passed=False, error="SyntaxError"),
        ])
        tracer.trace_verifier(
            verdict="fail" if not result.all_passed else "pass",
            reasoning=f"{len(result.critical_failures)} check(s) failed",
            evidence=result.summary(),
            defects=[c.error for c in result.critical_failures],
        )
        entry = tracer.entries[0]
        assert "fail" in entry.decision.lower()
        assert "SyntaxError" in entry.context["evidence"]


    def test_adaptive_budget_with_steering(self, tmp_path: Path):
        """Budget allocation considers active files from steering."""
        budget = AdaptiveBudget.for_model("claude-sonnet-5", complexity=1.5)
        budget.active_files = ["src/auth.py", "src/login.py"]
        alloc = budget.allocate(has_plan=True, has_tools=10)
        # Should produce valid allocation
        assert alloc.system >= budget.min_system
        assert alloc.working > budget.min_working  # boosted by has_plan

    def test_spec_with_hooks_lifecycle(self, tmp_path: Path):
        """Spec stage transitions can trigger hooks."""
        from kageha.specs.models import SpecState
        from kageha.project.hooks import HookRunner, HookSpec

        state = SpecState(name="test-feature", prompt="test")
        runner = HookRunner(hooks=[
            HookSpec(event="specStageComplete", command="echo stage done"),
        ], project_root=tmp_path)

        # Approve and advance
        state.gate_for(state.current_stage).approve()
        state.advance()

        # Simulate hook trigger
        result = runner.run("specStageComplete", payload={
            "spec": state.name,
            "stage": state.current_stage.value,
        })
        assert result.allowed
        assert result.ran == 1

    def test_steering_with_spec_files(self, tmp_path: Path):
        """Steering fileMatch activates for spec artifacts."""
        steering_dir = tmp_path / ".kageha" / "steering"
        steering_dir.mkdir(parents=True)
        (steering_dir / "spec-rules.md").write_text(
            "---\n"
            "inclusion: fileMatch\n"
            "fileMatchPattern: '**/*.md'\n"
            "---\n"
            "When working on specs, follow the design-first approach."
        )
        registry = load_steering_registry(project_root=tmp_path)
        ctx = registry.resolve_context(
            active_files=["requirements.md", "design.md"]
        )
        assert "design-first" in ctx

    def test_replay_timeline_from_decision_trace(self, tmp_path: Path):
        """Decision trace entries can be loaded as replay events."""
        from kageha.obs.replay import load_timeline_from_events

        # Write events that include decision_trace entries
        session_dir = tmp_path / "session-001"
        session_dir.mkdir()
        events_file = session_dir / "events.jsonl"
        events = []
        base_ts = time.time()
        for i in range(20):
            events.append({
                "ts": base_ts + i,
                "kind": "decision_trace",
                "step": i // 4,
                "data": {
                    "category": "model_selection",
                    "decision": f"Selected model-{i}",
                    "reasoning": f"Best for step {i}",
                },
            })
        with events_file.open("w") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

        timeline = load_timeline_from_events(events_file)
        assert timeline is not None
        assert len(timeline.decisions) == 20
        assert timeline.total_steps == 4
