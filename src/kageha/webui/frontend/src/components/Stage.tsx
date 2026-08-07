import { useEffect, useRef, useState } from "react";
import { cn } from "../lib/cn";
import { Icon } from "../lib/icons";
import { useAppStore } from "../store";
import { ApprovalBanner } from "./ApprovalBanner";
import { AgentCanvas } from "./AgentCanvas";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { TodoBoard } from "./TodoBoard";

interface StageProps {
  onToggleSessions?: () => void;
}

function statusDotClass(status: string): string {
  if (status === "running" || status === "streaming") return "bg-accent";
  if (status === "error") return "bg-danger";
  if (status === "cancelled") return "bg-faint";
  if (status === "waiting_approval" || status === "awaiting_plan_approval") {
    return "bg-warn";
  }
  return "bg-line-strong";
}

/** One conversation column: title, transcript, approvals, composer. */
export function Stage({ onToggleSessions }: StageProps) {
  const messages = useAppStore((s) => s.messages);
  const runStatus = useAppStore((s) => s.runStatus);
  const statusLabel = useAppStore((s) => s.statusLabel);
  const sessionTitle = useAppStore((s) => s.sessionTitle);
  const sessionId = useAppStore((s) => s.sessionId);
  const error = useAppStore((s) => s.error);
  const clearError = useAppStore((s) => s.clearError);
  const renameSession = useAppStore((s) => s.renameSession);
  const showToast = useAppStore((s) => s.showToast);
  const canvasOpen = useAppStore((s) => s.canvasOpen);
  const setCanvasOpen = useAppStore((s) => s.setCanvasOpen);
  const refreshArtifacts = useAppStore((s) => s.refreshArtifacts);
  const canvasItems = useAppStore((s) => s.canvasItems);
  const todoBoard = useAppStore((s) => s.todoBoard);
  const theme = useAppStore((s) => s.prefs.theme);
  const setPrefs = useAppStore((s) => s.setPrefs);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const titleInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingTitle) {
      titleInputRef.current?.focus();
      titleInputRef.current?.select();
    }
  }, [editingTitle]);

  const title =
    (sessionTitle && sessionTitle.trim()) ||
    (sessionId ? sessionId.slice(0, 8) : "New chat");

  const commitTitle = async () => {
    const next = titleDraft.trim();
    setEditingTitle(false);
    if (!sessionId || next === (sessionTitle || "")) return;
    try {
      await renameSession(next);
    } catch (err) {
      showToast(`Could not rename: ${err instanceof Error ? err.message : err}`);
    }
  };

  return (
    <main className="flex min-h-0 min-w-0 flex-1 flex-col" id="stage">
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line px-3 md:px-5">
        <button
          type="button"
          className="ka-icon-btn h-8 w-8 text-muted md:hidden"
          aria-label="Open sessions"
          title="Sessions"
          onClick={() => onToggleSessions?.()}
        >
          <Icon.Menu size={18} />
        </button>
        <span
          className={cn(
            "h-2 w-2 shrink-0 rounded-full",
            statusDotClass(runStatus),
          )}
          id="run-status-dot"
          data-status={runStatus}
          aria-hidden="true"
        />
        <span
          id="run-status-label"
          className="truncate text-sm text-muted"
        >
          {statusLabel || "Ready"}
        </span>
        <span className="text-faint" aria-hidden="true">
          ·
        </span>
        {editingTitle ? (
          <input
            ref={titleInputRef}
            type="text"
            className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2 py-1 text-sm"
            aria-label="Session title"
            value={titleDraft}
            onChange={(e) => setTitleDraft(e.target.value)}
            onBlur={() => void commitTitle()}
            onKeyDown={(e) => {
              if (e.key === "Enter") void commitTitle();
              if (e.key === "Escape") setEditingTitle(false);
            }}
          />
        ) : (
          <button
            type="button"
            className="min-w-0 flex-1 truncate text-left text-sm font-medium text-ink disabled:opacity-50"
            disabled={!sessionId}
            onClick={() => {
              setTitleDraft(sessionTitle || "");
              setEditingTitle(true);
            }}
          >
            {title}
          </button>
        )}
        <button
          type="button"
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium text-muted hover:bg-line/70 hover:text-ink"
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          onClick={() =>
            setPrefs({ theme: theme === "dark" ? "light" : "dark" })
          }
        >
          {theme === "dark" ? <Icon.Sun size={14} /> : <Icon.Moon size={14} />}
          <span className="hidden sm:inline">{theme === "dark" ? "Light" : "Dark"}</span>
        </button>
        <button
          type="button"
          className={cn(
            "inline-flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium",
            canvasOpen
              ? "bg-accent-soft text-accent"
              : "text-muted hover:bg-line/70 hover:text-ink",
          )}
          title="Toggle artifact canvas"
          disabled={!sessionId}
          onClick={() => {
            const next = !canvasOpen;
            setCanvasOpen(next);
            if (next) void refreshArtifacts();
          }}
        >
          <Icon.Canvas size={14} />
          Canvas{canvasItems.length ? ` · ${canvasItems.length}` : ""}
        </button>
        <button
          type="button"
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium text-muted hover:bg-line/70 hover:text-ink"
          title="Export session as shareable HTML"
          disabled={!sessionId}
          onClick={() => {
            if (!sessionId) return;
            window.open(`/api/sessions/${sessionId}/share`, "_blank");
          }}
        >
          <Icon.Share size={14} />
          <span className="hidden sm:inline">Share</span>
        </button>
      </header>

      <div className="flex min-h-0 flex-1">
        <div className="relative flex min-h-0 min-w-0 flex-1 flex-col" id="stage-chat">
          <section
            className="min-h-0 flex-1 overflow-y-auto"
            id="conversation"
          >
            <MessageList messages={messages} />
          </section>
          {/* Live todo/milestone board — slides in while agent is running */}
          {todoBoard && todoBoard.total > 0 && (
            <div
              className={cn(
                "shrink-0 border-t border-line px-4 py-2.5 transition-all duration-300",
                "animate-[slideUp_0.3s_ease-out]",
              )}
            >
              <TodoBoard board={todoBoard} />
            </div>
          )}
          <ApprovalBanner />
          {error ? (
            <p
              className="flex items-start gap-3 border-t border-danger/20 bg-danger-soft px-4 py-2 text-sm text-danger"
              role="alert"
            >
              <span className="min-w-0 flex-1">{error}</span>
              <button
                type="button"
                className="shrink-0 text-danger underline-offset-2 hover:underline"
                onClick={clearError}
              >
                Dismiss
              </button>
            </p>
          ) : null}
          <Composer />
        </div>
        <div className="hidden min-h-0 md:flex md:max-w-[38%] lg:max-w-[35%]">
          <AgentCanvas />
        </div>
      </div>
    </main>
  );
}
