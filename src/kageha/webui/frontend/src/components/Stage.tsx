import { useEffect, useRef, useState } from "react";
import { useAppStore } from "../store";
import { ApprovalBanner } from "./ApprovalBanner";
import { Composer } from "./Composer";
import { DesignPanel } from "./DesignPanel";
import { MessageList } from "./MessageList";
import { TaskTabs } from "./TaskTabs";
import { TodoBoard } from "./TodoBoard";
import { Workbench } from "./Workbench";

interface StageProps {
  sessionsOpen?: boolean;
  onToggleSessions?: () => void;
}

export function Stage({ onToggleSessions }: StageProps) {
  const messages = useAppStore((s) => s.messages);
  const runStatus = useAppStore((s) => s.runStatus);
  const statusLabel = useAppStore((s) => s.statusLabel);
  const sessionTitle = useAppStore((s) => s.sessionTitle);
  const sessionId = useAppStore((s) => s.sessionId);
  const error = useAppStore((s) => s.error);
  const clearError = useAppStore((s) => s.clearError);
  const drawers = useAppStore((s) => s.drawers);
  const toggleDrawer = useAppStore((s) => s.toggleDrawer);
  const openDrawer = useAppStore((s) => s.openDrawer);
  const renameSession = useAppStore((s) => s.renameSession);
  const loadDesign = useAppStore((s) => s.loadDesign);
  const showToast = useAppStore((s) => s.showToast);

  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [moreOpen, setMoreOpen] = useState(false);
  const titleInputRef = useRef<HTMLInputElement>(null);
  const moreRef = useRef<HTMLDetailsElement>(null);

  const title =
    (sessionTitle && sessionTitle.trim()) ||
    (sessionId ? sessionId.slice(0, 8) : "new session");

  useEffect(() => {
    if (editingTitle) {
      titleInputRef.current?.focus();
      titleInputRef.current?.select();
    }
  }, [editingTitle]);

  useEffect(() => {
    if (!moreOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (!moreRef.current?.contains(e.target as Node)) {
        setMoreOpen(false);
        if (moreRef.current) moreRef.current.open = false;
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [moreOpen]);

  const commitTitle = async () => {
    const next = titleDraft.trim();
    setEditingTitle(false);
    if (!sessionId) return;
    const prev = (sessionTitle && sessionTitle.trim()) || "";
    if (next === prev) return;
    try {
      await renameSession(next);
    } catch (err) {
      showToast(
        `Could not rename: ${err instanceof Error ? err.message : err}`,
      );
    }
  };

  const openDesign = () => {
    if (drawers.design) {
      toggleDrawer("design");
    } else {
      openDrawer("design");
      void loadDesign().catch(() => {});
    }
    setMoreOpen(false);
    if (moreRef.current) moreRef.current.open = false;
  };

  return (
    <main
      className={`stage${drawers.workbench ? " workbench-open" : ""}`}
      id="stage"
    >
      <header className="stage-bar stage-bar-chrome">
        <div className="stage-meta">
          <button
            type="button"
            className="btn ghost icon mobile-only sessions-menu-btn"
            id="btn-sessions-menu"
            aria-label="Open sessions"
            title="Sessions"
            onClick={() => onToggleSessions?.()}
          >
            ☰
          </button>
          <span
            className="status-dot"
            id="run-status-dot"
            data-status={runStatus}
          />
          <span id="run-status-label">{statusLabel}</span>
          <span className="meta-sep" aria-hidden="true">
            ·
          </span>
          <span className="session-title-group" id="session-title-group">
            {editingTitle ? (
              <input
                ref={titleInputRef}
                type="text"
                className="session-title-input"
                id="active-session-title-input"
                maxLength={200}
                aria-label="Session title"
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onBlur={() => {
                  void commitTitle();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void commitTitle();
                  }
                  if (e.key === "Escape") {
                    e.preventDefault();
                    setEditingTitle(false);
                  }
                }}
              />
            ) : (
              <button
                type="button"
                className="session-title-btn"
                id="active-session-title"
                title="Click to rename session"
                disabled={!sessionId}
                onClick={() => {
                  setTitleDraft(
                    (sessionTitle && sessionTitle.trim()) || "",
                  );
                  setEditingTitle(true);
                }}
              >
                {title}
              </button>
            )}
          </span>
        </div>
        <div className="stage-bar-actions stage-bar-group">
          <div className="stage-bar-wide">
            <button
              type="button"
              className={`btn ghost${drawers.workbench ? " is-active" : ""}`}
              id="btn-workbench"
              title="Stage workbench · Best-of-N · review"
              aria-pressed={drawers.workbench}
              onClick={() => toggleDrawer("workbench")}
            >
              Workbench
            </button>
            <button
              type="button"
              className={`btn ghost${drawers.jobs ? " is-active" : ""}`}
              id="btn-jobs"
              title="Background jobs dashboard"
              onClick={() => toggleDrawer("jobs")}
            >
              Jobs
            </button>
            <button
              type="button"
              className={`btn ghost${drawers.labs ? " is-active" : ""}`}
              id="btn-labs"
              title="Project labs"
              onClick={() => toggleDrawer("labs")}
            >
              Labs
            </button>
            <button
              type="button"
              className={`btn ghost${drawers.settings ? " is-active" : ""}`}
              id="btn-settings"
              title="Settings"
              aria-pressed={drawers.settings}
              onClick={() => toggleDrawer("settings")}
            >
              Settings
            </button>
            <span className="stage-bar-sep" aria-hidden="true" />
            <button
              type="button"
              className={`btn ghost${drawers.design ? " is-active" : ""}`}
              id="btn-design"
              title="Plan / Spec panel"
              onClick={openDesign}
            >
              Design
            </button>
            <button
              type="button"
              className={`btn ghost${drawers.artifacts ? " is-active" : ""}`}
              id="btn-artifacts"
              onClick={() => toggleDrawer("artifacts")}
            >
              Artifacts
            </button>
            <button
              type="button"
              className={`btn ghost${drawers.memory ? " is-active" : ""}`}
              id="btn-memory"
              onClick={() => toggleDrawer("memory")}
            >
              Memory
            </button>
          </div>
          <details
            ref={moreRef}
            className="stage-bar-more"
            onToggle={(e) => setMoreOpen((e.target as HTMLDetailsElement).open)}
          >
            <summary
              className="btn ghost icon stage-bar-more-summary"
              aria-label="More tools"
              title="More"
            >
              ⋯
            </summary>
            <div className="stage-bar-more-menu" role="menu">
              <button
                type="button"
                className="stage-bar-more-item"
                role="menuitem"
                onClick={() => {
                  toggleDrawer("workbench");
                  setMoreOpen(false);
                  if (moreRef.current) moreRef.current.open = false;
                }}
              >
                Workbench
              </button>
              <button
                type="button"
                className="stage-bar-more-item"
                role="menuitem"
                onClick={() => {
                  toggleDrawer("jobs");
                  setMoreOpen(false);
                  if (moreRef.current) moreRef.current.open = false;
                }}
              >
                Jobs
              </button>
              <button
                type="button"
                className="stage-bar-more-item"
                role="menuitem"
                onClick={() => {
                  toggleDrawer("labs");
                  setMoreOpen(false);
                  if (moreRef.current) moreRef.current.open = false;
                }}
              >
                Labs
              </button>
              <button
                type="button"
                className="stage-bar-more-item"
                role="menuitem"
                onClick={openDesign}
              >
                Design
              </button>
              <button
                type="button"
                className="stage-bar-more-item"
                role="menuitem"
                onClick={() => {
                  toggleDrawer("artifacts");
                  setMoreOpen(false);
                  if (moreRef.current) moreRef.current.open = false;
                }}
              >
                Artifacts
              </button>
              <button
                type="button"
                className="stage-bar-more-item"
                role="menuitem"
                onClick={() => {
                  toggleDrawer("memory");
                  setMoreOpen(false);
                  if (moreRef.current) moreRef.current.open = false;
                }}
              >
                Memory
              </button>
              <button
                type="button"
                className="stage-bar-more-item"
                role="menuitem"
                onClick={() => {
                  toggleDrawer("settings");
                  setMoreOpen(false);
                  if (moreRef.current) moreRef.current.open = false;
                }}
              >
                Settings
              </button>
            </div>
          </details>
        </div>
      </header>

      <TaskTabs />

      <div className="stage-split" id="stage-split">
        <div className="stage-chat" id="stage-chat">
          <section
            className="conversation"
            id="conversation"
            aria-live="polite"
          >
            <MessageList messages={messages} />
          </section>

          <ApprovalBanner />
          <TodoBoard />
          <DesignPanel />

          {error ? (
            <p className="stage-error" role="alert">
              <span className="stage-error-text">{error}</span>
              <button
                type="button"
                className="btn ghost compact stage-error-dismiss"
                aria-label="Dismiss error"
                onClick={() => clearError()}
              >
                Dismiss
              </button>
            </p>
          ) : null}

          <Composer />
        </div>

        <Workbench />
      </div>
    </main>
  );
}
