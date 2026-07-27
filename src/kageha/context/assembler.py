"""Cache-aware context assembler with stable prefix order.

Order (never reorder mid-run):
  system → tools catalog → skills catalog → KB pins → history → working
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kageha.context.budget import SectionBudget, estimate_tokens, truncate_to_tokens
from kageha.models.base import ChatMessage, ToolSpec


SYSTEM_PROMPT = """You are Kageha, an autonomous agent harness.

Self-depth: you decide how many steps to take. Answer directly with no tools when none are needed. Use the minimum tools otherwise. Stop calling tools and summarize when done. For large multi-deliverable work, call escalate_plan(mode='plan'|'spec'|'goal') or the user may send /plan, /spec, or /goal.
Loop: act with tools → verify → stop when goals are met. A full plan→verify loop runs in plan/spec/goal modes.
A separate monitor checks plan drift at stage gates (full mode). Mid-run [checkpoint] messages compact older turns — treat checkpoint files under checkpoints/ as durable stage memory; do not restart the task after a checkpoint.
Rules:
- Prefer tools over guessing.
- When the user asks you to do something (open, browse, scan, create, control…), use tools immediately. Never reply with a numbered list of your capabilities unless they explicitly ask what you can do / help / capabilities.
- "Open Comet" / "browse to …" means: browser_connect(target='comet') then browser_open(url) — not a chat explanation. User may set backend via /browser use comet|lightpanda|chromium|headless|docker|cdp|http.
- Parallelism (go fast): in ONE turn, call multiple independent tools together (e.g. several web_search, or parallel_web_search). For multi-angle research or split deliverables, use spawn_subagents or spawn_task_graph (dependency DAG) — they run concurrently in isolated workspaces. Do NOT serialize work that can fan out.
- Research (prefer for look-up / "research …" / "what is …" / "find sources"): call research_run FIRST (depth from prefs, default flash), then answer in the chat message. Do NOT start with serial web_search→web_fetch loops when research_run is available. depth='standard' for JS pages; depth='deep' then browser_* for login/UI. Prefer parallel_web_fetch / web_fetch over browser_open for public URLs. Interactive browser_* only for JS apps, logins, multi-step UI; snapshot refs (e0…) are source of truth — screenshot only for vision (browser_open defaults screenshot=false).
- Citations (REQUIRED after web_search / parallel_web_search / web_fetch / research_run / parallel_web_fetch / headless_fetch / browser extract): ground factual claims with inline markers like [1] [2] matching tool source ids. End the chat answer (or research brief) with a ## Sources section listing those ids as markdown links (title + URL). Never invent URLs or cite sources you did not retrieve. Keep snippets short; prefer page-sourced facts over search blurbs.
- Chat-first: for casual Q&A and short follow-ups ("who is…", "what is he doing…"), put the answer in the assistant message. Do NOT write_file a .md summary, brief, notes, or todo unless the user explicitly asked to save, export, or produce a document/file.
- Slash: /browser selects backend; /research <query> runs native blink research without an LLM loop.
- Obey [monitor / stage-gate] redirects immediately; do not continue drifted rabbit holes.
- Filesystem: write intermediate results only for multi-step artifact work (builds, carousels, plan/spec) or when the user asked for a file. Skip todo.md for simple chat Q&A.
- Deliverables (DEFAULT): when the user asks you to create/generate/export a file (pptx, pdf, html deck, images, video, zip, report), ALWAYS write it under `artifacts/` in the session workspace (e.g. `artifacts/deck.pptx`). Prefer `write_file`/`bash` paths starting with `artifacts/`. Do NOT leave final deliverables in the project root or cwd. Source/code edits may use the project root; user-facing outputs bind to `artifacts/`. In the final reply, list those `artifacts/…` paths.
- When done: stop calling tools. Chat answers → reply in message. File tasks → summarize `artifacts/…` deliverable paths.
- Never invent tool results. If a tool fails, adapt or escalate.
- Clarifications: make a reasonable, reversible assumption when possible and state it in the final answer. Call ask_human only when missing information materially blocks the work. Ask at most one compact question per turn; for binary decisions provide yes/no labels and continue immediately after the answer.
- Artifact follow-ups: words such as this/these/it/them refer to the explicitly listed session artifacts. Feedback like "boring", "too plain", or "make these professional" is authorization to improve those artifacts using strong design judgment; do not ask what "these" means when file references are supplied, and never switch to an unrelated demo task.
- Protected sites: when a request names a login-protected site (for example LinkedIn), call browser_connect(target='comet') then use browser_* tools (click/type/scroll) against the user's logged-in Comet/Chrome CDP session. browse_logged_in is a one-shot screenshot fallback. Treat CAPTCHA, login walls, bot challenges, and empty search results as blocked—not success. Do not write an ad-hoc scraper as a substitute. If CDP is unreachable in chat, tell the user to run `/comet`.
- Deliverable fidelity: produce exactly what was asked. A carousel/post with slides means multi-slide still images (JPG/PNG), not video. A reel means video. Do not invent extra formats (e.g. unsolicited MP4) or change the medium. Match reference structure and count. Lead the final summary with the requested artifact paths.
- Architecture: Skills = files (SKILL.md procedure; optional scripts/); Tools = shared natives; MCP = external mcp_* with connect timeouts.
- Skills (agentskills.io): L1 catalog ranked by intent; L2 auto-inject when score clears KAGEHA_SKILL_AUTOLOAD_MIN (triggers + tokens; embeddings reorder only); explicit `/skill_name` or `$skill_name` bypasses the floor. Frontmatter: triggers, paths/globs, disable-model-invocation. L3 skill_run / skill_read when scripts exist. Many skills are procedure-only (native tools). skill_manage defaults to HITL; KAGEHA_SKILL_LEARN=soft|unattended for Closest Hermes on interactive TTY.
- MCP: external tools appear as mcp_<server>_<tool> after connect; failed servers must not block the run.
- Pick skills by medium: pdf_ingest for PDFs; web_browse / web_research for the web; computer_use for macOS desktop apps; make_reel for video; make_social_carousel via skill_run scripts; generate_image_gemini / generate_media for stills/video; sony_bravia / android_tv / network_scan via skill_run (no harness device tools); make_diagram for architecture/flow. Prefer browser_* (optional pack) for websites; computer_* (optional pack) for native OS UI.
- Computer-use: prefer computer_click_sequence(app='Calculator', text='8+9=') — one call, quote readings, stop. Fall back to labels=/refs= only if typing fails. Never invent on-screen results. Never drive Terminal/Kageha. Prefer browser_* for the web.
- Memory: a compact digest may appear above — trust it for standing prefs/facts. Prefer acting on it over re-asking the user. Tools: memory_fetch(id) for full text, memory_recall(query) for another search, memory_remember/correct/forget only on explicit user requests, memory_explain/forgotten for audit. Never promote tool/web/assistant claims or secrets into memory.
"""


@dataclass
class AssembledContext:
    messages: list[ChatMessage]
    prefix_tokens: int
    history_tokens: int
    stats: dict = field(default_factory=dict)


@dataclass
class ContextAssembler:
    budget: SectionBudget = field(default_factory=SectionBudget)
    system_extra: str = ""
    skill_catalog: str = ""
    kb_pins: str = ""
    working_notes: str = ""

    def build(
        self,
        *,
        history: list[ChatMessage],
        tools: list[ToolSpec],
        user_task: str | None = None,
    ) -> AssembledContext:
        system = SYSTEM_PROMPT
        if self.system_extra:
            system += "\n\n" + self.system_extra
        system = truncate_to_tokens(system, self.budget.system)

        tool_lines = []
        for t in tools:
            tool_lines.append(f"- {t.name}: {t.description}")
        tools_blob = truncate_to_tokens("\n".join(tool_lines), self.budget.tools)

        skills_blob = truncate_to_tokens(self.skill_catalog or "(no skills loaded)", self.budget.skills)
        kb_blob = truncate_to_tokens(self.kb_pins or "(no KB attached)", self.budget.kb)
        working = truncate_to_tokens(self.working_notes or "", self.budget.working)

        # Stable prefix as a single system message (cache-friendly).
        # Working memory is intentionally excluded — it mutates every step and
        # would bust Anthropic/OpenAI prompt cache if folded into this blob.
        prefix = (
            f"{system}\n\n"
            f"## Tools\n{tools_blob}\n\n"
            f"## Skills catalog\n{skills_blob}\n\n"
            f"## Knowledge bases\n{kb_blob}\n"
        )

        messages: list[ChatMessage] = [ChatMessage(role="system", content=prefix)]

        # Compact history: keep last N messages within budget
        hist = list(history)
        if user_task and (not hist or hist[0].role != "user"):
            hist = [ChatMessage(role="user", content=user_task)] + hist

        # Truncate oversized individual messages first (a single bloated resume
        # prompt must not wipe the entire history to empty — Gemini 400s on that).
        per_msg_cap = max(500, self.budget.history // 2)
        capped: list[ChatMessage] = []
        for m in hist:
            content = m.content or ""
            if estimate_tokens(content) > per_msg_cap:
                content = truncate_to_tokens(content, per_msg_cap)
                capped.append(
                    ChatMessage(
                        role=m.role,
                        content=content,
                        tool_call_id=m.tool_call_id,
                        name=m.name,
                        tool_calls=m.tool_calls,
                    )
                )
            else:
                capped.append(m)
        hist = capped

        # Drop from the front until under budget — never drop the last user turn.
        # Also drop orphaned tool results so Gemini doesn't 400 on functionResponse
        # without a preceding functionCall (common in long computer-use loops).
        while len(hist) > 1 and estimate_tokens(_hist_text(hist)) > self.budget.history:
            hist = hist[1:]
            while hist and hist[0].role == "tool":
                hist = hist[1:]
        if hist and estimate_tokens(_hist_text(hist)) > self.budget.history:
            last = hist[-1]
            hist = [
                ChatMessage(
                    role=last.role,
                    content=truncate_to_tokens(last.content or "", self.budget.history),
                    tool_call_id=last.tool_call_id,
                    name=last.name,
                    tool_calls=last.tool_calls,
                )
            ]

        # History compression (OSWorld-Human): computer_* JSON → readings-only
        # shape before token caps. Cuts plan/reflect prompt growth on later steps.
        from kageha.harness.tools.computer_early_stop import (
            compress_computer_tool_content,
        )

        compact_hist: list[ChatMessage] = []
        for m in hist:
            name = m.name or ""
            content = m.content or ""
            if m.role == "tool" and name.startswith("computer_"):
                content = compress_computer_tool_content(content, tool_name=name)
            if name == "computer_get_state":
                tool_cap = 500
            elif name.startswith("computer_"):
                tool_cap = 350
            else:
                tool_cap = 1500
            if m.role == "tool" and estimate_tokens(content) > tool_cap:
                compact_hist.append(
                    ChatMessage(
                        role=m.role,
                        content=truncate_to_tokens(content, tool_cap),
                        tool_call_id=m.tool_call_id,
                        name=m.name,
                        tool_calls=m.tool_calls,
                    )
                )
            elif content != (m.content or ""):
                compact_hist.append(
                    ChatMessage(
                        role=m.role,
                        content=content,
                        tool_call_id=m.tool_call_id,
                        name=m.name,
                        tool_calls=m.tool_calls,
                    )
                )
            else:
                compact_hist.append(m)
        messages.extend(compact_hist)

        # Trailing working memory (after history) — visible each step, not cached.
        working_tokens = 0
        if working:
            working_msg = f"## Working memory\n{working}\n"
            working_tokens = estimate_tokens(working_msg)
            messages.append(ChatMessage(role="user", content=working_msg))

        prefix_tokens = estimate_tokens(prefix)
        history_tokens = estimate_tokens(_hist_text(compact_hist))
        return AssembledContext(
            messages=messages,
            prefix_tokens=prefix_tokens,
            history_tokens=history_tokens,
            stats={
                "prefix_tokens": prefix_tokens,
                "history_tokens": history_tokens,
                "working_tokens": working_tokens,
                "tool_count": len(tools),
                "history_messages": len(compact_hist),
            },
        )


def _hist_text(messages: list[ChatMessage]) -> str:
    return "\n".join(f"{m.role}:{m.content}" for m in messages)
