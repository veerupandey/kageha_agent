# Migration Guide: Reliability Spine (schema v1 → v2)

This guide covers upgrading an existing Kageha deployment past the reliability-spine
milestone: what changed in the database, what happens to sessions created before the
change, and how to diagnose new verification-related failures.

## Database migration

`RuntimeStore._ensure_schema()` bumps `SCHEMA_VERSION` from `1` to `2`. On an existing
database at schema version `1`, this is a **dedicated, additive, non-destructive** path:
it runs `CREATE TABLE IF NOT EXISTS` for two new tables and nothing else.

New tables:

- `task_contracts` — one row per `(session_id, turn_id)`, storing the serialized
  `TaskContract` JSON. Upserted via `RuntimeStore.put_task_contract()`.
- `evidence_records` — append-only `EvidenceRecord` rows, indexed by
  `(session_id, turn_id, criterion_id)`. There is no `UPDATE`/`DELETE` path — the ledger is
  immutable by construction, not just by convention.

No existing table (`sessions`, `turns`, `events`, `tool_attempts`, etc.) is altered, and no
existing row is touched. The migration runs automatically the next time `RuntimeStore` opens
that database file — no manual step is required.

The destructive "rebuild when schema diverges" path in `_ensure_schema()` only fires when the
on-disk `user_version` is neither `0` (empty), `1` (the known pre-milestone version), nor the
current `SCHEMA_VERSION`. A real `SCHEMA_VERSION=1` database from before this milestone will
always take the additive path above, never the destructive rebuild.

## Old-session behavior

A session or turn created before this milestone simply has **no row** in `task_contracts` or
`evidence_records` for it. This is the normal, expected state — not an error:

- `RuntimeStore.get_task_contract(session_id, turn_id)` returns `None` for such a turn.
- `RunResult.contract`, `.evidence`, and `.report` are `None`/`()`/`None` for such a turn.
- Verification for such a turn continues to run through the pre-existing
  `validate_result()` deterministic-registry + semantic-verifier path in
  `AgentRuntime._execute` — unchanged from before this milestone.

If a stored session's underlying `events`/`turns` JSON is genuinely corrupted (not merely
missing a `task_contracts` row), loading that session still fails closed and requires
re-creating the session — Kageha does not attempt partial recovery of corrupted turn state.

## New verification events

The `VERIFICATION` event payload gains additive fields when a `VerificationReport` is
attached to a turn's `RunResult`:

- `report` — `{"scope", "success", "verdicts": [{"criterion_id", "status", "stage_results"}]}`
- `evidence` — `[{"criterion_id", "source", "certainty", "digest"}, ...]`

Existing consumers reading `status`, `validated`, `deterministic_passed`, `checks`, and
`defects` from that same event continue to work unchanged — these fields are never removed,
only supplemented.

## Troubleshooting

**A required criterion is stuck `UNRESOLVED`.**
This means the `Deterministic_Extractor` found two explicit requirements in the request that
conflict (same `RequirementKind`, different `value`) — for example two different exact slide
counts. Both are marked `UNRESOLVED` with mutual `contradicts` references rather than one
being silently preferred. This stays unresolved until a new turn contains explicit
clarification text that the extractor recognizes as resolving that specific pair. Check
`Requirement.contradicts` on the stored `TaskContract` to see which two entries conflict.

**Evidence looks `stale` in a verification report.**
`EvidenceLedger.with_staleness()` marks any `EvidenceRecord` whose `turn_id` differs from the
turn currently being verified as `certainty=STALE`, unless a fresh record in the current turn
references it via `metadata={"reconfirms": <old_id>}`. This is intentional — proof from a
previous turn is context, not fresh confirmation, even within the same session. If a
criterion should be able to reuse prior-turn evidence, the verifier stage producing the new
check must explicitly reconfirm it.

**A turn has no `TaskContract` even though it looks like a substantial task.**
Contracts are compiled automatically at turn intake, but three cases legitimately produce
no contract: (1) the turn ran in `followup` mode (short chat replies skip compilation to
avoid planner latency), (2) `ContractCompiler` classified the objective as trivial or a
simple read-only lookup, or (3) the turn ran without a runtime journal (e.g. a direct
`LoopController` construction in tests, or the eval harness's `controller_factory` path).
In all three cases `RunResult.report` is `None` and the turn is validated by the
pre-existing `validate_result()` + semantic-verifier path — verification was not skipped,
it just took the older route. Check the turn's events for `contract_compiled` (success),
`contract_compile_error` (compilation failed and was swallowed so the turn could proceed),
or neither (compilation was intentionally skipped).

**The adversarial suite reports a `false_success` outcome.**
Inspect the specific run via `kageha eval inspect <run_id>` and cross-reference the task's
`false_success_trap` field in `kageha/eval/adversarial_tasks/<category>.json` — it documents
exactly how a naive verifier could be fooled on that task. A `false_success` means the
detection mechanism worked (it caught the trap); it does not mean the trap itself failed.

## This is a direct replacement, not a shadow path

The reliability spine is designed as a direct replacement of the prior completion-checking
logic, not an opt-in feature. There is no feature flag governing whether `VerificationEngine`
or `EvidenceLedger` runs — once a turn has a compiled `TaskContract`, verification for that
turn goes through the new spine, and contracts are compiled automatically at turn intake.

Two things remain true and are worth understanding rather than mistaking for a flag:

- Turns that legitimately have no contract (followup mode, trivial/lookup classification,
  or no runtime journal) fall back to the pre-existing verification path. That is a
  classification outcome, not a toggle between two supported implementations.
- `verify_with_defects` still runs alongside the `VerificationEngine` on each gate. Both
  feed one merged defect list and one control path, but the older verifier has not been
  deleted. See `docs/ARCHITECTURE.md` → Known limitations.
