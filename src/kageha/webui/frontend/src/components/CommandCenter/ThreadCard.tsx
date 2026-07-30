import type { SessionSummary } from "../../api/types";

interface ThreadCardProps {
  session: SessionSummary;
  onClick: () => void;
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

export function ThreadCard({ session, onClick }: ThreadCardProps) {
  const title =
    (session.title && session.title.trim()) || session.session_id.slice(0, 8);

  return (
    <button
      type="button"
      className="ka-card flex w-full flex-col gap-1.5 text-left transition-all"
      onClick={onClick}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 flex-1 truncate text-sm font-medium text-ink">
          {session.pinned && (
            <span className="mr-1 text-accent" aria-hidden="true">☆</span>
          )}
          {title}
        </p>
        <span className="shrink-0 text-[0.65rem] text-faint">
          {timeAgo(session.updated_at)}
        </span>
      </div>
      {session.objective && (
        <p className="line-clamp-2 text-xs text-muted">{session.objective}</p>
      )}
    </button>
  );
}
