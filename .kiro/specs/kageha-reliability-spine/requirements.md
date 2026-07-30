# Requirements Document

## Introduction

Kageha currently completes tasks through a fragmented set of paths: loose requirement dictionaries, ad hoc string evidence, and multiple independent pass/fail decisions (runtime post-processing, the standalone enhanced-verifier, and controller-side checks) that can disagree with each other. This milestone, the Kageha Reliability Spine, replaces that fragmented completion path with one authoritative loop: compile a typed contract, gather structured evidence, run one verification authority, and repair deterministic failures inside the controller — while preserving the existing CLI, WebUI, runtime events, stored sessions, and tool APIs. The change is a direct replacement; there is no shadow path or feature flag.

The milestone is complete only when the full all-extras qualification suite runs noninteractively and green three consecutive times, required outcomes are represented by typed success criteria, every successful criterion carries structured evidence, deterministic failures are repaired inside the controller loop, thirty focused adversarial tasks pass the false-success gate across ninety runs, and repeated evaluation baselines are stored and comparable.

## Glossary

- **Kageha_Agent**: The overall agent system, including its CLI, WebUI, runtime engine, and tool harness.
- **TaskContract**: The versioned, typed, authoritative representation of an executable request, composed of Constraint, Deliverable, Requirement, SuccessCriterion, PermissionEnvelope, and ResourceBudget entries.
- **Contract_Compiler**: The component that produces a TaskContract for a turn through deterministic extraction and optional semantic completion.
- **Deterministic_Extractor**: The subcomponent of the Contract_Compiler that performs rule-based extraction of explicit requirements from the request text.
- **Semantic_Completion_Service**: The subcomponent of the Contract_Compiler that sends the deterministic draft contract to the planner model for structured completion.
- **Constraint**: A TaskContract element recording a limiting condition and whether its source is explicit user text, deterministic inference, or semantic inference.
- **Deliverable**: A TaskContract element describing an expected output artifact.
- **Requirement**: A TaskContract element describing an explicit or inferred obligation extracted from the request (file counts, filenames, slide or page counts, dimensions, citations, test commands, browser outcomes, or explicit prohibitions).
- **SuccessCriterion**: A TaskContract element with a stable ID, required or optional status, description, VerifierSpec, and dependency IDs, used to determine task success.
- **VerifierSpec**: A specification describing how a SuccessCriterion is checked by the VerificationEngine.
- **PermissionEnvelope**: A TaskContract element describing which mutating actions are allowed or denied for a turn.
- **ResourceBudget**: A TaskContract element describing the maximum steps, cost, and time allowed for a turn.
- **EvidenceRecord**: An immutable record proving a criterion outcome, linked to session, turn, criterion, tool attempt, and artifact where applicable.
- **EvidenceSource**: The enumerated origin of an EvidenceRecord (for example command output, artifact digest, browser probe, or research retrieval).
- **EvidenceCertainty**: The enumerated confidence level of an EvidenceRecord.
- **Evidence_Ledger**: The persistent, append-only store of EvidenceRecord entries.
- **VerificationEngine**: The single authoritative component that produces a CriterionVerdict for each SuccessCriterion and assembles a VerificationReport.
- **CriterionVerdict**: The pass, fail, blocked, or unresolved outcome the VerificationEngine assigns to one SuccessCriterion.
- **VerificationDefect**: A structured description of a failed required SuccessCriterion, fed into the LoopController's repair or replan control path.
- **VerificationReport**: The aggregate set of CriterionVerdict entries produced by the VerificationEngine for one verification pass.
- **GoalCard**: The existing public checklist type, populated from the TaskContract for backward compatibility.
- **TaskState**: The existing public executive-state type, populated from the TaskContract for backward compatibility.
- **RunResult**: The existing public result type, carrying the validated flag, verification events, and string evidence, plus additive structured contract, evidence, and report fields.
- **ApprovalGate**: The existing component that grants, denies, or asks for approval before a mutating Computer_Tool call proceeds.
- **LoopController**: The existing orchestration component that runs the plan, execute, verify, and repair loop for a turn.
- **Computer_Tool**: Any tool capable of performing a mutating action on the host filesystem, a browser, or a sandboxed computer.
- **CLI_Entrypoint**: A `kageha` command-line entry point intended for interactive human use.
- **Runtime_Store**: The existing SQLite-backed durable store of sessions, turns, and events.
- **Core_Qualification_Command**: The fast qualification command covering a representative subset of checks.
- **Full_Qualification_Command**: The all-extras qualification command covering lint, type checking, Python tests, and frontend tests.
- **Evaluation_Harness**: The component that executes golden and adversarial evaluation tasks against the Kageha_Agent and records results.
- **Evaluation_CLI**: The command-line interface exposing `run`, `compare`, and `inspect` commands for the Evaluation_Harness.
- **Benchmark_Storage**: The existing storage for individual evaluation runs and aggregate summaries.
- **Adversarial_Task_Suite**: The set of thirty focused private evaluation tasks defined for this milestone, each containing a known false-success trap.
- **Architecture_Documentation**: The project documentation describing Kageha_Agent architecture and component status.
- **Migration_Guide**: The operator-facing documentation describing database migration, compatibility, and troubleshooting for this milestone.

## Requirements

### Requirement 1: Preserve compatibility while the contract becomes authoritative

**User Story:** As an operator of Kageha, I want the reliability spine to replace the completion path without breaking existing interfaces, so that I can adopt the new verification behavior without migrating clients or losing history.

#### Acceptance Criteria

1. THE Kageha_Agent SHALL keep the CLI, WebUI, runtime event names, and tool APIs unchanged in shape for existing callers.
2. WHEN a nontrivial executable request is submitted, THE Contract_Compiler SHALL treat the compiled TaskContract as the authoritative interpretation of that request.
3. WHEN a TaskContract is compiled for a turn, THE Contract_Compiler SHALL populate the existing GoalCard and TaskState from that TaskContract.
4. THE RunResult type SHALL retain the existing validated field, verification events, and string evidence fields in addition to the added structured contract, evidence, and report fields.
5. WHILE a stored session predates TaskContract support and its stored data is intact, THE Kageha_Agent SHALL load that session successfully.
6. IF a stored session predates TaskContract support and its stored data is corrupted or cannot be migrated, THEN THE Kageha_Agent SHALL fail that session load and SHALL require the session to be re-created rather than loading partial or inconsistent data.
7. WHEN a stored session without a TaskContract begins its next executable turn, THE Contract_Compiler SHALL compile a TaskContract for that turn before execution proceeds.
8. WHEN a turn is classified as trivial conversation or a simple read-only lookup, THE Contract_Compiler SHALL retain the lightweight path without compiling a full TaskContract.
9. THE Kageha_Agent SHALL provide the reliability spine as a direct replacement of the prior completion path, with no shadow path and no feature flag governing which path runs.

### Requirement 2: Fail-closed approval for mutating computer tools (REL-001)

**User Story:** As a security-conscious operator, I want mutating computer tools denied by default when no approval is configured, so that unattended runs cannot silently perform destructive actions.

#### Acceptance Criteria

1. IF auto_approve is False and no approver is supplied, THEN THE ApprovalGate SHALL return a DENIED decision for a mutating Computer_Tool call before that call reaches the driver.
2. WHERE a CLI_Entrypoint is used, THE Kageha_Agent SHALL install the interactive CLI approver only for that CLI_Entrypoint.
3. WHILE the LoopController is constructed by controller code or by test construction, THE LoopController SHALL remain noninteractive by default.
4. WHEN an approver is explicitly injected into the LoopController, THE ApprovalGate SHALL use the injected approver instead of the interactive CLI approver.
5. WHEN a requested action matches an existing allowlist entry, THE ApprovalGate SHALL automatically approve that action regardless of other approval conditions.
6. WHEN the computer permission regression test suite runs, THE Kageha_Agent test suite SHALL pass with driver mocks observing zero completed mutation calls for denied requests, though brief non-mutating driver contact before the block is acceptable.

### Requirement 3: Remove interactive test hangs (REL-002)

**User Story:** As a developer running the test suite, I want tests to never block on interactive input, so that the qualification suite completes noninteractively.

#### Acceptance Criteria

1. THE mode-machine test fixtures SHALL inject deterministic approval decisions instead of prompting for input.
2. IF a test attempts to read from stdin and no interactive prompt has already occurred earlier in that test, THEN THE test guard SHALL fail that test immediately.
3. WHERE a test genuinely requires a live UI or provider interaction, THE test suite SHALL mark that test explicitly as live rather than allowing implicit interaction.
4. WHEN the Kageha_Agent test suite runs with the REL-001 fail-closed approval behavior in place, THE Kageha_Agent test suite SHALL complete without prompting for interactive input.

### Requirement 4: Canonical qualification commands (REL-003)

**User Story:** As a release engineer, I want one fast command and one full command for qualification, so that I can verify release readiness without ambiguity.

#### Acceptance Criteria

1. THE Kageha_Agent SHALL provide a Core_Qualification_Command that runs a fast subset of checks.
2. THE Kageha_Agent SHALL provide a Full_Qualification_Command that runs lint, type checking, Python tests, and frontend tests.
3. WHEN the Full_Qualification_Command runs, THE Full_Qualification_Command SHALL complete without prompting for input, except where an individual test genuinely requires user confirmation to proceed.
4. WHEN a test is skipped during a Full_Qualification_Command run, THE Full_Qualification_Command SHALL record the skipped test and its required environment.
5. WHERE a check other than the Full_Qualification_Command runs (including the Core_Qualification_Command or an individual test run), THE Kageha_Agent SHALL NOT be required to suppress prompting for that check.
6. THE Full_Qualification_Command SHALL complete with a green result three consecutive times before the milestone is considered done.

### Requirement 5: The TaskContract model (REL-010)

**User Story:** As a platform developer, I want a versioned TaskContract model, so that executable requests have one authoritative, structured representation.

#### Acceptance Criteria

1. THE TaskContract SHALL record a schema_version field set to 1.
2. THE TaskContract SHALL contain SuccessCriterion entries, each with a stable ID, required or optional status, description, VerifierSpec, and dependency IDs, and THE TaskContract SHALL accept both required and optional SuccessCriterion entries within the same contract.
3. THE Constraint entries within a TaskContract SHALL record their source as explicit user text, deterministic inference, or semantic inference.
4. WHERE a request does not supply stricter budget values, THE ResourceBudget SHALL inherit the existing runtime default budget values.
5. WHERE a request supplies stricter budget values, THE ResourceBudget SHALL use the supplied stricter values.
6. THE TaskContract SHALL provide conversion to the current GoalCard, TaskState plan state, and Deliverable records.

### Requirement 6: Deterministic contract extraction (REL-011)

**User Story:** As a platform developer, I want deterministic extraction of explicit requirements, so that unambiguous requests are captured reliably without depending on a model call.

#### Acceptance Criteria

1. THE Deterministic_Extractor SHALL extract typed Requirement entries for file counts, filenames, slide or page counts, dimensions, citations, test commands, browser outcomes, and explicit prohibitions.
2. THE Kageha_Agent SHALL preserve compile_requirements() as a compatibility adapter returning its current dictionary shape.
3. IF the Deterministic_Extractor finds two explicit requirements that contradict each other, THEN THE Deterministic_Extractor SHALL mark both requirements as unresolved rather than selecting one silently.
4. WHILE two explicit requirements remain marked as contradictory and unresolved, THE Contract_Compiler SHALL continuously enforce that unresolved status and SHALL prevent any later step from marking either requirement resolved without explicit user clarification.

### Requirement 7: Semantic contract completion (REL-012)

**User Story:** As a platform developer, I want the planner model to add semantic criteria on top of the deterministic draft, so that subjective or implied requirements are captured without weakening explicit ones.

#### Acceptance Criteria

1. WHEN a nontrivial executable task is compiled, THE Semantic_Completion_Service SHALL send the deterministic draft contract to the planner model using structured output.
2. THE Semantic_Completion_Service SHALL allow the planner model to add semantic SuccessCriterion or Constraint entries to the draft contract.
3. THE Semantic_Completion_Service SHALL reject a planner model result that weakens or deletes an explicit SuccessCriterion or Constraint.
4. WHEN the Semantic_Completion_Service receives a planner model result, THE Semantic_Completion_Service SHALL validate criterion IDs, dependency IDs, verifier availability, and budget values before accepting that result.
5. IF the planner model call fails or the returned schema is invalid, THEN THE Contract_Compiler SHALL execute the turn using the deterministic contract without blocking the turn.
6. IF the Semantic_Completion_Service rejects a planner model result for a reason other than a planner model call failure or an invalid schema, such as an invalid criterion ID, an invalid dependency ID, an unavailable verifier, or an invalid budget value, THEN THE Semantic_Completion_Service SHALL reject only the offending semantic additions and SHALL NOT fall back to the deterministic-only contract for that reason alone.
7. WHEN a turn is classified as trivial chat or a simple lookup or status request, THE Semantic_Completion_Service SHALL skip the planner model call for that turn.

### Requirement 8: Persist and expose contracts (REL-013)

**User Story:** As a platform developer, I want TaskContracts persisted and exposed through existing events, so that contracts survive resume, replay, and inspection.

#### Acceptance Criteria

1. THE Runtime_Store SHALL persist a TaskContract keyed by session ID and turn ID using additive table creation.
2. WHEN a TaskContract is accepted or planned, THE Kageha_Agent SHALL include the contract version and criterion summaries in the corresponding accepted or planned event.
3. WHEN a session is replayed or resumed, THE Runtime_Store SHALL restore the TaskContract associated with each turn.
4. WHEN the Runtime_Store opens an existing database that lacks TaskContract tables, THE Runtime_Store SHALL migrate that database automatically through additive table creation.

### Requirement 9: The evidence ledger (REL-020)

**User Story:** As a platform developer, I want an immutable evidence ledger, so that every criterion outcome can be traced to concrete proof.

#### Acceptance Criteria

1. THE Evidence_Ledger SHALL persist EvidenceRecord entries as immutable records linked to session ID, turn ID, criterion ID, tool attempt, and artifact where applicable.
2. THE EvidenceRecord SHALL record source type, source reference, timestamp, digest, EvidenceCertainty, producer, metadata, and a reproducible probe where available.
3. WHILE a verifier has not explicitly confirmed that evidence from a previous turn remains valid, THE VerificationEngine SHALL treat that evidence as context rather than fresh proof, even when the previous turn occurred within the same session.
4. WHEN an EvidenceRecord is persisted, THE Evidence_Ledger SHALL redact secrets from that EvidenceRecord before persistence.

### Requirement 10: Convert runtime observations into evidence (REL-021)

**User Story:** As a platform developer, I want runtime tool observations converted into structured evidence, so that criterion verdicts rest on reproducible proof rather than raw tool output.

#### Acceptance Criteria

1. WHEN an artifact validator runs, THE artifact validator SHALL emit file digest and structural EvidenceRecord entries.
2. WHEN a command tool runs, THE command tool SHALL emit an EvidenceRecord containing the command, working-directory identity, exit status, and a bounded output digest.
3. WHEN a browser or computer mutation completes, THE Kageha_Agent SHALL require a post-action reading or state probe before recording that mutation as evidence.
4. WHEN research evidence is produced, THE Kageha_Agent SHALL record the retrieved URL, retrieval time, and claim association in the EvidenceRecord.
5. IF a verifier does not accept a given tool output as proof, THEN THE VerificationEngine SHALL treat that tool output alone as insufficient for criterion proof.
6. WHERE more than one verifier evaluates the same tool output and the verifiers disagree, THE VerificationEngine SHALL accept that tool output as proof only when the configured acceptance threshold of verifiers, such as a majority, accepts it.

### Requirement 11: Preserve compatibility evidence (REL-022)

**User Story:** As a WebUI or event consumer, I want existing string evidence fields to keep working, so that current integrations continue to function.

#### Acceptance Criteria

1. THE Kageha_Agent SHALL derive existing string evidence fields from structured EvidenceRecord entries as the only compatibility mechanism for populating those fields.
2. THE Kageha_Agent SHALL keep existing event consumers and WebUI evidence rendering functional.
3. THE Kageha_Agent SHALL expose structured evidence through additive event payload fields.

### Requirement 12: One VerificationEngine as sole authority (REL-030)

**User Story:** As a platform developer, I want one VerificationEngine as the sole authority for criterion verdicts, so that verification decisions are consistent and cannot be bypassed.

#### Acceptance Criteria

1. THE VerificationEngine SHALL be the sole authority producing a CriterionVerdict for each SuccessCriterion.
2. THE VerificationEngine SHALL adapt the existing runtime validators, semantic verifier, and specialized checks behind a common verifier interface.
3. THE Kageha_Agent SHALL retire production use of the standalone enhanced-verifier orchestration while reusing its deterministic checks inside the VerificationEngine.
4. WHEN the VerificationEngine evaluates a SuccessCriterion, THE VerificationEngine SHALL apply deterministic postcondition checks, then artifact or function checks, then semantic judgment, in that order, and THE VerificationEngine SHALL require every one of those three check stages to produce a definitive result for that criterion regardless of the outcome of an earlier stage.
5. IF a deterministic postcondition check fails for a SuccessCriterion, THEN THE VerificationEngine SHALL record a failed CriterionVerdict for that criterion regardless of any semantic judgment result.

### Requirement 13: Move deterministic validation inside the controller (REL-031)

**User Story:** As a platform developer, I want deterministic validation running inside the controller loop, so that failures trigger repair before the run ends rather than being reported after the fact.

#### Acceptance Criteria

1. WHEN the LoopController reaches a plan milestone, THE VerificationEngine SHALL run verification for the criteria associated with that milestone.
2. WHEN the executor claims task completion, THE VerificationEngine SHALL run verification for all required SuccessCriterion entries.
3. WHEN a required SuccessCriterion fails verification, THE VerificationEngine SHALL convert that failure into a VerificationDefect and SHALL guarantee that the VerificationDefect is fed into the existing repair or replan control path.
4. WHEN a targeted repair completes, THE VerificationEngine SHALL re-run only the criteria affected by that repair.
5. WHEN the LoopController reports task completion, THE VerificationEngine SHALL perform one final complete verification covering all required SuccessCriterion entries before that completion is reported as success.
6. THE runtime engine SHALL consume the LoopController's final VerificationReport instead of performing a second authoritative validation after the LoopController returns.

### Requirement 14: Completion semantics (REL-032)

**User Story:** As a user of Kageha, I want success to require verified evidence for every required criterion, so that a task cannot be reported successful when it silently fails.

#### Acceptance Criteria

1. THE VerificationEngine SHALL report task success only when every required SuccessCriterion passes with accepted evidence; an optional SuccessCriterion may pass without accepted evidence.
2. IF a required SuccessCriterion remains unresolved, THEN THE VerificationEngine SHALL assign that criterion a blocked or failed CriterionVerdict and SHALL exclude the task from a successful outcome.
3. WHEN an optional SuccessCriterion fails, THE VerificationEngine SHALL include that failure in the final response without excluding the task from a successful outcome.
4. IF a requirement is impossible or contradicts another requirement and the contradiction cannot be resolved safely, THEN THE VerificationEngine SHALL escalate the task to the user.
5. IF the escalation to the user cannot be delivered, THEN THE LoopController SHALL continue the task with a warning noted in the final response, proceeding with the partial or conflicting requirements rather than halting silently.
6. WHEN the ResourceBudget is exhausted, THE LoopController SHALL preserve partial artifacts and SHALL report the task as not successful.

### Requirement 15: Remove obsolete split-path behavior (REL-033)

**User Story:** As a platform developer, I want the old duplicated pass/fail logic removed, so that there is exactly one verification decision path.

#### Acceptance Criteria

1. THE Kageha_Agent SHALL preserve existing compatibility verification functions as thin adapters over the VerificationEngine.
2. THE runtime post-processing path SHALL derive its pass/fail outcome exclusively from the VerificationEngine's VerificationReport.
3. WHEN a session is resumed or replayed, THE Kageha_Agent SHALL emit verification events idempotently.

### Requirement 16: Extend evaluation manifests (REL-040)

**User Story:** As an evaluation engineer, I want evaluation manifests extended with contract-aware fields, so that adversarial tasks can be specified precisely and reproducibly.

#### Acceptance Criteria

1. THE Evaluation_Harness SHALL support manifest fields for contract criteria, fixtures, forbidden actions, expected terminal state, maximum cost, maximum steps, maximum time, and repetition count.
2. THE Evaluation_Harness SHALL record model identifier, harness configuration, dependency lock digest, platform, and repository commit for each evaluation run.
3. THE Evaluation_Harness SHALL preserve existing golden JSON loading.

### Requirement 17: Thirty adversarial tasks (REL-041)

**User Story:** As an evaluation engineer, I want thirty adversarial tasks covering coding, artifact, browser, research, and lifecycle scenarios, so that false success can be detected across representative failure modes.

#### Acceptance Criteria

1. THE Adversarial_Task_Suite SHALL contain six coding tasks covering tests, forbidden test modification, syntax failure, regression, partial repair, and budget exhaustion.
2. THE Adversarial_Task_Suite SHALL contain six artifact tasks covering missing files, empty files, wrong counts, invalid formats, render failure, and subjective unresolved quality.
3. THE Adversarial_Task_Suite SHALL contain six browser or computer tasks covering verified mutation, unverifiable input, stale screenshot, permission denial, partial navigation, and changed UI state.
4. THE Adversarial_Task_Suite SHALL contain six research tasks covering missing citation, unreachable citation, citation-claim mismatch, stale source, conflicting sources, and incomplete evidence.
5. THE Adversarial_Task_Suite SHALL contain six lifecycle tasks covering interruption, resume, repeated failure, contradictory requirements, impossible task, and stale prior-turn evidence.
6. THE Adversarial_Task_Suite SHALL include a known false-success trap in each of the thirty tasks.

### Requirement 18: Repeated evaluation and comparison (REL-042)

**User Story:** As an evaluation engineer, I want each adversarial task run three times per configuration with results stored and comparable, so that reliability trends can be tracked over time.

#### Acceptance Criteria

1. THE Evaluation_Harness SHALL run each Adversarial_Task_Suite task exactly three times per configuration, and THE Evaluation_Harness SHALL NOT run additional iterations beyond three for that configuration.
2. THE Evaluation_Harness SHALL report verified success, false success, unresolved outcome, recovery after failure, cost, latency, steps, and tool calls for each run.
3. THE Evaluation_Harness SHALL store individual run results and aggregate summaries in the existing Benchmark_Storage.
4. THE Evaluation_CLI SHALL provide run, compare, and inspect commands.

### Requirement 19: Strict release gates (REL-043)

**User Story:** As a release engineer, I want strict release gates enforced before merge, so that the reliability spine cannot regress the false-success guarantee.

#### Acceptance Criteria

1. THE Adversarial_Task_Suite SHALL produce zero false-success outcomes across all ninety focused runs before release.
2. THE permission-denial tasks within the Adversarial_Task_Suite SHALL cause zero driver mutations.
3. THE non-model fixtures within the Adversarial_Task_Suite SHALL achieve at least 95 percent deterministic reproducibility.
4. THE Evaluation_Harness SHALL show no regression against existing golden tasks.
5. THE Full_Qualification_Command SHALL pass three consecutive times before release.

### Requirement 20: Reconcile architecture documentation (REL-050)

**User Story:** As a new contributor, I want the architecture documentation to reflect the current system accurately, so that I can trust the documented behavior.

#### Acceptance Criteria

1. THE Architecture_Documentation SHALL replace stale claims that verifier agents, specs, tracing, and replay are absent.
2. THE Architecture_Documentation SHALL document which components are integrated versus experimental.
3. THE Architecture_Documentation SHALL document the contract, evidence, and verification lifecycle and the qualification commands.

### Requirement 21: Migration and operator guidance (REL-051)

**User Story:** As an operator upgrading an existing deployment, I want migration and troubleshooting guidance, so that I can upgrade safely and diagnose failures.

#### Acceptance Criteria

1. THE Migration_Guide SHALL explain the additive database migration, old-session behavior, and verification event additions.
2. THE Migration_Guide SHALL document failure troubleshooting steps for the new verification spine.
3. THE Migration_Guide SHALL document that the new verifier is a direct replacement for the prior verification path.
