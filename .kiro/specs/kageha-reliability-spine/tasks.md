# Implementation Plan: Kageha Reliability Spine

## Overview

This plan implements the Reliability Spine as one direct replacement of the
fragmented completion path, following the design's execution order:

`REL-001 → REL-002 → REL-003` (fail-closed approvals, no interactive test hangs, canonical qualification commands)
`REL-010 → REL-011 → REL-012 → REL-013` (TaskContract model, deterministic extraction, semantic completion, persistence)
`REL-020 → REL-021 → REL-022` (Evidence_Ledger, evidence producers, compatibility evidence)
`REL-010 + REL-020 → REL-030 → REL-031 → REL-032 → REL-033` (VerificationEngine, controller integration, completion semantics, split-path removal)
`REL-040 + REL-041 → REL-042` (evaluation manifests, adversarial suite, repeated runs/comparison)
`REL-003 + REL-032 + REL-042 → REL-043` (strict release gates)
`REL-013 + REL-033 → REL-050 → REL-051` (architecture docs, migration guide)

Work proceeds top-to-bottom through these groups. Property-based tests
(Hypothesis, `@settings(max_examples=100)`) are added as sub-tasks next to
the implementation they validate, each referencing its design Property
number and Requirements clause. Unit tests from the design's Testing
Strategy section are included as sub-tasks. Test-related sub-tasks are
marked optional with `*` and are not implemented by the coding agent.

## Tasks

- [ ] 1. Fail-closed ApprovalGate wiring (REL-001)
  - [x] 1.1 Make `LoopController` fail closed by default
    - In `kageha/loop/controller.py`, change `self.approver = approver or cli_approver` to `self.approver = approver` so no construction path silently installs the interactive approver
    - Confirm `ApprovalGate._ask()` in `kageha/harness/approvals.py` already returns `ApprovalOutcome(False)` / `denied_no_approver` when `self.approver is None`; no change needed there
    - _Requirements: 2.1, 2.3_
  - [x] 1.2 Wire `cli_approver` explicitly at CLI/REPL entrypoints only
    - In `kageha/cli.py`'s one-shot path and `kageha/chat/repl.py`'s interactive path, pass `approver=cli_approver` explicitly into the `TurnRequest`/`durable.execute(...)` call when the session is interactive
    - Verify the WebUI's `_make_web_approver` path and the eval harness/test construction paths do not receive `cli_approver` implicitly
    - _Requirements: 2.2_
  - [x] 1.3 Preserve injected-approver override and allowlist auto-approve behavior
    - Confirm `ApprovalGate.require()` uses an explicitly injected approver instead of `cli_approver` for all decisions in that run
    - Confirm allowlist-matched actions are approved regardless of `auto_approve` or approver presence (existing `process_permissions()` inheritance path)
    - _Requirements: 2.4, 2.5_
  - [ ] 1.4 Write property test for fail-closed default construction
    - **Property 5: Mutating tool calls fail closed by default, regardless of construction path**
    - **Validates: Requirements 2.1, 2.3**
  - [x] 1.5 Write property test for injected-approver override
    - **Property 6: An explicitly injected approver always overrides the interactive default**
    - **Validates: Requirements 2.4**
  - [x] 1.6 Write property test for allowlist auto-approve
    - **Property 7: Allowlisted actions are approved regardless of other approval state**
    - **Validates: Requirements 2.5**
  - [x] 1.7 Write unit test verifying only CLI entrypoint installs `cli_approver`
    - Assert the WebUI and eval-harness entrypoints do not install `cli_approver`
    - _Requirements: 2.2_
  - [ ] 1.8 Update/extend the computer permission regression test suite
    - Assert driver mocks observe zero completed mutation calls for denied requests (brief non-mutating driver contact before the block is acceptable)
    - _Requirements: 2.6_

- [ ] 2. Remove interactive test hangs (REL-002)
  - [x] 2.1 Inject deterministic approval decisions into mode-machine test fixtures
    - Update `tests/test_mode_machines.py` (and any shared fixtures) to inject deterministic approval decisions instead of prompting for input
    - _Requirements: 3.1_
  - [x] 2.2 Add a stdin-read test guard
    - Add a guard (e.g. a pytest fixture/conftest hook) that fails a test immediately if it attempts to read from stdin without a prior interactive prompt already having occurred earlier in that test
    - _Requirements: 3.2_
  - [x] 2.3 Mark genuinely-live tests explicitly
    - Identify tests that require a live UI or provider interaction (e.g. `tests/test_live_providers.py`) and mark them explicitly as live via a marker/skip condition rather than allowing implicit interaction
    - _Requirements: 3.3_
  - [ ] 2.4 Write property test for the stdin guard
    - **Property 8: The stdin guard fails immediately absent a prior interactive prompt**
    - **Validates: Requirements 3.2**
  - [ ] 2.5 Run the full test suite and confirm no interactive prompting occurs
    - With REL-001's fail-closed approval behavior in place, confirm the suite completes without prompting for interactive input
    - _Requirements: 3.4_

- [ ] 3. Canonical qualification commands (REL-003)
  - [x] 3.1 Define the Core_Qualification_Command
    - Add/confirm a fast qualification command (e.g. a `Makefile`/`pyproject.toml` script target or `scripts/` entry) running a representative subset of checks
    - _Requirements: 4.1, 4.5_
  - [x] 3.2 Define the Full_Qualification_Command
    - Add/confirm a command running lint, type checking, Python tests, and frontend tests together, completing without prompting for input except where an individual test genuinely requires confirmation
    - _Requirements: 4.2, 4.3_
  - [ ] 3.3 Record skipped tests and their required environment
    - Configure the Full_Qualification_Command's test runner to record skipped tests and the environment each requires (e.g. via pytest skip reasons surfaced in a summary report)
    - _Requirements: 4.4_
  - [ ] 3.4 Write unit test for skipped-test recording
    - Assert a skipped test's required environment is recorded in the Full_Qualification_Command's output
    - _Requirements: 4.4_

- [ ] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. TaskContract model and element types (REL-010)
  - [ ] 5.1 Create `kageha/contract/models.py` with all TaskContract element types
    - Implement `ConstraintSource`, `ContractStatus`, `Constraint`, `RequirementKind`, `Requirement`, `Deliverable`, `VerifierKind`, `VerifierSpec`, `SuccessCriterion`, `PermissionEnvelope`, `ResourceBudget`, and `TaskContract` exactly as specified in the design, with `schema_version=1` and `compiler_source` field
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_
  - [ ] 5.2 Implement per-field ResourceBudget resolution
    - Implement the `min(default, requested)` per-field resolution rule for `max_steps`, `max_usd`, `max_time_s`, defaulting to `config.max_steps()`/`config.max_usd()` when no request value is supplied
    - _Requirements: 5.4, 5.5_
  - [ ] 5.3 Create `kageha/contract/convert.py` compatibility bridge
    - Implement `to_goal_card(contract)` mapping each `SuccessCriterion` to one `GoalItem` (`id`, `description`, `passes=False`), preserving `GoalCard.all_passed()`/`progress()`/`to_markdown()` behavior
    - Implement `to_task_state_deliverables(contract)` and `to_task_state_constraints(contract)`
    - _Requirements: 1.3, 5.6_
  - [ ] 5.4 Write property test for contract-to-GoalCard/TaskState conversion
    - **Property 1: Contract-to-GoalCard/TaskState conversion is structure-preserving**
    - **Validates: Requirements 1.3, 5.6**
  - [ ] 5.5 Write property test for TaskContract structural invariants
    - **Property 9: TaskContract structural invariants hold for any mix of required/optional criteria**
    - **Validates: Requirements 5.1, 5.2**
  - [ ] 5.6 Write property test for Constraint.source tagging
    - **Property 10: Constraint.source correctly tags its creation path**
    - **Validates: Requirements 5.3**
  - [ ] 5.7 Write property test for ResourceBudget resolution
    - **Property 11: ResourceBudget always resolves to the stricter of default and requested, per field**
    - **Validates: Requirements 5.4, 5.5**

- [ ] 6. Deterministic contract extraction (REL-011)
  - [ ] 6.1 Implement `Deterministic_Extractor` in `kageha/contract/extractor.py`
    - Extend the existing regex families from `runtime/validators.compile_requirements()` (slide/page counts, citations, browser outcome) with filename, dimensions, test-command, and explicit-prohibition patterns
    - Return typed `Requirement` entries with the correct `RequirementKind`
    - _Requirements: 6.1_
  - [ ] 6.2 Implement contradiction detection
    - When two explicit `Requirement` entries share a `kind` but have conflicting `value`s, mark both `status=UNRESOLVED` with mutual `contradicts` references instead of dropping or preferring one
    - _Requirements: 6.3_
  - [ ] 6.3 Enforce continuous unresolved enforcement
    - Ensure the Contract_Compiler and downstream steps check `status == UNRESOLVED` and never auto-resolve a contradictory pair without an explicit user-clarification event that the extractor recognizes as resolving that specific pair
    - _Requirements: 6.4_
  - [ ] 6.4 Convert `compile_requirements()` into a thin compatibility adapter
    - Rewrite `runtime/validators.compile_requirements()` to call `Deterministic_Extractor.extract()` and project the result into the existing flat dict shape (`{"slides": int, "citations": bool, ...}`)
    - _Requirements: 6.2_
  - [ ] 6.5 Write property test for deterministic extraction coverage
    - **Property 12: Deterministic extraction recovers every supported Requirement type from matching text**
    - **Validates: Requirements 6.1**
  - [ ] 6.6 Write property test for compile_requirements() compatibility shape
    - **Property 13: compile_requirements() preserves its pre-migration dict shape**
    - **Validates: Requirements 6.2**
  - [ ] 6.7 Write property test for contradiction handling
    - **Property 14: Contradictory explicit requirements are marked unresolved and stay unresolved absent clarification**
    - **Validates: Requirements 6.3, 6.4**

- [ ] 7. Semantic contract completion (REL-012)
  - [ ] 7.1 Implement `Semantic_Completion_Service` in `kageha/contract/semantic.py`
    - Send the deterministic draft contract to the planner model using structured output via `ModelRouter.chat(role="planning")`, matching the `loop/planner.make_plan` pattern
    - _Requirements: 7.1_
  - [ ] 7.2 Implement additive merge and rejection rules
    - Merge additive `SuccessCriterion`/`Constraint` entries with `source=SEMANTIC`
    - Reject (drop only) any single entry that deletes or weakens an `EXPLICIT`- or `DETERMINISTIC`-sourced entry
    - Validate each new entry's id, dependency ids, `VerifierSpec.kind` availability, and budget values before acceptance; drop only the individually invalid entry, not the whole response
    - _Requirements: 7.2, 7.3, 7.4, 7.6_
  - [ ] 7.3 Implement fallback on planner failure or invalid schema
    - Catch planner call exceptions and schema validation errors; return the deterministic draft unchanged with `compiler_source="deterministic_fallback"` without blocking the turn
    - _Requirements: 7.5_
  - [ ] 7.4 Skip planner call for trivial/lookup turns
    - Reuse `loop/verifier.is_lookup_status_text` plus a trivial-conversation check to skip the `Semantic_Completion_Service` call for trivial chat, simple lookup, or status requests
    - _Requirements: 7.7_
  - [ ] 7.5 Write property test for additive/selective semantic merge
    - **Property 15: Semantic completion is additive and selectively validated**
    - **Validates: Requirements 7.2, 7.3, 7.4, 7.6**
  - [ ] 7.6 Write property test for planner failure fallback
    - **Property 16: Planner failure or invalid schema falls back without blocking**
    - **Validates: Requirements 7.5**

- [ ] 8. Contract_Compiler orchestration and trivial-turn classification (REL-010, REL-012)
  - [ ] 8.1 Implement `ContractCompiler` in `kageha/contract/compiler.py`
    - Implement `compile(objective, session_id, turn_id, default_budget)` orchestrating classification, deterministic extraction, draft assembly, semantic completion, and budget resolution as specified in the design
    - Return `None` for trivial/lookup turns so the caller keeps building `GoalCard.from_task()` directly
    - _Requirements: 1.2, 1.8, 7.7_
  - [ ] 8.2 Write property test for trivial/lookup classification skip
    - **Property 3: Trivial/lookup classification skips both contract compilation and the planner call**
    - **Validates: Requirements 1.8, 7.7**

- [ ] 9. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 10. Persist and expose TaskContracts (REL-013)
  - [ ] 10.1 Add additive `task_contracts` table and migration to `RuntimeStore`
    - In `kageha/runtime/store.py`, bump `SCHEMA_VERSION` from 1 to 2 and add a dedicated `version == 1` migration branch that runs `CREATE TABLE IF NOT EXISTS task_contracts (...)` and `PRAGMA user_version=2` without touching existing tables
    - Ensure the pre-existing "rebuild when version diverges" branch continues to fire only for `version == 0`
    - _Requirements: 8.1, 8.4, 1.9_
  - [ ] 10.2 Implement `put_task_contract` / `get_task_contract`
    - Add `RuntimeStore.put_task_contract(contract)` and `RuntimeStore.get_task_contract(session_id, turn_id)` keyed by session ID and turn ID, serializing/deserializing the `TaskContract` as JSON
    - Treat a missing contract row as "no contract" rather than an error
    - _Requirements: 8.1, 1.5_
  - [ ] 10.3 Include contract summaries in accepted/planned events
    - When a TaskContract is accepted or planned, include the contract version and criterion summaries in the corresponding accepted/planned event payload (additive fields only)
    - _Requirements: 8.2_
  - [ ] 10.4 Restore TaskContract on replay/resume
    - Ensure session replay/resume paths call `get_task_contract` to restore the TaskContract associated with each turn
    - _Requirements: 8.3_
  - [ ] 10.5 Compile a contract for a contract-less session's next executable turn
    - In the turn-intake path, when a session's most recent turn has no persisted TaskContract and the next turn is executable, compile a TaskContract before execution proceeds
    - _Requirements: 1.7_
  - [ ] 10.6 Write property test for TaskContract persistence round-trip
    - **Property 17: TaskContract persistence round-trips by session and turn, including through replay/resume**
    - **Validates: Requirements 8.1, 8.3**
  - [ ] 10.7 Write property test for contract-less session's next-turn compilation
    - **Property 4: A contract-less session's next executable turn always compiles a contract first**
    - **Validates: Requirements 1.7**
  - [ ] 10.8 Write property test for additive payload fields
    - **Property 2: Additive payload fields never remove existing fields**
    - **Validates: Requirements 1.4, 8.2, 11.3**
  - [ ] 10.9 Write unit test for intact pre-milestone session load
    - Assert loading an intact pre-milestone session (no `task_contracts` rows) succeeds end to end
    - _Requirements: 1.5_
  - [ ] 10.10 Write unit test for corrupted session load failing closed
    - Assert loading a session whose stored JSON is corrupted fails closed and requires re-creation rather than partial recovery
    - _Requirements: 1.6_
  - [ ] 10.11 Write unit test for the additive migration against a fixed fixture
    - Run the migration against a fixed pre-milestone `runtime.db` fixture and assert the two new tables are created without altering existing rows
    - _Requirements: 8.4, 21.1_

- [ ] 11. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 12. The Evidence_Ledger (REL-020)
  - [ ] 12.1 Create `EvidenceRecord`, `EvidenceSource`, `EvidenceCertainty` types
    - Implement in `kageha/verification/evidence.py` exactly as specified in the design (id, session_id, turn_id, criterion_id, tool_attempt_id, artifact_path, source, source_ref, timestamp, digest, certainty, producer, metadata, probe)
    - _Requirements: 9.2_
  - [ ] 12.2 Add additive `evidence_records` table to `RuntimeStore`
    - Extend the same `version == 1 → 2` migration branch from task 10.1 to also create `evidence_records` and its `evidence_records_criterion` index
    - Add `RuntimeStore.append_evidence(record)` and `RuntimeStore.evidence_for_turn(session_id, turn_id)`, with no `UPDATE`/`DELETE` path exposed (immutability by construction)
    - _Requirements: 9.1_
  - [ ] 12.3 Implement `EvidenceLedger`
    - Implement `EvidenceLedger.append()` (runs every string field through `kageha.obs.events.redact()` before INSERT, rejects duplicate ids rather than overwriting), `for_criterion()`, and `for_turn()`
    - _Requirements: 9.1, 9.4_
  - [ ] 12.4 Implement staleness handling
    - In verification-context evidence lookups, treat any `EvidenceRecord` whose `turn_id` differs from the turn currently being verified as `certainty=STALE` context unless a fresh record in the current turn references it via `metadata={"reconfirms": old_id}`
    - _Requirements: 9.3_
  - [ ] 12.5 Write property test for evidence ledger round-trip and immutability
    - **Property 18: Evidence ledger entries round-trip by their linking keys and are never mutated**
    - **Validates: Requirements 9.1**
  - [ ] 12.6 Write property test for stale evidence handling
    - **Property 19: Evidence from a different turn is never treated as fresh proof without explicit re-confirmation**
    - **Validates: Requirements 9.3**
  - [ ] 12.7 Write property test for secret redaction before persistence
    - **Property 20: Secret-shaped substrings are redacted before persistence**
    - **Validates: Requirements 9.4**

- [ ] 13. Convert runtime observations into evidence (REL-021)
  - [ ] 13.1 Add `to_evidence()` to artifact validators
    - In `kageha/runtime/validators.py`, add `to_evidence(check)` to `FileValidator`, `PDFValidator`, `PowerPointValidator`, etc., capturing a sha256 digest and existing structural fields (page/slide counts, dimensions)
    - _Requirements: 10.1_
  - [ ] 13.2 Add evidence hook to `ToolJournal.after()`
    - Emit one `EvidenceRecord` per completed command-shaped tool call, carrying the command, workspace-relative cwd, exit status, and a bounded digest of stdout/stderr
    - _Requirements: 10.2_
  - [ ] 13.3 Require post-action probes for browser/computer mutation evidence
    - Only produce an `EvidenceRecord` with `certainty=VERIFIED` for a mutation when a post-action probe (state/screenshot follow-up, or DOM/URL check) exists for the same `tool_attempt_id` chain; otherwise do not create a record or create one with `certainty=UNVERIFIABLE`
    - _Requirements: 10.3_
  - [ ] 13.4 Emit research retrieval evidence
    - In `kageha/research/backend.py` / `kageha/research/citations.py`, emit `EvidenceRecord(source=RESEARCH_RETRIEVAL, source_ref=url, metadata={"claim_id", "retrieved_at"})`
    - _Requirements: 10.4_
  - [ ] 13.5 Implement tool-output acceptance with configured quorum
    - When more than one verifier stage examines the same `EvidenceRecord.id` and stances differ, accept the evidence as proof only when the configured acceptance threshold (default majority) is met; otherwise treat the tool output alone as insufficient
    - _Requirements: 10.5, 10.6_
  - [ ] 13.6 Write property test for per-source evidence field population
    - **Property 21: Each evidence producer populates the fields required for its source type**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4**
  - [ ] 13.7 Write property test for verifier quorum acceptance
    - **Property 22: Verifier acceptance of tool output requires quorum, and rejected output alone is never sufficient**
    - **Validates: Requirements 10.5, 10.6**

- [ ] 14. Preserve compatibility evidence (REL-022)
  - [ ] 14.1 Implement `render_evidence_text()` projection
    - Add a pure `render_evidence_text(records: list[EvidenceRecord]) -> str` function; derive `RunResult.verification_evidence` and `TaskState` tool-result notes from `EvidenceLedger.for_turn()` exclusively via this function
    - _Requirements: 11.1, 11.2_
  - [ ] 14.2 Add additive `evidence` array to event payloads
    - Add an additive `evidence` array (`[{"criterion_id", "source", "certainty", "digest"}, ...]`) to relevant runtime events alongside existing string fields
    - _Requirements: 11.3_
  - [ ] 14.3 Write property test for legacy evidence as a pure projection
    - **Property 23: Legacy string evidence is a pure projection of the EvidenceRecord set**
    - **Validates: Requirements 11.1**

- [ ] 15. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 16. VerificationEngine as sole authority (REL-030)
  - [ ] 16.1 Define `CriterionStatus`, `VerificationDefect`, `CriterionVerdict`, `VerificationReport` types
    - Implement in `kageha/verification/engine.py` exactly as specified in the design, including `VerificationReport.success` computed from the required-only rule
    - _Requirements: 12.1_
  - [ ] 16.2 Define the `Verifier` protocol and `VerificationEngine.verify()`
    - Implement `Verifier` protocol and `VerificationEngine.__init__(ledger, verifiers)` / `verify(contract, criterion_ids, ctx)`, supporting `criterion_ids=None` (verify all) and a scoped subset
    - _Requirements: 12.1_
  - [ ] 16.3 Implement `DeterministicPostconditionVerifier`
    - Wrap `verifier_agent.py`'s `check_files_exist` / `check_python_syntax` / `check_tests` / `check_lint` functions individually (not via `run_deterministic_verification`, which is not called in production)
    - _Requirements: 12.3_
  - [ ] 16.4 Implement `ArtifactCheckVerifier`
    - Wrap `runtime/validators.ValidatorRegistry`; convert `validate_result()` into a thin adapter that calls `VerificationEngine.verify()` and reshapes the `VerificationReport` back into the existing `VerificationResult` dataclass shape
    - _Requirements: 12.2, 15.1_
  - [ ] 16.5 Implement `SemanticJudgmentVerifier`
    - Wrap `loop/verifier.verify_with_defects` unchanged, adapted to return a `CriterionVerdict`
    - _Requirements: 12.2_
  - [ ] 16.6 Implement fixed three-stage precedence
    - Ensure `verify()`'s per-criterion loop always runs all three stages in order, always records all three `stage_results`, and sets final `status=FAIL` when the deterministic stage fails regardless of the semantic stage's result
    - _Requirements: 12.4, 12.5_
  - [ ] 16.7 Write property test for legacy check adaptation parity
    - **Property 24: Adapting a legacy check preserves its original outcome**
    - **Validates: Requirements 12.2, 15.1**
  - [ ] 16.8 Write property test for fixed stage precedence
    - **Property 25: All three verification stages always run, in order, with fixed precedence**
    - **Validates: Requirements 12.4, 12.5**

- [ ] 17. Move deterministic validation inside the controller (REL-031)
  - [ ] 17.1 Wire milestone-scoped verification into `LoopController.run`
    - Replace the current single `verify_with_defects` milestone call with `engine.verify(contract, criterion_ids=milestone_criteria, ctx=...)` when a `PlanStage` transitions to `DONE`
    - _Requirements: 13.1_
  - [ ] 17.2 Wire completion-claim verification
    - Where the loop calls `verify_with_defects(goal, ...)` after `model_said_done`, call `engine.verify(contract, criterion_ids=None, ctx=...)` covering all required criteria instead
    - _Requirements: 13.2_
  - [ ] 17.3 Feed failing required criteria into the existing repair/replan path
    - Append each required, failed `CriterionVerdict.defect` into `task_state.failures`/`Defect` exactly as `verify.snapshot.defects` does today, so `ControlDecision.REPAIR`/`REPLAN_STAGE` needs no change
    - _Requirements: 13.3_
  - [ ] 17.4 Wire targeted post-repair re-verification
    - After a targeted repair, call `engine.verify` scoped to only the repaired criteria, leaving other already-`PASS` verdicts untouched
    - _Requirements: 13.4_
  - [ ] 17.5 Wire final full verification before reporting success
    - Immediately before `LoopController.run` returns `RunResult` with `status="success"`, run one more `engine.verify(contract, criterion_ids=None, ...)`; set `RunResult.validated` from that report's `.success`
    - Add `contract`, `evidence`, `report` fields to `RunResult` additively, alongside existing `validated`, `verification_evidence`, `verified_facts` fields
    - _Requirements: 13.5, 1.4_
  - [ ] 17.6 Write property test for milestone-scoped verification
    - **Property 26: Milestone verification is scoped exactly to that milestone's criteria**
    - **Validates: Requirements 13.1**
  - [ ] 17.7 Write property test for completion-claim full-required-set verification
    - **Property 27: A completion claim always verifies the full required-criteria set**
    - **Validates: Requirements 13.2**
  - [ ] 17.8 Write property test for defect propagation to repair
    - **Property 28: Every failing required criterion produces a defect fed to the repair path**
    - **Validates: Requirements 13.3**
  - [ ] 17.9 Write property test for targeted post-repair re-verification scope
    - **Property 29: Targeted repair re-verification touches exactly the repaired criteria**
    - **Validates: Requirements 13.4**
  - [ ] 17.10 Write property test for final full verification before success
    - **Property 30: Reporting completion always follows a fresh final full verification**
    - **Validates: Requirements 13.5**

- [ ] 18. Consume the final VerificationReport in AgentRuntime (REL-031, REL-033)
  - [ ] 18.1 Simplify `AgentRuntime._execute` to consume the controller's report only
    - In `kageha/runtime/engine.py`, delete the block computing `result.validated = semantic_passed and deterministic.deterministic_passed`; replace with `result.validated = bool(result.report and result.report.success)`
    - _Requirements: 13.6, 15.2_
  - [ ] 18.2 Populate the VERIFICATION event from the final report
    - Populate the `VERIFICATION` event emitted after the controller returns from `result.report` (criteria/defects/evidence ids) instead of a freshly recomputed deterministic result
    - _Requirements: 13.6, 15.2_
  - [ ] 18.3 Write property test for runtime/controller validated-outcome equivalence
    - **Property 31: The runtime's validated outcome always equals the controller's final report outcome**
    - **Validates: Requirements 13.6, 15.2**

- [ ] 19. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 20. Completion semantics (REL-032)
  - [ ] 20.1 Implement the required/optional success rule in `VerificationReport.success`
    - Task succeeds iff every `required=True` criterion has `status=PASS` backed by at least one accepted `EvidenceRecord`; an optional criterion may pass without accepted evidence
    - A required criterion left `UNRESOLVED` is treated as not-passing (`BLOCKED`/`FAIL`) for the success determination
    - An optional criterion's failure is included in the final response without excluding the task from success
    - _Requirements: 14.1, 14.2, 14.3_
  - [ ] 20.2 Implement contradiction escalation and undeliverable-escalation fallback
    - When a requirement is impossible or contradicts another and cannot be resolved safely, escalate to the user
    - If escalation delivery fails, continue the task with a warning noted in the final response instead of halting silently
    - _Requirements: 14.4, 14.5_
  - [ ] 20.3 Implement budget-exhaustion handling
    - When `ResourceBudget` is exhausted, stop issuing new actions, preserve partial artifacts already written to the workspace, and report the task as not successful
    - _Requirements: 14.6_
  - [ ] 20.4 Write property test for completion semantics success rule
    - **Property 32: Completion semantics — success iff every required criterion passes with accepted evidence**
    - **Validates: Requirements 14.1, 14.2, 14.3**
  - [ ] 20.5 Write property test for contradiction escalation and warning fallback
    - **Property 33: Unresolvable contradictions escalate, and undeliverable escalation degrades to a warning**
    - **Validates: Requirements 14.4, 14.5**
  - [ ] 20.6 Write property test for budget-exhaustion artifact preservation
    - **Property 34: Budget exhaustion always preserves partial artifacts and never reports success**
    - **Validates: Requirements 14.6**

- [ ] 21. Remove obsolete split-path behavior (REL-033)
  - [ ] 21.1 Convert remaining compatibility verification functions into thin adapters
    - Ensure every existing compatibility verification function delegates to `VerificationEngine` rather than duplicating pass/fail logic
    - _Requirements: 15.1_
  - [ ] 21.2 Make runtime post-processing derive pass/fail exclusively from the VerificationReport
    - Confirm no code path outside `VerificationEngine` independently computes a pass/fail decision for a criterion
    - _Requirements: 15.2_
  - [ ] 21.3 Implement idempotent verification event emission on replay/resume
    - Derive verification event idempotency keys from `VerificationReport.report_id` (deterministic per `(turn_id, scope, criterion_ids)`); rely on `RuntimeStore.append_event`'s existing `UNIQUE(idempotency_key)` handling to skip duplicate inserts on replay/resume
    - _Requirements: 15.3_
  - [ ] 21.4 Write property test for idempotent replay of verification events
    - **Property 35: Replay/resume never duplicates or drops verification events**
    - **Validates: Requirements 15.3**

- [ ] 22. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 23. Extend evaluation manifests (REL-040)
  - [ ] 23.1 Add contract-aware fields to `GoldenTask`
    - In `kageha/eval/harness.py`, add `contract_criteria`, `fixtures`, `forbidden_actions`, `expected_terminal_state`, `max_time_s`, `repeat=1` as additive optional fields to `GoldenTask` without changing any existing field or `load_goldens()`'s JSON shape for files that omit them
    - _Requirements: 16.1, 16.3_
  - [ ] 23.2 Record run environment metadata
    - Implement `run_environment()` returning model identifier, harness configuration hash, dependency lock digest, platform, and repository commit; record these for each evaluation run via `RuntimeStore.record_benchmark()`
    - _Requirements: 16.2_
  - [ ] 23.3 Write property test for manifest field round-trip
    - **Property 36: Manifest parsing round-trips all contract-aware fields**
    - **Validates: Requirements 16.1**
  - [ ] 23.4 Write unit test confirming `load_goldens()` still parses existing golden fixtures unchanged
    - _Requirements: 16.3_
  - [ ] 23.5 Write unit test for the five required environment fields
    - Assert model id, harness config, dependency lock digest, platform, and commit are populated for one representative run
    - _Requirements: 16.2_

- [ ] 24. Thirty adversarial tasks (REL-041)
  - [ ] 24.1 Define `AdversarialTask` and `AdversarialRunResult` types
    - Implement in `kageha/eval/adversarial.py` exactly as specified in the design, including `ADVERSARIAL_REPEAT_COUNT = 3`
    - _Requirements: 17.6_
  - [ ] 24.2 Author six coding adversarial tasks
    - Create `kageha/eval/adversarial_tasks/coding.json` covering tests, forbidden test modification, syntax failure, regression, partial repair, and budget exhaustion, each with a non-empty `false_success_trap`
    - _Requirements: 17.1, 17.6_
  - [ ] 24.3 Author six artifact adversarial tasks
    - Create `kageha/eval/adversarial_tasks/artifact.json` covering missing files, empty files, wrong counts, invalid formats, render failure, and subjective unresolved quality, each with a non-empty `false_success_trap`
    - _Requirements: 17.2, 17.6_
  - [ ] 24.4 Author six browser/computer adversarial tasks
    - Create `kageha/eval/adversarial_tasks/browser.json` covering verified mutation, unverifiable input, stale screenshot, permission denial, partial navigation, and changed UI state, each with a non-empty `false_success_trap`
    - _Requirements: 17.3, 17.6_
  - [ ] 24.5 Author six research adversarial tasks
    - Create `kageha/eval/adversarial_tasks/research.json` covering missing citation, unreachable citation, citation-claim mismatch, stale source, conflicting sources, and incomplete evidence, each with a non-empty `false_success_trap`
    - _Requirements: 17.4, 17.6_
  - [ ] 24.6 Author six lifecycle adversarial tasks
    - Create `kageha/eval/adversarial_tasks/lifecycle.json` covering interruption, resume, repeated failure, contradictory requirements, impossible task, and stale prior-turn evidence, each with a non-empty `false_success_trap`
    - _Requirements: 17.5, 17.6_
  - [ ] 24.7 Write unit test for the fixed adversarial suite content
    - Assert exactly six tasks per category and a non-empty `false_success_trap` on all thirty tasks
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.6_

- [ ] 25. Repeated evaluation and comparison (REL-042)
  - [ ] 25.1 Implement `run_adversarial_suite()`
    - Run each `AdversarialTask` exactly `ADVERSARIAL_REPEAT_COUNT` (3) times per configuration regardless of `task.repeat`, recording outcome classification, cost, latency, steps, and tool-call count for each run
    - Store each run via `RuntimeStore.record_benchmark(suite="adversarial", ...)` plus one aggregate summary per task
    - _Requirements: 18.1, 18.2, 18.3_
  - [ ] 25.2 Implement the `Evaluation_CLI` `run`/`compare`/`inspect` commands
    - Mount a new Typer sub-app from `kageha/cli.py` as `kageha eval run|compare|inspect`; `run` executes and stores results, `compare` diffs pass rates and false-success counts between two stored runs, `inspect` prints per-task reasons, evidence digests, and cost
    - _Requirements: 18.4_
  - [ ] 25.3 Write property test for exactly-three-iterations enforcement
    - **Property 37: The adversarial runner executes exactly three iterations per task/configuration**
    - **Validates: Requirements 18.1**
  - [ ] 25.4 Write property test for required outcome-field population
    - **Property 38: Every recorded run populates all required outcome fields**
    - **Validates: Requirements 18.2**
  - [ ] 25.5 Write property test for benchmark storage round-trip
    - **Property 39: Benchmark storage round-trips individual and aggregate results**
    - **Validates: Requirements 18.3**
  - [ ] 25.6 Write unit test for Evaluation_CLI subcommand parsing
    - Assert `run`, `compare`, `inspect` exist and parse their arguments correctly
    - _Requirements: 18.4_

- [ ] 26. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 27. Strict release gates (REL-043)
  - [ ] 27.1 Run the full adversarial suite and confirm zero false-success outcomes
    - Execute all thirty adversarial tasks across all ninety focused runs (3 configurations x 30 tasks) and confirm zero `false_success` outcomes
    - _Requirements: 19.1_
  - [ ] 27.2 Confirm zero driver mutations for permission-denial tasks
    - Confirm the permission-denial adversarial tasks cause zero driver mutations across their runs
    - _Requirements: 19.2_
  - [ ] 27.3 Confirm deterministic reproducibility for non-model fixtures
    - Confirm the non-model fixtures within the Adversarial_Task_Suite achieve at least 95 percent deterministic reproducibility across repeated runs
    - _Requirements: 19.3_
  - [ ] 27.4 Confirm no regression against existing golden tasks
    - Run the golden task suite and confirm no regression relative to its pre-milestone baseline
    - _Requirements: 19.4_
  - [ ] 27.5 Run the Full_Qualification_Command three consecutive times
    - Confirm three consecutive green runs of the Full_Qualification_Command
    - _Requirements: 19.5, 4.6_

- [ ] 28. Reconcile architecture documentation (REL-050)
  - [ ] 28.1 Update architecture documentation to remove stale absence claims
    - Replace stale claims in the project's architecture documentation that verifier agents, specs, tracing, and replay are absent
    - _Requirements: 20.1_
  - [ ] 28.2 Document integrated vs. experimental component status
    - Add a section documenting which components (Contract_Compiler, Evidence_Ledger, VerificationEngine, evaluation harness extensions, etc.) are integrated versus experimental
    - _Requirements: 20.2_
  - [ ] 28.3 Document the contract/evidence/verification lifecycle and qualification commands
    - Add a section documenting the TaskContract → Evidence_Ledger → VerificationEngine → repair lifecycle and the Core_Qualification_Command / Full_Qualification_Command
    - _Requirements: 20.3_

- [ ] 29. Migration and operator guidance (REL-051)
  - [ ] 29.1 Write the Migration_Guide's database migration and compatibility section
    - Document the additive database migration (`SCHEMA_VERSION` 1 → 2), old-session behavior (no `task_contracts` row is normal), and the new verification event additions
    - _Requirements: 21.1_
  - [ ] 29.2 Write the Migration_Guide's troubleshooting section
    - Document failure troubleshooting steps for the new verification spine (e.g. diagnosing `UNRESOLVED` criteria, stale evidence, escalation warnings)
    - _Requirements: 21.2_
  - [ ] 29.3 Document the direct-replacement nature of the new verifier
    - State explicitly that the new verifier is a direct replacement for the prior verification path, with no shadow path or feature flag
    - _Requirements: 21.3_

- [ ] 30. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP; the coding agent MUST NOT implement `*`-marked sub-tasks.
- Every implementation task references specific requirement clauses for traceability.
- Property tests use Hypothesis with `@settings(max_examples=100)` minimum, live under `tests/property/test_<area>.py`, and mock the planner model, browser/computer driver, and network calls, per the design's Testing Strategy.
- Checkpoints ensure incremental validation and give the user a chance to raise questions before moving to the next requirement group.
- REL-002/REL-003 (group 2) come before the contract/evidence/verification work so the test suite is noninteractive before more tests are added on top of it.
- REL-030 depends on both REL-010 (TaskContract/SuccessCriterion) and REL-020 (Evidence_Ledger) being in place, matching the design's stated dependency.
- REL-043 depends on REL-003 (qualification commands), REL-032 (completion semantics), and REL-042 (repeated evaluation), matching the design's stated dependency.
- REL-050/REL-051 depend on REL-013 (persistence) and REL-033 (split-path removal) being complete, matching the design's stated dependency.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.2", "3.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.1", "2.3", "3.2"] },
    { "id": 2, "tasks": ["1.4", "1.5", "1.6", "1.7", "1.8", "2.4", "2.5", "3.3"] },
    { "id": 3, "tasks": ["3.4"] },
    { "id": 4, "tasks": ["5.1"] },
    { "id": 5, "tasks": ["5.2", "5.3", "6.1", "12.1"] },
    { "id": 6, "tasks": ["5.4", "5.5", "5.6", "5.7", "6.2", "6.4", "12.2"] },
    { "id": 7, "tasks": ["6.3", "6.5", "6.6", "6.7", "12.3"] },
    { "id": 8, "tasks": ["7.1", "12.4"] },
    { "id": 9, "tasks": ["7.2", "7.3", "7.4", "12.5", "12.6", "12.7"] },
    { "id": 10, "tasks": ["7.5", "7.6", "8.1"] },
    { "id": 11, "tasks": ["8.2"] },
    { "id": 12, "tasks": ["10.1"] },
    { "id": 13, "tasks": ["10.2", "13.1"] },
    { "id": 14, "tasks": ["10.3", "10.4", "10.5", "13.2", "13.3", "13.4"] },
    { "id": 15, "tasks": ["10.6", "10.7", "10.8", "10.9", "10.10", "10.11", "13.5", "13.6", "13.7"] },
    { "id": 16, "tasks": ["14.1"] },
    { "id": 17, "tasks": ["14.2", "14.3"] },
    { "id": 18, "tasks": ["16.1"] },
    { "id": 19, "tasks": ["16.2"] },
    { "id": 20, "tasks": ["16.3", "16.4", "16.5"] },
    { "id": 21, "tasks": ["16.6"] },
    { "id": 22, "tasks": ["16.7", "16.8", "17.1"] },
    { "id": 23, "tasks": ["17.2"] },
    { "id": 24, "tasks": ["17.3"] },
    { "id": 25, "tasks": ["17.4"] },
    { "id": 26, "tasks": ["17.5"] },
    { "id": 27, "tasks": ["17.6", "17.7", "17.8", "17.9", "17.10", "18.1"] },
    { "id": 28, "tasks": ["18.2"] },
    { "id": 29, "tasks": ["18.3", "20.1"] },
    { "id": 30, "tasks": ["20.2", "20.3"] },
    { "id": 31, "tasks": ["20.4", "20.5", "20.6", "21.1"] },
    { "id": 32, "tasks": ["21.2"] },
    { "id": 33, "tasks": ["21.3"] },
    { "id": 34, "tasks": ["21.4", "23.1"] },
    { "id": 35, "tasks": ["23.2", "24.1"] },
    { "id": 36, "tasks": ["23.3", "23.4", "23.5", "24.2", "24.3", "24.4", "24.5", "24.6"] },
    { "id": 37, "tasks": ["24.7", "25.1"] },
    { "id": 38, "tasks": ["25.2"] },
    { "id": 39, "tasks": ["25.3", "25.4", "25.5", "25.6"] },
    { "id": 40, "tasks": ["27.1", "27.2", "27.3", "27.4", "27.5"] },
    { "id": 41, "tasks": ["28.1", "28.2", "28.3"] },
    { "id": 42, "tasks": ["29.1", "29.2", "29.3"] }
  ]
}
```
