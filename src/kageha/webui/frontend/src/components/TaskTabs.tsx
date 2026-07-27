import { useAppStore } from "../store";

function shortId(id: string): string {
  return id.length > 10 ? id.slice(0, 8) : id;
}

export function TaskTabs() {
  const tabs = useAppStore((s) => s.tabs);
  const sessionId = useAppStore((s) => s.sessionId);
  const sessionTitle = useAppStore((s) => s.sessionTitle);
  const sessions = useAppStore((s) => s.sessions);
  const runs = useAppStore((s) => s.runs);
  const openSession = useAppStore((s) => s.openSession);
  const closeTab = useAppStore((s) => s.closeTab);
  const newSession = useAppStore((s) => s.newSession);

  if (!tabs.length) return null;

  return (
    <div
      className="task-tabs"
      id="task-tabs"
      role="tablist"
      aria-label="Parallel tasks"
    >
      {tabs.map((id) => {
        const run = runs[id];
        const meta = sessions.find((s) => s.session_id === id);
        const title =
          (id === sessionId && sessionTitle) ||
          meta?.title ||
          shortId(id) ||
          "task";
        const status = run?.waitingApproval
          ? "waiting_approval"
          : run?.sending
            ? "running"
            : run?.needsAttention
              ? "success"
              : meta?.turn_status || run?.status || "idle";
        return (
          <div
            key={id}
            className={
              "task-tab" +
              (id === sessionId ? " active" : "") +
              (run?.sending ? " running" : "") +
              (run?.needsAttention ? " needs-attention" : "") +
              (run?.waitingApproval ? " waiting-approval" : "")
            }
            data-session-id={id}
            role="tab"
            aria-selected={id === sessionId}
          >
            <button
              type="button"
              className="task-tab-main"
              onClick={() => {
                if (id !== sessionId) {
                  void openSession(id).catch((err: Error) =>
                    alert(err.message || err),
                  );
                }
              }}
            >
              <span className="task-tab-dot" data-status={status} />
              <span className="task-tab-label" title={id}>
                {String(title).trim() || shortId(id)}
              </span>
            </button>
            <button
              type="button"
              className="task-tab-close"
              aria-label="Close tab"
              onClick={() => {
                if (run?.sending) {
                  const ok = window.confirm(
                    "This task is still running. Close the tab anyway?",
                  );
                  if (!ok) return;
                }
                void closeTab(id);
              }}
            >
              ×
            </button>
          </div>
        );
      })}
      <button
        type="button"
        className="task-tab task-tab-add"
        title="New parallel task"
        aria-label="New parallel task"
        onClick={() => {
          void newSession({ parallel: true }).catch((err: Error) =>
            alert(err.message || err),
          );
        }}
      >
        +
      </button>
    </div>
  );
}
