/**
 * AgentCanvas — Monitoring panel.
 *
 * Three tabs:
 * - Timeline: Live tool calls with expand/collapse, I/O, duration, status
 * - Artifacts: File previews (images, docs, media)
 * - Stats: Cost, tokens, steps, elapsed time
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { cn } from "../lib/cn";
import { useAppStore } from "../store";
import type { ActivityStep, ToolCard } from "../api/types";
import { ArtifactPanel } from "./ThreadView/ArtifactPanel";


// ── Types ──────────────────────────────────────────────────────────────

type CanvasTab = "timeline" | "plan" | "subagents" | "artifacts" | "stats";

interface SessionStats {
  steps: number;
  toolCalls: number;
  toolSuccesses: number;
  toolFailures: number;
  totalTokensIn: number;
  totalTokensOut: number;
  totalCostUsd: number;
  elapsedMs: number;
  models: string[];
}

// ── Helpers ────────────────────────────────────────────────────────────

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

function formatCost(usd: number): string {
  if (usd < 0.001) return "<$0.001";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(3)}`;
}

function toolStatusIcon(status?: string): string {
  if (status === "success" || status === "completed") return "✓";
  if (status === "error" || status === "failed") return "✗";
  if (status === "running" || status === "pending") return "⟳";
  return "·";
}

function toolStatusColor(status?: string): string {
  if (status === "success" || status === "completed") return "text-green-500";
  if (status === "error" || status === "failed") return "text-red-500";
  if (status === "running" || status === "pending") return "text-accent";
  return "text-muted";
}


// ── Timeline Tab ───────────────────────────────────────────────────────

function ToolCallRow({ card }: { card: ToolCard }) {
  const [expanded, setExpanded] = useState(false);
  const icon = toolStatusIcon(card.status);
  const color = toolStatusColor(card.status);

  return (
    <div className="border-b border-line/50 last:border-b-0">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-line/30 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <span className={cn("text-sm font-mono", color)}>{icon}</span>
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-ink">
          {card.name}
        </span>
        {card.durationMs != null && (
          <span className="shrink-0 text-[0.65rem] tabular-nums text-muted">
            {formatDuration(card.durationMs)}
          </span>
        )}
        <span className="shrink-0 text-[0.6rem] text-faint">
          {expanded ? "▾" : "▸"}
        </span>
      </button>
      {expanded && (
        <div className="px-3 pb-2 space-y-1">
          {card.argsPreview && (
            <div>
              <span className="text-[0.6rem] uppercase tracking-wide text-muted">Input</span>
              <pre className="mt-0.5 max-h-24 overflow-auto rounded bg-canvas p-1.5 font-mono text-[0.65rem] text-ink whitespace-pre-wrap break-all">
                {card.argsPreview}
              </pre>
            </div>
          )}
          {card.resultPreview && (
            <div>
              <span className="text-[0.6rem] uppercase tracking-wide text-muted">Output</span>
              <pre className="mt-0.5 max-h-24 overflow-auto rounded bg-canvas p-1.5 font-mono text-[0.65rem] text-ink whitespace-pre-wrap break-all">
                {card.resultPreview}
              </pre>
            </div>
          )}
          {card.artifactRefs && card.artifactRefs.length > 0 && (
            <div className="flex flex-wrap gap-1 pt-1">
              {card.artifactRefs.map((ref) => (
                <span key={ref} className="rounded bg-accent-soft px-1.5 py-0.5 text-[0.6rem] text-accent">
                  📎 {ref.split("/").pop()}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TimelineTab() {
  const messages = useAppStore((s) => s.messages);

  // Collect all tool cards from all assistant messages
  const allToolCards = useMemo(() => {
    const cards: ToolCard[] = [];
    for (const m of messages) {
      if (m.role === "assistant" && m.toolCards) {
        cards.push(...m.toolCards);
      }
    }
    return cards;
  }, [messages]);

  // Also collect activity steps for context (exclude todo/plan items — those go in Plan tab)
  const allSteps = useMemo(() => {
    const steps: ActivityStep[] = [];
    for (const m of messages) {
      if (m.role === "assistant" && m.steps) {
        for (const step of m.steps) {
          const low = (step.label || "").toLowerCase();
          // Filter out todo board and plan-related steps
          if (low.startsWith("todos") || low.includes("todo_board")) continue;
          steps.push(step);
        }
      }
    }
    return steps;
  }, [messages]);

  if (!allToolCards.length && !allSteps.length) {
    return (
      <div className="flex flex-1 items-center justify-center px-4 text-center text-sm text-muted">
        No tool calls yet. Activity will appear here as the agent works.
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      {/* Steps summary */}
      {allSteps.length > 0 && (
        <div className="border-b border-line px-3 py-2">
          <p className="text-[0.65rem] uppercase tracking-wide text-muted mb-1">
            Steps ({allSteps.length})
          </p>
          <div className="space-y-0.5">
            {allSteps.slice(-20).map((step, i) => (
              <p key={i} className="text-xs text-ink truncate">
                <span className="text-accent mr-1">▸</span>
                {step.label}
              </p>
            ))}
          </div>
        </div>
      )}
      {/* Tool calls */}
      <div>
        <p className="px-3 py-2 text-[0.65rem] uppercase tracking-wide text-muted">
          Tool Calls ({allToolCards.length})
        </p>
        {allToolCards.map((card) => (
          <ToolCallRow key={card.id} card={card} />
        ))}
      </div>
    </div>
  );
}

function SubagentsTab() {
  const messages = useAppStore((s) => s.messages);
  const cards = useMemo(() => {
    const out: ToolCard[] = [];
    for (const message of messages) {
      if (message.role !== "assistant" || !message.toolCards) continue;
      out.push(...message.toolCards.filter((card) =>
        /spawn_(?:subagent|subagents)|spawn_task_graph/i.test(card.name),
      ));
    }
    return out;
  }, [messages]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="border-b border-line bg-accent-soft/30 px-3 py-3">
        <p className="text-xs font-semibold text-ink">Coordinator workspace</p>
        <p className="mt-1 text-[0.68rem] leading-relaxed text-muted">
          Independent work runs in subagent threads while the main chat stays focused on the parent task.
        </p>
      </div>
      {cards.length ? (
        <div>
          <p className="px-3 py-2 text-[0.65rem] uppercase tracking-wide text-muted">
            Delegations ({cards.length})
          </p>
          {cards.map((card) => <ToolCallRow key={card.id} card={card} />)}
        </div>
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center px-6 py-12 text-center">
          <span className="text-2xl text-accent">⑂</span>
          <p className="mt-3 text-sm font-medium text-muted">No subagents yet</p>
          <p className="mt-1 text-xs leading-relaxed text-faint">
            The coordinator will show delegated research, implementation, testing, and review work here.
          </p>
        </div>
      )}
    </div>
  );
}


// ── Plan Tab ───────────────────────────────────────────────────────────

function PlanTab() {
  const todoBoard = useAppStore((s) => s.todoBoard);
  const messages = useAppStore((s) => s.messages);
  const runStatus = useAppStore((s) => s.runStatus);
  const sessionId = useAppStore((s) => s.sessionId);
  const [planMd, setPlanMd] = useState("");
  const [planLoading, setPlanLoading] = useState(false);
  const [planExpanded, setPlanExpanded] = useState(false);
  const [stepsExpanded, setStepsExpanded] = useState(true);

  // Fetch plan.md content from the session
  useEffect(() => {
    if (!sessionId) { setPlanMd(""); return; }
    let cancelled = false;
    setPlanLoading(true);
    void fetch(`/api/sessions/${encodeURIComponent(sessionId)}/files/plan.md`)
      .then((r) => {
        if (!r.ok) throw new Error("not found");
        return r.text();
      })
      .then((text) => { if (!cancelled) setPlanMd(text); })
      .catch(() => { if (!cancelled) setPlanMd(""); })
      .finally(() => { if (!cancelled) setPlanLoading(false); });
    return () => { cancelled = true; };
  }, [sessionId, todoBoard?.total]);

  // Extract plan summary from activity steps
  const planInfo = useMemo(() => {
    for (const m of [...messages].reverse()) {
      if (m.role !== "assistant") continue;
      for (const step of [...(m.steps || [])].reverse()) {
        if (step.label?.toLowerCase().includes("plan ready") && step.detail?.length) {
          return { label: step.label, details: step.detail };
        }
      }
    }
    return null;
  }, [messages]);

  const hasBoard = todoBoard && todoBoard.total > 0;
  const isEmpty = !hasBoard && !planInfo && !planMd;

  if (isEmpty) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center px-4 py-8 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-line/30 mb-3">
          <span className="text-xl text-faint">☑</span>
        </div>
        <p className="text-sm font-medium text-muted">No plan yet</p>
        <p className="mt-1.5 text-xs text-faint leading-relaxed max-w-[220px]">
          Use <span className="font-mono text-accent">/plan</span> to create an execution plan with tracked steps
        </p>
      </div>
    );
  }

  const progress = hasBoard ? (todoBoard!.done / todoBoard!.total) * 100 : 0;
  const allDone = hasBoard && todoBoard!.done === todoBoard!.total;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      {/* Progress header */}
      {hasBoard && (
        <div className="sticky top-0 z-10 border-b border-line bg-surface/95 backdrop-blur-sm px-3 py-2.5">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[0.7rem] font-semibold text-ink">
              {allDone ? "Complete" : "In Progress"}
            </span>
            <span className={cn(
              "text-[0.7rem] font-bold tabular-nums",
              allDone ? "text-accent" : "text-muted",
            )}>
              {todoBoard!.done}/{todoBoard!.total}
            </span>
          </div>
          <div className="relative h-1.5 rounded-full bg-line overflow-hidden">
            <div
              className={cn(
                "absolute inset-y-0 left-0 rounded-full transition-all duration-700 ease-out",
                allDone ? "bg-accent" : "bg-accent/80",
              )}
              style={{ width: `${progress}%` }}
            />
            {runStatus === "running" && !allDone && (
              <div
                className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-transparent via-white/25 to-transparent animate-[shimmer_2s_ease-in-out_infinite]"
                style={{ width: `${Math.min(100, progress + 10)}%` }}
              />
            )}
          </div>
        </div>
      )}

      <div className="p-3 space-y-2">
        {/* Steps — vertical stepper */}
        {hasBoard && (
          <div className="rounded-xl border border-line bg-canvas overflow-hidden">
            <button
              type="button"
              className="flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-line/20 transition-colors"
              onClick={() => setStepsExpanded((v) => !v)}
            >
              <svg
                width="10" height="10" viewBox="0 0 10 10"
                className={cn("shrink-0 text-muted transition-transform duration-200", stepsExpanded ? "rotate-0" : "-rotate-90")}
              >
                <path d="M2 3.5L5 6.5L8 3.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
              </svg>
              <span className="text-[0.7rem] font-semibold text-ink flex-1">Steps</span>
              {allDone && <span className="text-[0.6rem] text-accent font-medium">✓ Done</span>}
            </button>
            <div className={cn(
              "grid transition-all duration-300 ease-out",
              stepsExpanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
            )}>
              <div className="overflow-hidden">
                <div className="px-3 pb-2.5">
                  {todoBoard!.items.map((item, i) => {
                    const isLast = i === todoBoard!.items.length - 1;
                    const isCurrent = !item.done && (i === 0 || todoBoard!.items[i - 1]?.done);
                    return (
                      <div key={item.id} className="flex gap-3 relative">
                        {/* Vertical connector line */}
                        {!isLast && (
                          <div className={cn(
                            "absolute left-[9px] top-[22px] bottom-0 w-[2px] rounded-full",
                            item.done ? "bg-accent/40" : "bg-line",
                          )} />
                        )}
                        {/* Step indicator */}
                        <div className="relative z-10 mt-[5px] shrink-0">
                          {item.done ? (
                            <div className="flex h-[20px] w-[20px] items-center justify-center rounded-full bg-accent text-white">
                              <svg width="10" height="10" viewBox="0 0 12 12" fill="none">
                                <path d="M2 6.5L4.5 9L10 3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                              </svg>
                            </div>
                          ) : isCurrent ? (
                            <div className="flex h-[20px] w-[20px] items-center justify-center rounded-full border-2 border-accent bg-accent/10">
                              <div className="h-2 w-2 rounded-full bg-accent animate-pulse" />
                            </div>
                          ) : (
                            <div className="flex h-[20px] w-[20px] items-center justify-center rounded-full border-2 border-line bg-surface">
                              <span className="text-[0.5rem] font-bold text-faint">{i + 1}</span>
                            </div>
                          )}
                        </div>
                        {/* Step text */}
                        <div className={cn(
                          "flex-1 pb-3 pt-[3px]",
                          item.done ? "opacity-60" : isCurrent ? "opacity-100" : "opacity-50",
                        )}>
                          <p className={cn(
                            "text-[0.78rem] leading-snug",
                            item.done ? "text-muted line-through decoration-muted/40" : "text-ink",
                            isCurrent && "font-medium",
                          )}>
                            {item.text}
                          </p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Plan.md — collapsible rendered markdown */}
        {(planMd || planLoading) && (
          <div className="rounded-xl border border-line bg-canvas overflow-hidden">
            <button
              type="button"
              className="flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-line/20 transition-colors"
              onClick={() => setPlanExpanded((v) => !v)}
            >
              <svg
                width="10" height="10" viewBox="0 0 10 10"
                className={cn("shrink-0 text-muted transition-transform duration-200", planExpanded ? "rotate-0" : "-rotate-90")}
              >
                <path d="M2 3.5L5 6.5L8 3.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
              </svg>
              <span className="text-[0.7rem] font-semibold text-ink flex-1">Plan Details</span>
              <span className="text-[0.55rem] text-faint font-mono">plan.md</span>
            </button>
            <div className={cn(
              "grid transition-all duration-300 ease-out",
              planExpanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
            )}>
              <div className="overflow-hidden">
                {planLoading && !planMd ? (
                  <p className="px-3 py-2 text-xs text-muted">Loading…</p>
                ) : planMd ? (
                  <div
                    className="markdown px-3 py-3 text-[0.78rem] border-t border-line/40 overflow-auto max-h-[50vh]"
                    dangerouslySetInnerHTML={{
                      __html: DOMPurify.sanitize(
                        marked.parse(planMd, { async: false }) as string
                      ),
                    }}
                  />
                ) : null}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}


// ── Stats Tab ──────────────────────────────────────────────────────────

function StatsTab() {
  const messages = useAppStore((s) => s.messages);
  const runStatus = useAppStore((s) => s.runStatus);

  const stats = useMemo<SessionStats>(() => {
    let toolCalls = 0;
    let toolSuccesses = 0;
    let toolFailures = 0;
    let totalTokensIn = 0;
    let totalTokensOut = 0;
    let totalCostUsd = 0;
    let steps = 0;
    const models = new Set<string>();

    for (const m of messages) {
      if (m.role !== "assistant") continue;
      if (m.toolCards) {
        for (const card of m.toolCards) {
          toolCalls++;
          if (card.status === "success" || card.status === "completed") toolSuccesses++;
          else if (card.status === "error" || card.status === "failed") toolFailures++;
        }
      }
      if (m.steps) {
        for (const step of m.steps) {
          steps++;
          // Parse token/cost info from step labels
          const label = step.label || "";
          const tokMatch = label.match(/tokens?[=:]?\s*(\d+)\+(\d+)/i);
          if (tokMatch) {
            totalTokensIn += parseInt(tokMatch[1], 10);
            totalTokensOut += parseInt(tokMatch[2], 10);
          }
          const costMatch = label.match(/usd~?([\d.]+)/i);
          if (costMatch) {
            totalCostUsd = Math.max(totalCostUsd, parseFloat(costMatch[1]));
          }
          const modelMatch = label.match(/model[=:]\s*([a-z0-9_-]+)/i);
          if (modelMatch) models.add(modelMatch[1]);
        }
      }
    }

    return {
      steps,
      toolCalls,
      toolSuccesses,
      toolFailures,
      totalTokensIn,
      totalTokensOut,
      totalCostUsd,
      elapsedMs: 0, // Could be calculated from first/last message timestamps
      models: Array.from(models),
    };
  }, [messages]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-3 space-y-4">
      {/* Status */}
      <div className="rounded-lg border border-line bg-canvas p-3">
        <p className="text-[0.65rem] uppercase tracking-wide text-muted mb-2">Status</p>
        <div className="flex items-center gap-2">
          <span className={cn(
            "h-2.5 w-2.5 rounded-full",
            runStatus === "running" ? "bg-accent animate-pulse" :
            runStatus === "success" ? "bg-green-500" :
            runStatus === "error" ? "bg-red-500" : "bg-line-strong"
          )} />
          <span className="text-sm font-medium text-ink capitalize">{runStatus}</span>
        </div>
      </div>

      {/* Tool Calls */}
      <div className="rounded-lg border border-line bg-canvas p-3">
        <p className="text-[0.65rem] uppercase tracking-wide text-muted mb-2">Tool Calls</p>
        <div className="grid grid-cols-3 gap-2 text-center">
          <div>
            <p className="text-lg font-bold text-ink">{stats.toolCalls}</p>
            <p className="text-[0.6rem] text-muted">Total</p>
          </div>
          <div>
            <p className="text-lg font-bold text-green-500">{stats.toolSuccesses}</p>
            <p className="text-[0.6rem] text-muted">Success</p>
          </div>
          <div>
            <p className="text-lg font-bold text-red-500">{stats.toolFailures}</p>
            <p className="text-[0.6rem] text-muted">Failed</p>
          </div>
        </div>
      </div>

      {/* Tokens & Cost */}
      <div className="rounded-lg border border-line bg-canvas p-3">
        <p className="text-[0.65rem] uppercase tracking-wide text-muted mb-2">Usage</p>
        <div className="space-y-1.5">
          {stats.totalTokensIn > 0 && (
            <div className="flex justify-between text-xs">
              <span className="text-muted">Tokens In</span>
              <span className="font-mono text-ink">{stats.totalTokensIn.toLocaleString()}</span>
            </div>
          )}
          {stats.totalTokensOut > 0 && (
            <div className="flex justify-between text-xs">
              <span className="text-muted">Tokens Out</span>
              <span className="font-mono text-ink">{stats.totalTokensOut.toLocaleString()}</span>
            </div>
          )}
          {stats.totalCostUsd > 0 && (
            <div className="flex justify-between text-xs">
              <span className="text-muted">Cost</span>
              <span className="font-mono font-medium text-ink">{formatCost(stats.totalCostUsd)}</span>
            </div>
          )}
          <div className="flex justify-between text-xs">
            <span className="text-muted">Steps</span>
            <span className="font-mono text-ink">{stats.steps}</span>
          </div>
        </div>
      </div>

      {/* Models Used */}
      {stats.models.length > 0 && (
        <div className="rounded-lg border border-line bg-canvas p-3">
          <p className="text-[0.65rem] uppercase tracking-wide text-muted mb-2">Models</p>
          <div className="flex flex-wrap gap-1">
            {stats.models.map((model) => (
              <span key={model} className="rounded bg-accent-soft px-1.5 py-0.5 text-[0.65rem] font-mono text-accent">
                {model}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


function ArtifactsTab({ onOpenLightbox }: { onOpenLightbox?: (path: string) => void }) {
  return (
    <ArtifactPanel
      filter="all"
      onOpenLightbox={(path) => onOpenLightbox ? onOpenLightbox(path) : useAppStore.getState().openCanvasItem(path, { expand: true })}
    />
  );
}


// ── Main Canvas Component ──────────────────────────────────────────────

const TAB_CONFIG: { id: CanvasTab; label: string; icon: string }[] = [
  { id: "timeline", label: "Timeline", icon: "⚡" },
  { id: "plan", label: "Plan", icon: "☑" },
  { id: "subagents", label: "Subagents", icon: "⑂" },
  { id: "artifacts", label: "Artifacts", icon: "📎" },
  { id: "stats", label: "Stats", icon: "📊" },
];

/** Monitoring canvas with Timeline, Artifacts, and Stats tabs. */
export function AgentCanvas({ alwaysShow, onCollapse, onOpenLightbox }: { alwaysShow?: boolean; onCollapse?: () => void; onOpenLightbox?: (path: string) => void } = {}) {
  const canvasOpen = useAppStore((s) => s.canvasOpen);
  const setCanvasOpen = useAppStore((s) => s.setCanvasOpen);
  const canvasItems = useAppStore((s) => s.canvasItems);
  const messages = useAppStore((s) => s.messages);
  const runStatus = useAppStore((s) => s.runStatus);
  const todoBoard = useAppStore((s) => s.todoBoard);
  const [activeTab, setActiveTab] = useState<CanvasTab>("timeline");

  // Keep the Subagents surface contextual: it only appears after the
  // coordinator has actually delegated work to a subagent.
  const hasSubagentActivity = useMemo(
    () => messages.some((message) =>
      message.role === "assistant" &&
      message.toolCards?.some((card) => /spawn_(?:subagent|subagents)|spawn_task_graph/i.test(card.name)),
    ),
    [messages],
  );
  const visibleTabs = useMemo(
    () => TAB_CONFIG.filter((tab) => tab.id !== "subagents" || hasSubagentActivity),
    [hasSubagentActivity],
  );

  const prevRunStatus = useRef(runStatus);
  const autoSwitchedToPlan = useRef(false);

  // Auto-switch logic: runs ONCE per transition, never yanks the user back
  useEffect(() => {
    const wasRunning = prevRunStatus.current === "running";
    const nowRunning = runStatus === "running";

    // New run starts → show Timeline (reset plan switch flag)
    if (nowRunning && !wasRunning) {
      setActiveTab("timeline");
      autoSwitchedToPlan.current = false;
    }

    prevRunStatus.current = runStatus;
  }, [runStatus]);

  // Todos appeared → switch to Plan (only once per run, never re-triggers)
  useEffect(() => {
    if (todoBoard && todoBoard.total > 0 && !autoSwitchedToPlan.current) {
      autoSwitchedToPlan.current = true;
      setActiveTab("plan");
    }
  }, [todoBoard?.total]);

  useEffect(() => {
    if (!hasSubagentActivity && activeTab === "subagents") {
      setActiveTab("timeline");
    }
  }, [activeTab, hasSubagentActivity]);

  // Run completed with artifacts → switch to Artifacts (only from Timeline/Plan)
  useEffect(() => {
    if (
      canvasItems.length > 0 &&
      runStatus !== "running" &&
      (activeTab === "timeline" || activeTab === "plan")
    ) {
      setActiveTab("artifacts");
    }
  }, [canvasItems.length, runStatus]);

  if (!alwaysShow && !canvasOpen) return null;

  return (
    <aside
      className="flex w-full min-w-0 flex-1 flex-col border-l border-line bg-surface md:min-w-[22rem]"
      id="agent-canvas"
      aria-label="Agent monitoring canvas"
    >
      {/* Header */}
      <header className="flex h-12 shrink-0 items-center gap-1 border-b border-line px-2">
        {/* Tabs */}
        <div className="flex min-w-0 flex-1 gap-0.5">
          {visibleTabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={cn(
                "rounded-md px-2 py-1 text-xs font-medium transition-colors",
                activeTab === tab.id
                  ? "bg-accent-soft text-accent"
                  : "text-muted hover:bg-line/70 hover:text-ink",
              )}
              onClick={() => setActiveTab(tab.id)}
            >
              <span className="mr-1">{tab.icon}</span>
              {tab.label}
              {tab.id === "artifacts" && canvasItems.length > 0 && (
                <span className="ml-1 rounded-full bg-accent/20 px-1 text-[0.6rem]">
                  {canvasItems.length}
                </span>
              )}
            </button>
          ))}
        </div>
        {/* Collapse / Close */}
        {onCollapse && (
          <button
            type="button"
            className="rounded-md px-2 py-1 text-xs text-muted hover:bg-line/70 hover:text-ink transition-colors"
            aria-label="Collapse panel"
            title="Collapse panel"
            onClick={onCollapse}
          >
            ⊟
          </button>
        )}
        {!alwaysShow && (
          <button
            type="button"
            className="rounded-md px-2 py-1 text-xs text-muted hover:bg-line/70"
            aria-label="Close canvas"
            onClick={() => setCanvasOpen(false)}
          >
            ✕
          </button>
        )}
      </header>

      {/* Tab Content */}
      <div className="flex min-h-0 flex-1 flex-col">
        {activeTab === "timeline" && <TimelineTab />}
        {activeTab === "plan" && <PlanTab />}
        {activeTab === "subagents" && <SubagentsTab />}
        {activeTab === "artifacts" && <ArtifactsTab onOpenLightbox={onOpenLightbox} />}
        {activeTab === "stats" && <StatsTab />}
      </div>

      {/* TodoBoard lives exclusively in the Plan tab now */}
    </aside>
  );
}
