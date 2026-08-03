# Anti-Loop Patterns: Comprehensive Analysis

> Synthesized from three research tracks: production agent architectures (Manus/HyperAgent), industry implementations (Codex/Devin/Claude Code/Cursor), and academic loop-detection patterns. Grounded in the Kageha codebase at `src/kageha/loop/`.

---

## 1. How Production Agents Handle Loops

### 1.1 Manus

Manus pioneered the **structured executive memory** pattern: a persistent `TaskState` object that survives transcript compaction and tracks all failures, decisions, and stage progress. Key contributions to the field:

- **Failure classification taxonomy**: Every tool call failure is categorized (invalid_args, missing_dep, provider_error, bad_output, timeout, tool_error, access_blocked, reasoning_error) and the category drives the escalation path.
- **Plan-as-state-machine**: Stages have explicit statuses (PENDING → ACTIVE → DONE/BLOCKED) with dependency tracking, allowing parallel execution and targeted replanning of individual stages without resetting the whole plan.
- **Monitor-as-critic**: A separate LLM call (not the execution model) evaluates plan alignment, preventing self-agreement bias.

### 1.2 HyperAgent

HyperAgent's core contribution is **multi-level circuit breakers**:

- Per-tool breakers that fire on repeated failures with configurable warn/block thresholds.
- Global breakers that track overall execution health across all tools.
- Post-compaction guards that arm tighter detectors after context window resets, since the agent may lose awareness of prior failures.

The "OpenClaw-style" PostCheckpointGuard in our codebase (tool_guardrails.py line 838) directly implements this pattern: a short 4-observation window after compaction that aborts if the same (tool, args, result) triple appears 3+ times.

### 1.3 OpenAI Codex

Codex's approach emphasizes **bounded execution with hard ceilings**:

- Step limits (max_steps) and budget limits (max_usd) as non-negotiable hard stops.
- The principle that "model saying 'I'm done' has no authority without validation" — the agent cannot self-certify completion.
- Deterministic early-stop for trivial follow-ups to avoid spinning verification LLMs unnecessarily.

### 1.4 Devin

Devin introduced the **HUDDLE pattern** — a structured diagnostic mode that forces the agent to:
1. Diagnose the specific blocker
2. Propose alternative strategies (new skill, different tool, MCP reload)
3. Wait for human approval before proceeding

This prevents the failure mode where agents exhaust all options without ever surfacing the root cause to the user.

### 1.5 Claude Code

Claude Code's contributions center on **action fingerprinting with canonicalization**:

- Canonical argument hashing: Stripping volatile keys (timestamp, session_id, run_id) to detect semantic repetition despite cosmetic differences.
- Result hashing with normalization: Parsing JSON, normalizing whitespace, and stripping guardrail suffixes to detect identical outcomes.
- The insight that detection must operate on *semantic equivalence*, not byte-level equality.

### 1.6 Cursor

Cursor-style patterns appear in the **Hermes-style** per-turn guardrails:

- Observe/refine nudges injected into tool results (not into the conversation) to steer without disrupting the agent's reasoning chain.
- Skill-learning hooks that teach the agent to avoid repeat failures through explicit "do NOT retry X" instructions in context.
- Warm → block escalation where the agent gets warnings before hard stops.

---

## 2. Anti-Loop Patterns (Categorized)

### 2.1 Action Fingerprinting

**What it detects**: The same tool call being repeated with identical or semantically-equivalent arguments.

**Implementation in Kageha** (`tool_guardrails.py`):
- `canonical_tool_args()`: Strips volatile keys (`timestamp`, `pid`, `session_id`, etc.), sorts remaining keys, and SHA-256 hashes the canonical form.
- `result_hash()`: Normalizes whitespace, strips prior guardrail guidance suffixes, JSON-canonicalizes parseable responses.
- Per-signature tracking: `_exact_failure_counts[signature]` → warn at 2, block at 4.

**Strengths**: Catches tight same-call loops immediately; low false-positive rate.
**Weaknesses**: Cannot detect loops where the agent varies arguments slightly each time (e.g., same URL with different query params), or where different tool calls all produce unhelpful results.

### 2.2 State Diff / Progress Metrics

**What it detects**: Agent is active but making no meaningful progress toward goals.

**Implementation layers**:
1. **Goal progress tracking** (`TaskState.progress()`): Numeric progress score that must increase over time.
2. **Stagnation counter** (`controller.py`): Increments when `progress <= last_progress` AND no tool calls. Fires `NO_PROGRESS` at 5 stagnant steps.
3. **Defect signature fingerprinting** (`controller.py`, `_defect_signature()`): Creates a stable fingerprint of open defects. If the verifier returns the same defects AND progress hasn't improved → `same_repair_streak` increments.
4. **Stagnant-with-tools** (`tool_guardrails.py`): Tracks whether ANY call in the rolling window produces a genuinely new `(tool, args_hash, result_hash)` triple via `_seen_triples` set. If N consecutive calls all produce already-seen triples → fires.
5. **MonitorVerdict** (`monitor.py`): Independent LLM critic checking plan drift — catches the case where the agent is "doing things" that don't align with the plan.

**Strengths**: Catches semantic stagnation even when individual tool calls vary; detects repair loops.
**Weaknesses**: Requires a verifier/critic LLM call (adds latency and cost); progress metrics need careful calibration.

### 2.3 Circuit Breakers

**What they enforce**: Hard limits that prevent infinite resource consumption regardless of detection quality.

**Implementation**:
| Breaker | Scope | Warn | Halt | Reset Condition |
|---------|-------|------|------|-----------------|
| Exact failure | Per-signature | 2 | 4 | Success on same tool |
| Same tool failure | Per-tool-name | 3 | 6 | Success on same tool |
| Idempotent no-progress | Per-signature (read-only tools) | 2 | 4 | Different result hash |
| Ping-pong | Alternating pair keys | 4 | 6 | New tool introduced |
| Global breaker | Rolling window (20 entries) | 8 | 12 | — |
| Stagnant-with-tools | Consecutive no-new-triple | 6 | 10 | New triple observed |
| PostCheckpointGuard | 4-call window post-compaction | — | 3 | Window expires |
| Max steps | Whole execution | — | 40 | — |
| Budget | Whole execution | — | $2.00 | — |
| No progress | Stagnant steps | — | 5 | Any progress |
| Same repair | Repair streak | — | 4 | New defect signature |
| Total repair | Cumulative repairs | — | 8 | — |

**Strengths**: Guarantees termination; protects against all failure modes including unknown ones.
**Weaknesses**: Can terminate productive work if thresholds are too aggressive; requires tuning.

### 2.4 Escalation Ladders (see Section 3)

---

## 3. Escalation Ladders

### 3.1 The Kageha Escalation Model

The `decide_control()` function maps detected failures to recovery strategies:

```
CONTINUE → RETRY → SWITCH_TOOL → REPAIR → REPLAN_STAGE → REPLAN_TASK → HUDDLE → ASK_USER → STOP
```

| Decision | Trigger | Action Taken |
|----------|---------|-------------|
| **CONTINUE** | No issues detected | Keep executing current stage |
| **RETRY** | `INVALID_ARGS` failure | "Fix arguments, retry once" — single chance |
| **SWITCH_TOOL** | `PROVIDER_ERROR` or `TIMEOUT` + anti_loop_hit | Switch to different tool/model/query |
| **REPAIR** | Verifier status="repair" | Inject repair list (severity, artifact, problem, evidence, instructions) |
| **REPLAN_STAGE** | Verifier status="fail" OR anti_loop_hit on non-reasoning failures | Reset current stage to ACTIVE, increment attempts |
| **REPLAN_TASK** | Repeated `REASONING` failures | Reset ALL non-done stages to PENDING, reactivate first incomplete |
| **HUDDLE** | `TOOL_ERROR`/`ACCESS_BLOCKED`/`BAD_OUTPUT`/`MISSING_DEP`/`UNKNOWN` failing 2+ times | Structured diagnose → invent → HITL protocol |
| **ASK_USER** | Stage blocked OR `same_repair_streak ≥ 4` OR `total_repair_cycles ≥ 8` | Surface problem to human |
| **STOP** | Budget/step limits OR all goals validated | Terminate execution |

### 3.2 Industry Comparison

| System | Retry | Repair | Tool Switch | Replan | Human Escalation |
|--------|-------|--------|-------------|--------|-----------------|
| **Kageha** | Fix args, single retry | Verifier-driven defect list | Steering: "pick different tool" | Stage or full-task reset | HUDDLE + ASK_USER |
| **Manus** | N/A (subsumed into repair) | External validator | Automatic tool rotation | Plan versioning | User approval gate |
| **Codex** | Retry with same params | Self-repair loop (capped) | N/A | Full restart from scratch | Hard abort → user |
| **Devin** | Retry with backoff | Multi-pass repair with diff | Suggest alternatives | HITL replan session | Always-on human oversight |
| **Claude Code** | Silent retry (1x) | Lint/test feedback loop | N/A | User must intervene | "I'm stuck" message |
| **Cursor** | Auto-retry on transient errors | Apply fix suggestions | Tab completion alternatives | User re-prompts | Inline error display |

### 3.3 Key Design Principle: Warn Before Block

All production systems share a common pattern: **warnings precede blocks**. The agent receives steering/guidance at the warn threshold and only gets hard-stopped at the block threshold. This gives the model a chance to self-correct before being forcibly interrupted, preserving its reasoning continuity.

---

## 4. Our Specific Problem: The `web_fetch` Loop

### 4.1 Problem Description

The Kageha agent enters a degenerate loop when:
1. A task requires image URLs from a web page
2. The agent calls `web_fetch` on the page URL
3. `web_fetch` returns text content (HTML stripped to text) — no image URLs available in extraction
4. The agent interprets the lack of image URLs as a failure
5. The agent retries `web_fetch` with the same URL (or slight parameter variations) hoping for different content
6. The loop persists because the fundamental limitation is architectural: text extraction cannot provide image URLs

### 4.2 Why Current Detection Is Slow to Fire

The existing detectors have gaps for this specific pattern:

1. **Exact failure detector**: Doesn't fire because the call "succeeds" (returns content, not an error). The failure is semantic (unhelpful content), not structural (error response).

2. **Idempotent no-progress detector**: DOES fire eventually (web_fetch is in IDEMPOTENT_TOOL_NAMES), but only when args are identical. If the agent varies params (mode, search phrase, etc.), each call gets a different `args_hash`, creating a new signature that starts from count=0.

3. **Stagnant-with-tools detector**: Eventually catches it (threshold 6-10 calls) because the result content is substantively similar, but this is expensive — 6-10 wasted API calls before intervention.

4. **Global circuit breaker**: Too high a threshold (8-12) for this specific case.

5. **Result hash variation**: If the agent uses selective mode with different search phrases, it gets genuinely different text snippets back — different `result_hash` values — defeating deduplication.

### 4.3 Root Cause

The root cause is **tool capability mismatch**: the agent believes `web_fetch` can extract image URLs (a reasonable assumption), but the text extraction mode fundamentally cannot provide them. The agent has no way to know this limitation is permanent rather than a parameter issue, so it keeps trying different parameters.

---

## 5. Recommended Solution

### 5.1 Detect Same-URL Repeated Fetches Earlier

**Proposed: URL-level deduplication in ToolCallGuardrailController**

Add a **URL-keyed tracker** alongside the existing signature-keyed trackers:

```python
# In ToolCallGuardrailController.__init__():
self._url_fetch_tracker: dict[str, int] = {}  # url → call_count

# In after_call(), specifically for web_fetch/browser tools:
if tool_name in ("web_fetch", "web_search"):
    url = self._extract_url(args)
    if url:
        self._url_fetch_tracker[url] = self._url_fetch_tracker.get(url, 0) + 1
        count = self._url_fetch_tracker[url]
        if count >= 3:  # Much tighter than the generic 6-10 threshold
            return HaltResult(
                detector="same_url_repeated",
                message=f"URL '{url}' fetched {count} times. The content available "
                        f"via text extraction will not change. Consider: browser_snapshot "
                        f"for visual content, or a different URL/API endpoint.",
                steer="switch_tool"
            )
        elif count >= 2:
            return WarnResult(
                detector="same_url_repeated",
                guidance=f"This URL was already fetched. If you need image URLs or "
                         f"visual content, web_fetch text extraction cannot provide them. "
                         f"Try: browser_snapshot, image search API, or page source inspection."
            )
```

**Key design decisions**:
- Threshold of 2 (warn) / 3 (block) vs the generic 6/10 — because for the same URL, text content is deterministic regardless of parameters.
- URL extraction ignores fragment identifiers but preserves query params for different-endpoint detection.
- Fires on "success" responses, not just failures — addressing the specific gap.

### 5.2 Force Tool-Switch or Replan After N Failed Attempts

**Proposed: Enhanced `decide_control()` routing for `BAD_OUTPUT` with tool hint**

When the URL-level detector fires and sets `steer="switch_tool"`, the existing `SWITCH_TOOL` path activates. Enhance the steering message with **tool-specific alternatives**:

```python
# In adaptive.py, extend switch_tool_steering_message():
def switch_tool_steering_message(state: TaskState, ...) -> str:
    base = f"""[adaptive steer — SWITCH_TOOL]
Reason: {reason}
Do NOT retry the same failing tool with the same arguments."""

    # Add tool-specific alternatives based on what was being attempted
    alternatives = _suggest_alternatives(state.last_action, state.last_goal)
    if alternatives:
        base += f"\n\nRecommended alternatives:\n{alternatives}"

    return base

def _suggest_alternatives(action: str, goal: str) -> str:
    """Context-aware tool suggestions based on what the agent was trying to do."""
    suggestions = {
        "web_fetch": {
            "image_urls": (
                "- browser_snapshot: Renders the page and captures visual content\n"
                "- web_search with 'site:' + 'filetype:' operators for direct image URLs\n"
                "- Inspect page source via web_fetch with mode='full' and grep for <img> tags\n"
                "- Use a dedicated image search API if available"
            ),
            "dynamic_content": (
                "- browser_snapshot: Captures JavaScript-rendered content\n"
                "- browser_click + browser_snapshot: Interact then capture\n"
                "- Look for an API endpoint that serves the data as JSON"
            ),
            "default": (
                "- Try a different URL that may have the information\n"
                "- Use web_search to find an alternative source\n"
                "- browser_snapshot for visual/rendered content"
            ),
        }
    }
    # Match based on action and goal keywords
    ...
```

**Escalation timeline** (from first web_fetch to forced switch):
1. Call 1: Normal execution
2. Call 2 (same URL): Warn — "URL already fetched, content won't change"
3. Call 3 (same URL): Block + SWITCH_TOOL — "Must use different tool" with specific alternatives
4. If agent still stuck after switch: REPLAN_STAGE fires (existing mechanism)
5. If replan fails: HUDDLE → human escalation (existing mechanism)

### 5.3 Give the Agent a 'Hint' About Alternative Tools

**Proposed: Capability-aware tool metadata injection**

Add tool capability annotations that the guardrail system can reference:

```python
# Tool registry metadata (in tool definitions):
TOOL_CAPABILITIES = {
    "web_fetch": {
        "can_do": ["extract_text", "extract_links", "search_within_page"],
        "cannot_do": ["extract_images", "render_javascript", "interact_with_page"],
        "alternatives_for": {
            "extract_images": ["browser_snapshot", "web_search filetype:jpg/png"],
            "render_javascript": ["browser_snapshot", "browser_click"],
            "interact_with_page": ["browser_click", "browser_type"],
        }
    },
    "browser_snapshot": {
        "can_do": ["render_page", "capture_visual", "see_images", "read_dynamic_content"],
        "cannot_do": ["extract_structured_data", "follow_links_automatically"],
        ...
    }
}
```

When the same-URL detector fires, the halt message includes capability context:

```
HALT: URL 'https://example.com/gallery' fetched 3 times via web_fetch.

web_fetch CANNOT: extract image URLs, render JavaScript, see visual content.
web_fetch CAN: extract text, extract hyperlinks, search within page text.

For image URLs, use instead:
  1. browser_snapshot — renders the page visually, you can see image elements
  2. web_fetch mode='full' — get raw HTML, then parse <img src="..."> tags yourself
  3. web_search "site:example.com" filetype:jpg — find image URLs via search index

Do NOT call web_fetch on this URL again with different parameters.
The text extraction result is deterministic for a given URL.
```

### 5.4 Implementation Summary

| Component | File | Change |
|-----------|------|--------|
| URL-level tracker | `tool_guardrails.py` | New `_url_fetch_tracker` dict + detection in `after_call()` |
| Capability metadata | `tool_registry.py` (new) or tool definition files | Static capability annotations per tool |
| Enhanced steering | `adaptive.py` | `_suggest_alternatives()` function keyed by tool + goal |
| Tighter thresholds for known-deterministic tools | `tool_guardrails.py` | Override `no_progress_warn_after` for web_fetch specifically (2 instead of 6) |

### 5.5 Expected Behavior After Fix

**Before** (current behavior):
```
Turn 1: web_fetch(url, mode=truncated) → text content (no images)
Turn 2: web_fetch(url, mode=full) → text content (no images)
Turn 3: web_fetch(url, mode=selective, search="image") → text snippet (no images)
Turn 4: web_fetch(url, mode=selective, search="src") → text snippet (no images)
Turn 5: web_fetch(url, mode=selective, search="jpg") → text snippet (no images)
Turn 6: [stagnant-with-tools finally fires at generic threshold]
... 4-6 more wasted calls before hard halt
```

**After** (with fix):
```
Turn 1: web_fetch(url, mode=truncated) → text content (no images)
Turn 2: web_fetch(url, mode=full) → text content + WARNING: "URL already fetched.
         If you need image URLs, web_fetch text extraction cannot provide them.
         Try: browser_snapshot, or parse <img> tags from full HTML."
Turn 3: [If agent ignores warning and retries] → HALT + SWITCH_TOOL with
         capability-aware alternatives. Agent is FORCED to use different tool.
Turn 4: browser_snapshot(url) → visual render with image elements visible
         OR web_fetch(url, mode=full) + manual <img> tag parsing
```

**Cost savings**: 4-8 fewer wasted LLM + tool calls per occurrence. At typical token costs, this saves $0.05-0.20 per stuck episode.

---

## Appendix A: Detection Mechanism Reference

### Detector Priority (highest to lowest)

1. **PostCheckpointGuard** — fires immediately post-compaction (3 repeats in 4 calls)
2. **Exact failure repetition** — same tool + same args failing (2 warn, 4 block)
3. **URL-level tracker** [PROPOSED] — same URL regardless of params (2 warn, 3 block)
4. **Idempotent no-progress** — same result from read-only tool (2 warn, 4 block)
5. **Same-tool failure** — any failure from same tool name (3 warn, 6 halt)
6. **Ping-pong** — alternating A,B,A,B pattern (4 warn, 6 halt)
7. **Stagnant-with-tools** — no new triples observed (6 warn, 10 halt)
8. **Global circuit breaker** — identical triple in rolling window (8 warn, 12 halt)
9. **StopRules** — max_steps (40), budget ($2), no_progress (5), repair caps (4/8)

### Key Architectural Principles

1. **Detect at the semantic level**: Hash normalization catches loops despite cosmetic variation.
2. **Warn before block**: Give the model a chance to self-correct.
3. **Escalate, don't abort**: Retry → repair → switch → replan → huddle → human.
4. **Structured memory survives compaction**: TaskState persists across context resets.
5. **Separation of concerns**: Execution model ≠ verification model ≠ monitor model.
6. **Hard ceilings are non-negotiable**: Budget and step limits always apply.
7. **The model cannot self-certify**: Completion requires external validation.

---

## Appendix B: Files Referenced

| File | Role |
|------|------|
| `src/kageha/loop/tool_guardrails.py` | Per-turn circuit breakers, idempotent detection, PostCheckpointGuard |
| `src/kageha/loop/adaptive.py` | `decide_control()`, steering messages, escalation routing |
| `src/kageha/loop/task_state.py` | FailureKind, anti_loop_hit(), ControlDecision enum |
| `src/kageha/loop/stop_rules.py` | Hard limits: max_steps, budget, no_progress, repair caps |
| `src/kageha/loop/controller.py` | Main loop orchestration, stagnation tracking, repair streaks |
| `src/kageha/loop/monitor.py` | Independent plan-drift critic (separate LLM call) |
| `docs/research/AGENT_ENGINEERING_PATTERNS.md` | Foundational patterns research |
