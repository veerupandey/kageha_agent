/**
 * AgentCanvas — Monitoring panel.
 *
 * Three tabs:
 * - Timeline: Live tool calls with expand/collapse, I/O, duration, status
 * - Artifacts: File previews (images, docs, media)
 * - Stats: Cost, tokens, steps, elapsed time
 */

import { useEffect, useMemo, useState } from "react";
import type { CanvasItem } from "../lib/artifactMedia";
import { artifactDownloadUrl, fileExt, kindLabel } from "../lib/artifactMedia";
import { cn } from "../lib/cn";
import { useAppStore } from "../store";
import type { ActivityStep, ToolCard } from "../api/types";
import { CodeBlock, CodeThumbnail } from "./shared/CodeBlock";


// ── Types ──────────────────────────────────────────────────────────────

type CanvasTab = "timeline" | "artifacts" | "stats";

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

  // Also collect activity steps for context
  const allSteps = useMemo(() => {
    const steps: ActivityStep[] = [];
    for (const m of messages) {
      if (m.role === "assistant" && m.steps) {
        steps.push(...m.steps);
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
          <div className="space-y-0.5 max-h-32 overflow-y-auto">
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


// ── Artifacts Tab ──────────────────────────────────────────────────────

function ArtifactThumb({ item, active, onClick }: {
  item: CanvasItem;
  active: boolean;
  onClick: () => void;
}) {
  const [failed, setFailed] = useState(false);
  const [codePreview, setCodePreview] = useState("");
  useEffect(() => { setFailed(false); }, [item.url]);

  // Fetch first few lines for code thumbnails
  useEffect(() => {
    if (item.kind !== "code") { setCodePreview(""); return; }
    let cancelled = false;
    void fetch(item.url)
      .then((r) => r.text())
      .then((body) => { if (!cancelled) setCodePreview(body.slice(0, 500)); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [item.url, item.kind]);

  return (
    <button
      type="button"
      title={item.path}
      onClick={onClick}
      className={cn(
        "w-[4.5rem] shrink-0 overflow-hidden rounded-lg border text-left transition",
        active ? "border-accent ring-2 ring-accent/25" : "border-line hover:border-accent/40",
      )}
    >
      {item.kind === "image" && !failed ? (
        <img src={item.url} alt="" className="h-12 w-full object-cover" onError={() => setFailed(true)} />
      ) : item.kind === "code" && codePreview ? (
        <div className="h-12 w-full overflow-hidden">
          <CodeThumbnail code={codePreview} filename={item.caption} lines={8} />
        </div>
      ) : (
        <div className="flex h-12 items-center justify-center bg-canvas text-[0.6rem] font-semibold uppercase tracking-wide text-accent">
          {kindLabel(item.kind).slice(0, 4)}
        </div>
      )}
      <span className="block truncate px-1.5 py-1 text-[0.65rem] text-muted">
        {item.caption}
      </span>
    </button>
  );
}

function ArtifactPreview({ item }: { item: CanvasItem }) {
  const sessionId = useAppStore((s) => s.sessionId);
  const [text, setText] = useState("");
  const [textLoading, setTextLoading] = useState(false);

  useEffect(() => {
    if (!item || (item.kind !== "text" && item.kind !== "markdown" && item.kind !== "code")) { setText(""); return; }
    let cancelled = false;
    setTextLoading(true);
    void fetch(item.url).then(r => r.text()).then(body => {
      if (!cancelled) { setText(body.slice(0, 100_000)); setTextLoading(false); }
    }).catch(() => { if (!cancelled) { setText("Could not load."); setTextLoading(false); } });
    return () => { cancelled = true; };
  }, [item?.url, item?.kind]);

  if (item.kind === "image") {
    return <img src={item.url} alt={item.caption} className="mx-auto max-h-64 max-w-full rounded-lg object-contain" />;
  }
  if (item.kind === "video") {
    return <video src={item.url} controls className="mx-auto max-h-64 w-full bg-ink" />;
  }
  if (item.kind === "audio") {
    return <audio src={item.url} controls className="w-full" />;
  }
  if (item.kind === "code") {
    if (textLoading) return <p className="p-2 text-xs text-muted">Loading…</p>;
    return (
      <div className="relative">
        <div className="absolute right-2 top-2 z-10 flex items-center gap-1.5">
          <span className="rounded bg-[#21262d] px-1.5 py-0.5 text-[0.6rem] font-medium text-[#8b949e]">
            {fileExt(item.path).replace(".", "").toUpperCase()}
          </span>
        </div>
        <CodeBlock code={text} filename={item.caption} maxHeight="16rem" />
      </div>
    );
  }
  if (item.kind === "markdown" || item.kind === "text") {
    if (textLoading) return <p className="p-2 text-xs text-muted">Loading…</p>;
    return <pre className="max-h-48 overflow-auto rounded border border-line bg-canvas p-2 font-mono text-[0.65rem] text-ink whitespace-pre-wrap">{text || "(empty)"}</pre>;
  }
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-line bg-canvas px-3 py-6 text-center">
      <span className="text-lg font-semibold text-accent">{kindLabel(item.kind)}</span>
      <a href={item.url} target="_blank" rel="noreferrer" className="text-xs text-accent hover:underline">Open</a>
      <a href={artifactDownloadUrl(sessionId, item.path) || item.url} download={item.caption} className="text-xs text-ink hover:underline">Download</a>
    </div>
  );
}

function ArtifactsTab() {
  const canvasItems = useAppStore((s) => s.canvasItems);
  const canvasSelectedPath = useAppStore((s) => s.canvasSelectedPath);
  const selectCanvasItem = useAppStore((s) => s.selectCanvasItem);
  const refreshArtifacts = useAppStore((s) => s.refreshArtifacts);
  const sessionId = useAppStore((s) => s.sessionId);

  useEffect(() => {
    if (sessionId) void refreshArtifacts();
  }, [sessionId, refreshArtifacts]);

  const selected = useMemo(
    () => canvasItems.find((i) => i.path === canvasSelectedPath) || canvasItems[0] || null,
    [canvasItems, canvasSelectedPath],
  );

  if (!canvasItems.length) {
    return (
      <div className="flex flex-1 items-center justify-center px-4 text-center text-sm text-muted">
        No artifacts yet. Files created will appear here.
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 overflow-x-auto border-b border-line px-2 py-2">
        <div className="flex gap-2">
          {canvasItems.map((item) => (
            <ArtifactThumb
              key={item.path}
              item={item}
              active={item.path === selected?.path}
              onClick={() => selectCanvasItem(item.path)}
            />
          ))}
        </div>
      </div>
      {selected && (
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          <div className="mb-2 flex items-center gap-2">
            <span className="rounded bg-line/80 px-1.5 py-0.5 text-[0.6rem] font-medium uppercase text-muted">
              {kindLabel(selected.kind)}
            </span>
            <span className="truncate text-[0.65rem] text-muted">{selected.path}</span>
          </div>
          <ArtifactPreview item={selected} />
        </div>
      )}
    </div>
  );
}


// ── Main Canvas Component ──────────────────────────────────────────────

const TAB_CONFIG: { id: CanvasTab; label: string; icon: string }[] = [
  { id: "timeline", label: "Timeline", icon: "⚡" },
  { id: "artifacts", label: "Artifacts", icon: "📎" },
  { id: "stats", label: "Stats", icon: "📊" },
];

/** Monitoring canvas with Timeline, Artifacts, and Stats tabs. */
export function AgentCanvas({ alwaysShow, onCollapse }: { alwaysShow?: boolean; onCollapse?: () => void } = {}) {
  const canvasOpen = useAppStore((s) => s.canvasOpen);
  const setCanvasOpen = useAppStore((s) => s.setCanvasOpen);
  const canvasItems = useAppStore((s) => s.canvasItems);
  const runStatus = useAppStore((s) => s.runStatus);
  const [activeTab, setActiveTab] = useState<CanvasTab>("timeline");

  // Auto-switch to timeline when a run starts
  useEffect(() => {
    if (runStatus === "running") setActiveTab("timeline");
  }, [runStatus]);

  // Auto-switch to artifacts when new ones appear
  useEffect(() => {
    if (canvasItems.length > 0 && activeTab === "timeline" && runStatus !== "running") {
      setActiveTab("artifacts");
    }
  }, [canvasItems.length]);

  if (!alwaysShow && !canvasOpen) return null;

  return (
    <aside
      className="flex w-full min-w-0 flex-col border-l border-line bg-surface md:w-[22rem] lg:w-[26rem]"
      id="agent-canvas"
      aria-label="Agent monitoring canvas"
    >
      {/* Header */}
      <header className="flex h-12 shrink-0 items-center gap-1 border-b border-line px-2">
        {/* Tabs */}
        <div className="flex min-w-0 flex-1 gap-0.5">
          {TAB_CONFIG.map((tab) => (
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
        {activeTab === "artifacts" && <ArtifactsTab />}
        {activeTab === "stats" && <StatsTab />}
      </div>
    </aside>
  );
}
