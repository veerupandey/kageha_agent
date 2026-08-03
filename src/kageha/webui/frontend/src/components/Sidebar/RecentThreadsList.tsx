import { useCallback, useState } from "react";
import { createPortal } from "react-dom";
import type { SessionSummary } from "../../api/types";
import { cn } from "../../lib/cn";
import { useAppStore } from "../../store";
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

function timeAgo(dateStr?: string | number | null): string {
  if (!dateStr && dateStr !== 0) return "";
  let ts: number;
  if (typeof dateStr === "number") {
    // Unix timestamp (seconds or milliseconds)
    ts = dateStr > 1e12 ? dateStr : dateStr * 1000;
  } else {
    const parsed = Number(dateStr);
    if (!isNaN(parsed) && parsed > 0) {
      // Numeric string — treat as Unix timestamp
      ts = parsed > 1e12 ? parsed : parsed * 1000;
    } else {
      ts = new Date(dateStr).getTime();
    }
  }
  if (!ts || isNaN(ts)) return "";
  const diff = Date.now() - ts;
  if (diff < 0) return "now";
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  const years = Math.floor(months / 12);
  return `${years}y ago`;
}

function statusLabel(session: SessionSummary): string | null {
  const s = session.turn_status || session.status || "";
  if (s === "running") return "Running";
  if (s === "waiting_approval") return "Waiting";
  if (s === "error") return "Error";
  return null;
}

// ── Context Menu (portaled to body) ───────────────────────────────────

interface ContextMenuProps {
  x: number;
  y: number;
  session: SessionSummary;
  onClose: () => void;
}

function SessionContextMenu({ x, y, session, onClose }: ContextMenuProps) {
  const renameSession = useAppStore((s) => s.renameSession);
  const deleteSession = useAppStore((s) => s.deleteSession);
  const pinSession = useAppStore((s) => s.pinSession);
  const archiveSession = useAppStore((s) => s.archiveSession);
  const openSession = useAppStore((s) => s.openSession);

  const handleRename = useCallback(async () => {
    const current = shortTitle(session);
    const id = session.session_id;
    onClose();
    await new Promise((r) => setTimeout(r, 50));
    const newTitle = window.prompt("Rename thread:", current);
    if (newTitle !== null && newTitle.trim() && newTitle.trim() !== current) {
      await openSession(id);
      await renameSession(newTitle.trim());
    }
  }, [session, onClose, renameSession, openSession]);

  const handleDelete = useCallback(async () => {
    const title = shortTitle(session);
    const id = session.session_id;
    onClose();
    // Small delay to let the portal unmount before showing confirm
    await new Promise((r) => setTimeout(r, 50));
    const confirmed = window.confirm(
      `Delete "${title}"? This cannot be undone.`,
    );
    if (confirmed) {
      try {
        await deleteSession(id);
      } catch {
        // ignore — toast is shown by store
      }
    }
  }, [session, onClose, deleteSession]);

  const handlePin = useCallback(async () => {
    onClose();
    await pinSession(session.session_id, !session.pinned);
  }, [session, onClose, pinSession]);

  const handleArchive = useCallback(async () => {
    onClose();
    await archiveSession(session.session_id, !session.archived);
  }, [session, onClose, archiveSession]);

  // Clamp so menu doesn't overflow the viewport
  const top = Math.min(y, window.innerHeight - 210);
  const left = Math.min(x, window.innerWidth - 180);

  return createPortal(
    <>
      <div
        className="fixed inset-0 z-[9998]"
        onClick={onClose}
        onContextMenu={(e) => {
          e.preventDefault();
          onClose();
        }}
      />
      <div
        className="fixed z-[9999] min-w-[148px] rounded-lg border border-[var(--color-line-strong)] bg-[var(--color-surface)] py-1.5 shadow-xl"
        style={{ top, left }}
        role="menu"
      >
        <MenuItem label="Rename" icon="✎" onClick={handleRename} />
        <MenuItem
          label={session.pinned ? "Unpin" : "Pin"}
          icon={session.pinned ? "✦" : "☆"}
          onClick={handlePin}
        />
        <MenuItem
          label={session.archived ? "Unarchive" : "Archive"}
          icon="▣"
          onClick={handleArchive}
        />
        <div className="my-1 mx-2 border-t border-[var(--color-line)]" />
        <MenuItem label="Delete" icon="✕" onClick={handleDelete} danger />
      </div>
    </>,
    document.body,
  );
}

function MenuItem({
  label,
  icon,
  onClick,
  danger,
}: {
  label: string;
  icon: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className={cn(
        "flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-[0.82rem] transition-colors",
        danger
          ? "text-[var(--color-danger)] hover:bg-[var(--color-danger-soft)]"
          : "text-ink hover:bg-[var(--color-accent-soft)]",
      )}
      onClick={onClick}
    >
      <span
        className={cn(
          "inline-flex h-5 w-5 shrink-0 items-center justify-center text-xs",
          danger ? "text-[var(--color-danger)]" : "text-muted",
        )}
      >
        {icon}
      </span>
      {label}
    </button>
  );
}

// ── Thread Item ───────────────────────────────────────────────────────

interface ThreadItemProps {
  session: SessionSummary;
  active: boolean;
  onOpen: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
}

function ThreadItem({
  session,
  active,
  onOpen,
  onContextMenu,
}: ThreadItemProps) {
  const dot = sessionDotStatus(session);
  const status = statusLabel(session);
  const title = shortTitle(session);
  const ago = timeAgo(session.updated_at);

  return (
    <li>
      <button
        type="button"
        className={cn(
          "group relative flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left transition-colors",
          active
            ? "bg-[var(--color-accent-soft)]"
            : "hover:bg-line/40",
        )}
        onClick={onOpen}
        onContextMenu={onContextMenu}
      >
        {/* Status dot */}
        <div className="mt-1 shrink-0">
          <StatusDot status={dot} />
        </div>

        {/* Content */}
        <div className="min-w-0 flex-1 overflow-hidden">
          <div className="flex items-center gap-1">
            {session.pinned && (
              <span className="shrink-0 text-[0.65rem] text-accent">✦</span>
            )}
            <p className="truncate text-[0.82rem] font-medium leading-snug text-ink">
              {title}
            </p>
          </div>
          {session.objective && (
            <p className="mt-0.5 truncate text-[0.72rem] leading-snug text-muted">
              {session.objective}
            </p>
          )}
          <div className="mt-0.5 flex items-center gap-1.5">
            {status && (
              <span
                className={cn(
                  "rounded px-1 py-px text-[0.6rem] font-medium leading-none",
                  dot === "active" && "bg-accent/15 text-accent",
                  dot === "waiting" &&
                    "bg-[var(--color-warn-soft)] text-[var(--color-warn)]",
                  dot === "error" &&
                    "bg-[var(--color-danger-soft)] text-[var(--color-danger)]",
                )}
              >
                {status}
              </span>
            )}
            <span className="text-[0.6rem] text-faint">{ago}</span>
          </div>
        </div>

        {/* Three-dot hover trigger */}
        <span
          className="absolute right-1 top-1 hidden h-6 w-6 shrink-0 items-center justify-center rounded text-sm text-faint hover:bg-line/60 hover:text-ink group-hover:inline-flex"
          onClick={(e) => {
            e.stopPropagation();
            onContextMenu(e);
          }}
          aria-label="Thread options"
        >
          ⋯
        </span>
      </button>
    </li>
  );
}

// ── Main Component ────────────────────────────────────────────────────

export function RecentThreadsList({
  sessions,
  activeSessionId,
  onOpen,
}: RecentThreadsListProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    session: SessionSummary;
  } | null>(null);

  const recent = sessions.filter((s) => !s.archived).slice(0, 15);

  const handleContextMenu = useCallback(
    (e: React.MouseEvent, session: SessionSummary) => {
      e.preventDefault();
      e.stopPropagation();
      setContextMenu({ x: e.clientX, y: e.clientY, session });
    },
    [],
  );

  return (
    <div className="px-3 py-2">
      <button
        type="button"
        className="mb-1.5 flex w-full items-center justify-between"
        onClick={() => setCollapsed(!collapsed)}
      >
        <p className="text-xs font-semibold uppercase tracking-wide text-faint">
          Recent threads
        </p>
        <span
          className="text-xs text-faint hover:text-ink transition-transform"
          style={{ transform: collapsed ? "rotate(-90deg)" : undefined }}
        >
          ▾
        </span>
      </button>

      {!collapsed && (
        <ul className="space-y-0.5">
          {recent.map((session) => (
            <ThreadItem
              key={session.session_id}
              session={session}
              active={session.session_id === activeSessionId}
              onOpen={() => onOpen(session.session_id)}
              onContextMenu={(e) => handleContextMenu(e, session)}
            />
          ))}
          {recent.length === 0 && (
            <li className="rounded-lg border border-dashed border-[var(--color-line)] px-3 py-4 text-center text-xs text-faint">
              No threads yet
            </li>
          )}
        </ul>
      )}

      {contextMenu && (
        <SessionContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          session={contextMenu.session}
          onClose={() => setContextMenu(null)}
        />
      )}
    </div>
  );
}
