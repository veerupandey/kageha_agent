# Agent Engineering Patterns: Research & Gap Analysis

**Branch:** `research/agent-engineering-patterns`
**Date:** 2026-07-29
**Scope:** Harness Engineering, Graph Engineering, Loop Engineering — learning from Kiro IDE/CLI and Cursor

---

## Table of Contents

1. [The Five Layers of AI Engineering](#the-five-layers-of-ai-engineering)
2. [Harness Engineering](#1-harness-engineering)
3. [Loop Engineering](#2-loop-engineering)
4. [Graph Engineering](#3-graph-engineering)
5. [How Kiro IDE/CLI Implements These](#4-kiro-idecli-implementation)
6. [How Cursor Implements These](#5-cursor-implementation)
7. [Kageha Framework: Current State](#6-kageha-framework-current-state)
8. [Gap Analysis](#7-gap-analysis)
9. [Recommendations & Roadmap](#8-recommendations--roadmap)

---

## The Five Layers of AI Engineering

The field has evolved through five cumulative layers (each builds on the previous):

| # | Layer | What You Engineer | Core Question |
|---|-------|-------------------|---------------|
| 1 | **Prompt** | The single request | Am I asking well? |
| 2 | **Context** | What the model sees | Does it have the right information? |
| 3 | **Harness** | Tools, memory, scaffolding | Can it act on the world and remember? |
| 4 | **Loop** | The repeat cycle one agent runs | When does it check its work and stop? |
| 5 | **Graph** | Coordination between many agents/steps | Who does what, in what order, sharing what state? |

**Key insight:** Each layer only works if the one below it is solid. A graph of weak loops
is a weak system. Master the loop before wiring the graph.

---


## 1. Harness Engineering

> "Agent = Model + Harness. The harness is everything else — tool schemas, permission models,
> context lifecycle management, feedback loops, sandboxing, documentation infrastructure,
> architectural invariants." — OpenAI (Feb 2026)

### Definition

Harness engineering is the discipline of designing the systems, constraints, and feedback loops
that wrap around AI agents to make them reliable in production. It's the architecture AROUND the
model — not the model itself.

### The Formula

```
Agent = Model + Harness
```

The harness gives the model: state, tools, feedback, and constraints. Strip away the harness
and the model is just a function that maps tokens to tokens.

### Six Core Components

1. **Context** — What information the model receives (RAG, file content, conversation history)
2. **Tools** — What actions the model can take (file ops, shell, search, APIs)
3. **Orchestration** — How tool calls are dispatched, parallelized, and sequenced
4. **State** — What persists across turns (memory, checkpoints, working notes)
5. **Evaluation** — How work is verified (linters, tests, separate verifier agents)
6. **Recovery** — What happens on failure (retry, replan, escalate, circuit break)

### Key Principles (from OpenAI's Codex team)

- **Constraints > Instructions**: "No TODOs, no partial implementations" works better than
  "remember to finish implementations"
- **Treat the model like a brilliant new hire**: It knows engineering but not your codebase
- **Spend more time on initial instructions**: Orders of magnitude more compute amplifies
  suboptimal instructions
- **The harness matters more than the prompt**: A Stanford HAI study found prompt refinement
  beyond baseline improved quality by <3%, while harness-level changes (retrieval, tool access,
  structured validation) improved quality by 28-47%

### Production Patterns

| Pattern | Description | Example |
|---------|-------------|---------|
| Tool Schema Design | JSON schemas that guide model behavior | Cursor's per-model tool tuning |
| Permission Ladder | Graduated trust levels for tool access | Codex 3-tier: once/session/full |
| Sandbox Isolation | Restricting agent filesystem/network access | Docker, seatbelt, bwrap |
| Approval Gates | Human-in-the-loop for risky operations | Kageha's ApprovalGate |
| Feedback Injection | Structured error messages back to model | Linter output → next prompt |
| Observability | Logging all decisions for debugging | OTEL spans, event journals |

---


## 2. Loop Engineering

> "Loop engineering is the practice of designing the execution environment around an AI agent.
> That includes the triggers, stopping conditions, feedback mechanisms, and failure controls
> that determine how the agent runs." — Data Science Dojo (2026)

### The 10 Design Patterns

#### Tier 1: Foundational (every builder needs these)

**Pattern 1: ReAct Loop**
- Perceive → Reason → Plan → Act → Observe → repeat
- The base pattern every major AI lab converged on
- One agent cycling through observation and action

**Pattern 2: Reflection Loop**
- Agent generates output, then critiques it before delivering
- Simplest self-correction; relies on agent's own judgment
- Limitation: agent can "pass its own work" by agreeing with itself

**Pattern 3: Tool Use Loop**
- Agent calls external APIs/tools within the loop
- Most established pattern in production systems
- Foundation for all complex patterns below

**Pattern 4: Prompt Chaining**
- Output of one LLM call becomes input of the next (fixed sequence)
- High reliability and auditability, low autonomy
- Use when tasks have a known fixed order

#### Tier 2: Practitioner (real-world constraints)

**Pattern 5: Ralph Loop**
- Agent runs until an EXTERNAL validator confirms success
- Exit condition comes from deterministic checks (tests pass, type errors = 0)
- Agent cannot self-certify success
- Key design: "model claiming done has NO authority without verification"

**Pattern 6: Evaluator-Optimizer Loop**
- A SEPARATE agent evaluates the primary agent's output
- Returns structured feedback; primary revises until evaluator approves
- Critic is separate from generator — prevents self-agreement

**Pattern 7: Multi-Agent Supervisor Loop**
- Supervisor coordinates specialized workers
- Each worker runs its own internal loop on a subtask
- Supervisor manages flow, doesn't do the work itself

#### Tier 3: Production Hardening (non-negotiable for autonomous systems)

**Pattern 8: Circuit Breaker**
- Monitors progress across iterations
- If stuck (repeating errors, no measurable progress for N cycles) → trips
- Without this, stuck agents burn tokens indefinitely

**Pattern 9: Heartbeat Loop**
- Agent wakes on schedule/event, checks condition, acts if needed, sleeps
- More cost-efficient than persistent agents
- Key failure mode: overlapping heartbeats (need "cycle in progress" lock)

**Pattern 10: Bounded Execution + Context Engineering**
- Hard caps: max iterations, max token spend, max wall time
- Context controls: selecting, compressing, isolating what enters the window each step
- Multi-agent systems cost up to 15x more per session — these cap that cost

### Common Production Stack

```
Tool Use Loop (base execution)
  + Bounded Execution (hard ceiling)
  + Circuit Breaker (stagnation detection)
  + Multi-Agent Supervisor (when task exceeds single context window)
```

---


## 3. Graph Engineering

> "The single loop is where everyone starts. The single loop fails in ways that are now well
> understood. The emerging answer is not a better loop but a graph of loops — a network of
> improvement cycles that watch, feed, constrain, and correct one another."

### Definition

Graph engineering is designing the graph your agents run in: which specialized **nodes** exist,
which **edges** route work between them, and what **shared state** travels along those edges.

### Three Parts of an Agent Graph

1. **Nodes** — Units that do work (specialized agents, deterministic steps, tool calls)
2. **Edges** — Routing between nodes (sequential, conditional, fan-out, fan-in, loop-back)
3. **Shared State** — Object traveling along edges that every node reads/writes

### Key Insight: A Loop IS a Graph

A single loop is the simplest possible graph: one node with an edge back to itself. Graph
engineering doesn't replace loop engineering — it's the layer directly above it. It decides
how several loops connect.

### When You Need a Graph (vs. staying with a loop)

| Signal | Loop is Enough | Reach for a Graph |
|--------|---------------|-------------------|
| Shape of task | One job, clear finish line | Splits into distinct specialties that hand off |
| Parallelism | Steps are sequential | Need fan-out then join |
| Tools per step | Same tools throughout | Different model/toolset per step |
| Control flow | One agent can free-roam safely | Need explicit auditable routing |
| Failure isolation | Bad step just retries | One bad node shouldn't poison the rest |
| Who verifies | Agent self-checks | Dedicated reviewer node checks another's work |

### The "Slop" Test

Most tasks never need a graph. The tell: **if you can collapse your nodes back into one
agent's loop and lose nothing, you should.** A graph earns its keep only when each node does
work a single loop genuinely couldn't hold.

### Frameworks That Implement Graphs

| Framework | Owner | Key Feature |
|-----------|-------|-------------|
| LangGraph | LangChain | StateGraph with nodes, edges, shared state |
| AutoGen GraphFlow | Microsoft | Multi-agent orchestration with hand-offs |
| Google ADK | Google | Sequential/parallel/loop workflow agents |
| A2A Protocol | Google | Cross-system agent-to-agent delegation |

### Checklist Before Building a Graph

1. Try to keep it a loop first
2. Name nodes only if they're real specialties (different model, toolset, or read-only reviewer)
3. Draw edges before coding (napkin test)
4. Design shared state explicitly — state drift is how graphs rot
5. Give the reviewer node teeth (separate, read-only verifier)
6. Isolate failure (one node fails without corrupting shared state)
7. Use an existing framework (don't hand-roll the runtime)
8. Set spend cap and hard bound (a graph is many loops burning tokens)

---


## 4. Kiro IDE/CLI Implementation

### Architecture Philosophy: Spec-Driven Development

Kiro's core differentiator is that the **unit of work is a specification**, not a prompt.
The workflow:

```
Prompt → Requirements Doc → Architecture Design → Sequenced Tasks → Parallel Agent Execution
```

Each stage produces a persistent artifact that can be reviewed, edited, and validated before
the next stage proceeds. This is fundamentally different from chat-first tools.

### Key Engineering Patterns in Kiro

#### Harness

| Component | Implementation |
|-----------|---------------|
| Instructions | System prompt + Steering files (.kiro/steering/*.md) |
| Tools | Built-in + MCP servers + dynamic tool loading |
| Model routing | Role-based: Claude Sonnet for reasoning, Nova for code generation |
| Permissions | PreToolUse hooks with exit code 2 = block |
| Sandboxing | Container-based execution for untrusted operations |

#### Loop

- **Spec mode**: Requirements → Design → Tasks (iterative refinement at each stage)
- **Vibe mode**: Standard ReAct conversational loop
- **Verification**: Property-based tests catch edge cases that unit tests miss
- **Stop conditions**: Task completion validated against spec, not self-reported

#### Graph

- **Parallel sub-agents**: Tasks from spec execute as parallel agents
- **DAG orchestration**: Dependencies between tasks respected
- **Shared state**: .kiro/ directory serves as shared artifact store

### The Hook System

Kiro hooks are event-driven automations:

```json
{
  "version": "v1",
  "hooks": [{
    "name": "lint-on-save",
    "trigger": "PostFileSave",
    "matcher": "\\.(ts|js)$",
    "action": { "type": "command", "command": "eslint --fix ${file}" }
  }]
}
```

**Trigger events:** PreToolUse, PostToolUse, SessionStart, Stop, UserPromptSubmit,
PreTaskExec, PostTaskExec, PostFileCreate, PostFileSave, PostFileDelete

**Action types:** command (shell), agent (inject prompt into context)

**Exit code semantics:**
- 0 = success (stdout forwarded)
- 2 = BLOCK the action (stderr forwarded as reason)

### Steering System

Steering files provide persistent context:
- **Always included** (default): Applied to every interaction
- **Conditional** (fileMatch): Loaded when matching files are in context
- **Manual**: User explicitly provides via `#` reference

Can reference external files: `#[[file:openapi.yaml]]` — low-friction spec inclusion.

### Multi-Agent Orchestration (from aws-samples)

Kiro CLI supports multi-agent development with specialized roles:
- **Orchestrator** (frontier model: Fable 5 / Opus 5)
- **Architect** (deep reasoning: Opus 5)
- **Coder** (balanced: Sonnet 5)
- **Reviewer** (cost-optimized: Haiku 4.5)
- **DevOps, Researcher, etc.** (domain-specific roles)

DAG-style parallel delegation with AGENTS.md as the collaboration guide.

---


## 5. Cursor Implementation

### Architecture Philosophy: Agent-First IDE

Cursor treats the IDE as an **agent execution runtime**. Key architectural decisions:

### The Three-Component Harness

```
Agent Harness = Instructions + Tools + Model
```

**Critical insight**: Cursor tunes each component PER MODEL. Different models respond
differently to the same prompts. A model trained on shell workflows prefers grep over a
dedicated search tool. Another needs explicit instructions to call linters after edits.

### Key Engineering Patterns in Cursor

#### Harness

| Component | Implementation |
|-----------|---------------|
| Instructions | Per-model system prompts + .cursor/rules/ (static context) |
| Tools | File editing, codebase search, terminal, browser, grep |
| Model tuning | Instructions and tool selection customized per frontier model |
| Per-model evals | Internal eval suite benchmarks each model's harness |
| Rules | Markdown in .cursor/rules/ — checked into git, team-shared |

#### Loop

- **Plan Mode** (Shift+Tab): Research → Clarify → Plan → Approval → Build
- **Test-driven loop**: Write tests → confirm fail → implement → iterate until green
- **Review loop**: Agent generates → Review pass flags issues → Iterate
- **Hooks-based grind loop**: Stop hook returns followup_message to continue iterating
- **Debug Mode**: Generate hypotheses → instrument code → reproduce → analyze → fix

#### Graph (Self-Driving Codebases Research)

Cursor's research on building a browser with 1000+ agents revealed the final architecture:

```
Root Planner (owns full scope, does NO coding)
  ├── Subplanner A (owns narrow slice, recursive)
  │     ├── Worker 1 (isolated repo copy, single task)
  │     ├── Worker 2
  │     └── Worker 3
  ├── Subplanner B
  │     ├── Worker 4
  │     └── Worker 5
  └── Direct Workers (simple tasks)
```

**Key learnings from Cursor's multi-agent research:**

1. **Self-coordination fails**: Equal agents with shared state files → lock contention,
   forgot to release, paralysis. Locking is narrowly correct and models can't get it right.

2. **Structure and roles win**: Planner → Executor → Workers with clear ownership resolved
   most coordination issues.

3. **Remove the integrator**: Central quality gate became bottleneck at scale. Let the system
   accept small error rate and converge naturally.

4. **Freshness mechanisms**: scratchpad.md rewritten (not appended), auto-summarize at context
   limits, self-reflection reminders, agents encouraged to pivot.

5. **Accept some error rate**: 100% correctness before every commit causes serialization
   slowdowns. Accept stable error rate + final "green branch" fixup pass.

6. **Architecture and instructions matter more than harness**: Agents follow instructions to
   the end — good or bad. Finding balance between narrow metrics and unstructured freedom
   is the hard problem.

7. **Constraints > instructions**: "No TODOs" works better than "finish implementations."

### Parallel Execution

- **Git worktrees**: Each agent in own worktree, isolated files/builds/tests
- **Up to 8 parallel agents** from single prompt (local)
- **Cloud agents**: Full sessions in isolated cloud VMs (Cursor 3.5+)
- **/multitask**: Async subagent capability breaking tasks into fleet execution (Cursor 3.2+)
- **Multi-model comparison**: Same prompt across multiple models simultaneously

### The Agent Window (Cursor 3.0+)

Full-screen workspace for parallel agents. Each agent:
- Has its own conversation thread
- Runs in isolated git worktree
- Can be local, cloud, remote SSH, or Docker
- Results merged back via "Apply" button

---


## 6. Kageha Framework: Current State

### What We Have (Strengths)

#### Harness Layer — STRONG

| Component | Implementation | File |
|-----------|---------------|------|
| Tool Registry | @tool decorator, JSON Schema generation, risk_class | `harness/tools/base.py` |
| Tool Packs | Core (always) + Optional (browser, computer, media) | `harness/tool_packs.py` |
| Dispatch Router | Parallel (safe) / Serial (gated), risk-class policy | `harness/router.py` |
| Approval Gate | 3-tier Codex-style (once/session/full) + allowlist | `harness/approvals.py` |
| Shell Sandbox | seatbelt (macOS), bwrap (Linux), docker, privilege ladder | `harness/shell_sandbox.py` |
| Hook System | 8 events, shell/HTTP actions, matchers, block capability | `project/hooks.py` |
| MCP Integration | stdio/SSE/HTTP, hot-reload, risk-gated, multi-server | `mcp/client.py` |

#### Loop Layer — STRONG

| Component | Implementation | File |
|-----------|---------------|------|
| Core Loop | ReAct with max_steps, tool dispatch, verification gates | `loop/controller.py` |
| Adaptive Control | REPAIR/REPLAN/SWITCH_TOOL/HUDDLE/RETRY/ASK_USER/ADVANCE | `loop/adaptive.py` |
| Stop Rules | Cancel, ask_user, success, max_steps, budget, repair loops, no-progress | `loop/stop_rules.py` |
| Verifier | Separate role/model, provider exclusion, defect emission | `loop/verifier.py` |
| Anti-Loop | Detects repeated failures, escalates RETRY→SWITCH→REPLAN→HUDDLE | `loop/tool_guardrails.py` |
| Checkpoint | LLM summarization, token-triggered, persistent | `loop/checkpoint.py` |
| Monitor | Plan alignment detection, drift detection | `loop/monitor.py` |

#### Graph Layer — GOOD

| Component | Implementation | File |
|-----------|---------------|------|
| Task Graph | DAG with cycle detection, wave-based parallel execution | `agents/task_graph.py` |
| Subagents | spawn_subagent, spawn_subagents (fan-out), spawn_task_graph | `agents/subagent.py` |
| Worktree Isolation | Git worktree per subagent, branch isolation | `project/worktree.py` |
| Concurrency | Semaphore-bounded (max 8), communication-only mode | `agents/subagent.py` |

#### Runtime Layer — SOLID

| Component | Implementation | File |
|-----------|---------------|------|
| Durable Engine | Session management, crash recovery, replay | `runtime/engine.py` |
| Tool Journal | Before/after recording, reconciliation states | `runtime/journal.py` |
| Event Store | SQLite WAL, idempotent append, full audit trail | `runtime/store.py` |
| Telemetry | OTEL spans, metrics, duration tracking | `runtime/telemetry.py` |
| Model Router | Role ladders, circuit breaker, provider exclusion, cost-aware | `models/router.py` |

#### Additional Strengths

- **Skills system**: Intent-matched, auto-injected, progressive loading
- **Memory service**: Long-term storage with recall, rule import from AGENTS.md/.cursor/rules
- **Multi-model routing**: Different models for planning/coding/verification/monitoring
- **Verification-gated success**: Model cannot self-certify — must pass verifier

---


## 7. Gap Analysis

### Critical Gaps (HIGH priority)

#### GAP 1: No Per-Model Harness Tuning

**What's missing:** All models receive the same system prompt and tool set. No mechanism to
say "when using GPT-5.6, prefer write_file over bash" or "when using Gemini, restructure
tool schemas differently."

**Why it matters:** Cursor explicitly states this is their competitive advantage. Different
models have different strengths, training data biases, and tool-calling patterns. A single
prompt/tool-set leaves performance on the table for every model except the one you optimized for.

**What competitors do:**
- Cursor: Custom instructions + tool selection per model family, internal eval suite per model
- Kiro-with-harness: Model policy scripts that retarget agent prompts per provider

**Proposed fix:**
```python
# models.yaml extension
models:
  claude-sonnet-5:
    harness_profile: "claude"  # → loads prompts/claude.md, tools/claude_overrides.yaml
  gpt-5.6:
    harness_profile: "openai"  # → loads prompts/openai.md, tools/openai_overrides.yaml
```

#### GAP 2: No Cloud/Remote Agent Execution

**What's missing:** Agents can only run locally. The Modal integration is a stub. There's no
way to spin up an agent session in an isolated cloud VM with its own filesystem, network, and
environment.

**Why it matters:** Long-running autonomous agents need:
- Full environment isolation (install dependencies without affecting host)
- Persistent execution (survives laptop close)
- Horizontal scaling (run 100 agents on 100 machines)
- Security boundary (agent can't escape its sandbox)

**What competitors do:**
- Cursor 3.5: Cloud agents in isolated VMs (full terminal, browser, desktop access)
- Cursor research: Single large VM with hundreds of agents, infrastructure for 1000 commits/hr
- Codex: Cloud-first execution with filesystem snapshots

**Proposed fix:** Implement `CloudAgentBackend` with:
- VM provisioning (Modal, Fly.io, or AWS ECS)
- Workspace snapshotting (git clone + dependencies)
- Result streaming (progress events back to local session)
- Cost controls (auto-shutdown after idle timeout)

#### GAP 3: No Spec-Driven Development Pipeline

**What's missing:** No formal pipeline that produces persistent artifacts:
requirements.md → design.md → tasks.md → parallel execution. The Plan mode produces a flat
step list, not structured documents with validation gates between stages.

**Why it matters:** Kiro's philosophy is that "vibe coding" produces technical debt. A spec
forces clear thinking, enables review at each stage, and gives agents a precise target. Without
it, complex features get built wrong on the first pass and require expensive rework.

**What competitors do:**
- Kiro: Spec → Requirements → Design → Tasks as separate .md artifacts in .kiro/specs/
- Kiro: Each stage has a validation gate (human review before proceeding)
- Kiro: Property-based tests derived from spec for verification

**Proposed fix:**
```
.kageha/specs/<feature>/
  ├── requirements.md   (generated from prompt, editable)
  ├── design.md         (architecture decisions, dependencies)
  ├── tasks.md          (sequenced, dependency-aware task list)
  └── verification.md   (acceptance criteria, test strategies)
```

### Medium Gaps

#### GAP 4: Static Context Engineering

**What's missing:** Context budget is fixed per-section (system=2000, tools=3000, history=12000
tokens). No semantic compression, no active context rotation, no relevance-based selection,
no model-specific window optimization.

**Why it matters:** Long runs accumulate noise. A fixed head-tail truncation loses relevant
mid-conversation context. Context window degradation is a silent quality killer.

**Proposed fix:**
- Dynamic budget allocation based on task complexity
- Semantic importance scoring for messages (keep high-signal, drop noise)
- Model-aware budgets (200k vs 1M context models get different allocations)
- Retrieval-augmented context from past turns (not just latest checkpoint)

#### GAP 5: Verifier Is Not a Full Agent

**What's missing:** The verifier is a single LLM call with a prompt. It can't run tests,
browse artifacts, execute commands, or use tools to verify. It grades based on what it's told,
not what it can independently confirm.

**Why it matters:** The Ralph Loop pattern (Pattern 5) works precisely because the exit
condition comes from deterministic external validation. An LLM-only verifier can still be
fooled by plausible-sounding tool output.

**Proposed fix:**
- Give the verifier its own tool subset (read_file, bash for test execution, grep)
- Run deterministic checks first (tests pass? lint clean? file exists?)
- LLM verification only for subjective quality or complex reasoning
- Different model family for verifier vs executor (adversarial evaluation)

#### GAP 6: Limited Hook Event Set

**What's missing:** Only 8 fixed events. No file lifecycle events (onSave, onCommit,
onCreate, onDelete). No session lifecycle events (onStart, onEnd, onPlanApproved). No
extensible event registry.

**Why it matters:** Cursor and Kiro both use hooks for powerful automation (lint-on-save,
review-on-stop, changelog-on-commit, capture-lessons). Our hook system can't do half of these.

**Proposed fix:** Extend to ~20 events:
```
File: preFileWrite, postFileWrite, postFileCreate, postFileDelete
Git: preCommit, postCommit, prePush
Session: sessionStart, sessionEnd, planApproved, specStageComplete
Agent: agentStuck, budgetWarning, contextOverflow
```

### Low Gaps (nice-to-have)

#### GAP 7: No Native Steering Directory Convention

We import from .cursor/rules/ and AGENTS.md but don't define our own `.kageha/steering/`
convention with inclusion modes (always, fileMatch, manual).

#### GAP 8: No Visual Session Replay

Strong structured logging exists but no UI to browse it. Timeline visualization of
decisions, tool calls, and state transitions would aid debugging.

#### GAP 9: No Decision Trace

The observability system records WHAT happened but not WHY. No trace of why the router
picked model X, why adaptive control chose repair vs replan, why the verifier passed/failed.

---


## 8. Recommendations & Roadmap

### Maturity Assessment

```
                    Kageha    Cursor    Kiro
                    ------    ------    ----
Harness              ████░     █████     ████░
Loop                 █████     ████░     ███░░
Graph                ███░░     █████     ████░
Context Engineering  ██░░░     ████░     ███░░
Spec Pipeline        ░░░░░     ██░░░     █████
Cloud Execution      ░░░░░     █████     ██░░░
Per-Model Tuning     ░░░░░     █████     ███░░
Hook Extensibility   ██░░░     ████░     █████
Observability        ████░     ███░░     ███░░
MCP Integration      █████     ███░░     ████░
Skills System        ████░     ████░     █████
```

### Priority Roadmap

#### Phase 1: Foundation Strengthening (Weeks 1-3)

1. **Per-Model Harness Profiles** — Create `harness_profiles/` directory with per-model
   instruction templates, tool preference weights, and parameter overrides. Hook into
   ContextAssembler to swap prompts based on active model.

2. **Extend Hook Events** — Add file lifecycle (preFileWrite, postFileWrite, postFileCreate),
   git events (preCommit, postCommit), and session events (sessionStart, sessionEnd,
   planApproved). Make the event registry extensible.

3. **Verifier Agent Upgrade** — Give the verifier a tool subset (read_file, bash, grep).
   Run deterministic checks first, LLM-only for subjective quality. Enforce different model
   family from executor.

#### Phase 2: Spec-Driven Development (Weeks 4-6)

4. **Spec Pipeline** — Implement the full flow:
   - `kageha spec new <feature>` → generates requirements.md from prompt
   - Validation gate → human review
   - `kageha spec design` → generates design.md with architecture decisions
   - Validation gate → human review
   - `kageha spec tasks` → generates sequenced task list with dependencies
   - Validation gate → human review
   - `kageha spec build` → executes tasks as parallel subagents via TaskGraph

5. **Native Steering Convention** — Define `.kageha/steering/` with:
   - Always-on rules (applied to every conversation)
   - Conditional rules (fileMatch pattern → loaded when matching files in context)
   - Manual rules (user explicitly invokes)

#### Phase 3: Scale & Isolation (Weeks 7-10)

6. **Cloud Agent Backend** — Implement real cloud execution:
   - Provider abstraction (Modal, Fly.io, AWS ECS)
   - Workspace provisioning (git clone + dependency install)
   - Event streaming (progress back to local session)
   - Cost controls (auto-shutdown, budget caps)
   - Result collection (diff generation, artifact download)

7. **Advanced Context Engineering** — Implement:
   - Semantic importance scoring per message
   - Dynamic budget allocation (task complexity → more/less history)
   - Active context rotation (swap in relevant past context from memory)
   - Model-aware window management (adjust for 200k vs 1M context)

#### Phase 4: Polish & Differentiation (Weeks 11-12)

8. **Decision Trace System** — Log WHY at every decision point:
   - Model selection reason
   - Adaptive control decision rationale
   - Verifier pass/fail evidence
   - Tool dispatch classification reason

9. **Session Replay** — Build on existing EventLog/RuntimeStore:
   - Timeline visualization (terminal-based, like `htop` for agent sessions)
   - Step-through mode for debugging
   - Export to JSON for external tools

---

## Key Takeaways

### What We're Doing Well

1. **Loop engineering is our strongest layer.** The adaptive control system
   (REPAIR/REPLAN/SWITCH_TOOL/HUDDLE) with anti-loop detection and verification-gated
   success is best-in-class. This IS the Pattern 5 (Ralph Loop) + Pattern 8 (Circuit
   Breaker) combination.

2. **MCP integration is production-ready.** Hot-reload, multi-transport, risk-gating.

3. **The approval system is battle-tested.** 3-tier Codex-style with shell classification,
   privilege ladder, and HITL integration.

4. **Task graph DAG execution works.** Wave-based parallel execution with cycle detection
   and dependency resolution.

### What We Need Most

1. **Per-model harness tuning** — The single biggest bang-for-buck improvement. Different
   models need different prompts, tool preferences, and constraints.

2. **Spec-driven development** — This is the philosophical differentiator. It turns kageha
   from "another ReAct agent" into a structured engineering tool.

3. **Cloud execution** — Required for any serious autonomous long-running workload.

### Philosophy to Adopt

From Cursor: "Constraints are more effective than instructions."
From Kiro: "The unit of work is a specification, not a prompt."
From OpenAI: "Harness-level changes improve quality 10x more than prompt changes."
From the field: "Master the loop before you wire the graph."

---

## Sources

- [Cursor: Best practices for coding with agents](https://cursor.com/blog/agent-best-practices)
- [Cursor: Towards self-driving codebases](https://cursor.com/blog/self-driving-codebases)
- [Data Science Dojo: 10 Loop Engineering Design Patterns](https://datasciencedojo.com/blog/loop-engineering-design-patterns/)
- [AI Builder Club: Graph Engineering Guide 2026](https://www.aibuilderclub.com/blog/graph-engineering-guide-2026)
- [Kiro.dev](https://kiro.dev/)
- [TeiNam/kiro-with-harness](https://github.com/TeiNam/kiro-with-harness)
- [aws-samples/sample-kiro-cli-multiagent-development](https://github.com/aws-samples/sample-kiro-cli-multiagent-development)
- [OpenAI: Harness Engineering](https://openai.com/index/harness-engineering/)
- [Cursor 3.2: IDE as Agent Execution Runtime](https://futurumgroup.com/insights/cursor-3-2-reframes-the-ide-as-an-agent-execution-runtime/)
