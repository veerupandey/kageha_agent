import type { SessionSummary } from "../../api/types";
import { StatusDot } from "../shared/StatusDot";
import type { DotStatus } from "../shared/StatusDot";

interface RecentThreadsListProps {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  onOpen: (sessionId: string) => void;
}

function sessionDotStatus(session: SessionSummary): DotStatus {
  if (session.status === "running" || session.turn_status === "running") return "active";
  if (session.status === "waiting" || session.turn_status === "waiting_approval") return "waiting";
  if (session.status === "error") return "error";
  return "idle";
}

function shortTitle(session: SessionSummary): string {
  if (session.title && session.title.trim()) return session.title.trim();
  return session.session_id.slice(0, 8);
}

function timeAgo(dateStr?: string | null): string {
  if (!dateStr) return "";
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

export function RecentThreadsList({
  sessions,
  activeSessionId,
  onOpen,
}: RecentThreadsListProps) {
  const recent = sessions
    .filter((s) => !s.archived)
    .slice(0, 10);

  return (
    <div className="px-3 py-2">
      <div className="mb-1 flex items-center justify-between">
        <p className="text-xs font-semibold uppercase tracking-wide text-faint">
          Recent threads
        </p>
        <button
          type="button"
          className="text-xs text-faint hover:text-ink"
        >
          ▾
        </button>
      </div>
      <ul className="space-y-0.5">
        {recent.map((session) => (
          <li key={session.session_id}>
            <button
              type="button"
              className={`ka-sidebar-item w-full ${session.session_id === activeSessionId ? "active" : ""}`}
              onClick={() => onOpen(session.session_id)}
            >
              <StatusDot status={sessionDotStatus(session)} />
              <div className="min-w-0 flex-1 text-left">
                <p className="truncate text-sm">
                  {session.pinned && (
                    <span className="mr-1 text-accent" aria-hidden="true">✦</span>
                  )}
                  {shortTitle(session)}
                </p>
                {session.objective && (
                  <p className="truncate text-xs text-faint">
                    {session.objective}
                  </p>
                )}
              </div>
              <span className="shrink-0 text-[0.65rem] text-faint">
                {timeAgo(session.updated_at)}
              </span>
            </button>
          </li>
        ))}
        {recent.length === 0 && (
          <li className="px-2 py-3 text-xs text-faint">No threads yet</li>
        )}
      </ul>
    </div>
  );
}
