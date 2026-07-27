import { useEffect, useRef, useState } from "react";
import { useAppStore } from "../store";
import { ApprovalBanner } from "./ApprovalBanner";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";

interface StageProps {
  onToggleSessions?: () => void;
}

/** The deliberately small core surface: one conversation and one session. */
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
    (sessionId ? sessionId.slice(0, 8) : "new session");

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
    <main className="stage" id="stage">
      <header className="stage-bar stage-bar-chrome">
        <div className="stage-meta">
          <button
            type="button"
            className="btn ghost icon mobile-only sessions-menu-btn"
            aria-label="Open sessions"
            title="Sessions"
            onClick={() => onToggleSessions?.()}
          >
            ☰
          </button>
          <span className="status-dot" id="run-status-dot" data-status={runStatus} />
          <span id="run-status-label">{statusLabel}</span>
          <span className="meta-sep" aria-hidden="true">·</span>
          {editingTitle ? (
            <input
              ref={titleInputRef}
              type="text"
              className="session-title-input"
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
              className="session-title-btn"
              disabled={!sessionId}
              onClick={() => {
                setTitleDraft(sessionTitle || "");
                setEditingTitle(true);
              }}
            >
              {title}
            </button>
          )}
        </div>
      </header>

      <div className="stage-split" id="stage-split">
        <div className="stage-chat" id="stage-chat">
          <section className="conversation" id="conversation" aria-live="polite">
            <MessageList messages={messages} />
          </section>
          <ApprovalBanner />
          {error ? (
            <p className="stage-error" role="alert">
              <span className="stage-error-text">{error}</span>
              <button type="button" className="btn ghost compact" onClick={clearError}>
                Dismiss
              </button>
            </p>
          ) : null}
          <Composer />
        </div>
      </div>
    </main>
  );
}
