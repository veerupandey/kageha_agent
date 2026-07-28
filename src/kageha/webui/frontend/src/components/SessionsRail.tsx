import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useMemo, useState } from "react";
import type { SessionSummary } from "../api/types";
import { cn } from "../lib/cn";
import { useAppStore } from "../store";
import { filterSessionsForRail } from "../store/sessions";

function shortId(id: string): string {
  return id.length > 10 ? id.slice(0, 8) : id;
}

function SessionRow({
  session,
  active,
  onOpen,
}: {
  session: SessionSummary;
  active: boolean;
  onOpen: () => void;
}) {
  const pinSession = useAppStore((s) => s.pinSession);
  const archiveSession = useAppStore((s) => s.archiveSession);
  const deleteSession = useAppStore((s) => s.deleteSession);
  const showToast = useAppStore((s) => s.showToast);

  const title =
    (session.title && String(session.title).trim()) ||
    shortId(session.session_id);

  const run = async (action: () => Promise<void>, okToast?: string) => {
    try {
      await action();
      if (okToast) showToast(okToast);
    } catch {
      /* toast already shown by store */
    }
  };

  const onDelete = () => {
    if (
      !window.confirm(`Delete session “${title}”? This cannot be undone.`)
    ) {
      return;
    }
    void run(
      () => deleteSession(session.session_id),
      `Deleted · ${shortId(session.session_id)}`,
    );
  };

  return (
    <li className="group relative">
      <button
        type="button"
        className={cn(
          "flex w-full flex-col gap-0.5 rounded-md px-2.5 py-2 text-left transition-colors",
          active ? "bg-accent-soft text-ink" : "hover:bg-line/60",
          session.archived && "opacity-70",
        )}
        onClick={onOpen}
      >
        <span className="truncate text-[0.85rem] font-medium">
          {session.pinned ? (
            <span className="mr-1 text-accent" aria-hidden="true">
              ✦
            </span>
          ) : null}
          {title}
        </span>
        <span className="font-mono text-[0.7rem] text-faint">
          {shortId(session.session_id)}
          {session.archived ? " · archived" : ""}
        </span>
      </button>

      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button
            type="button"
            className="absolute right-1 top-1.5 hidden h-7 w-7 items-center justify-center rounded-md text-muted hover:bg-surface group-hover:inline-flex data-[state=open]:inline-flex"
            aria-label="Session actions"
            onClick={(e) => e.stopPropagation()}
          >
            ···
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content
            className="z-50 min-w-36 rounded-lg border border-line bg-surface p-1 shadow-lg"
            sideOffset={4}
            align="end"
          >
            <DropdownMenu.Item
              className="cursor-pointer rounded-md px-2.5 py-1.5 text-sm outline-none data-[highlighted]:bg-accent-soft"
              onSelect={() =>
                void run(
                  () => pinSession(session.session_id, !session.pinned),
                  session.pinned ? "Unpinned" : "Pinned",
                )
              }
            >
              {session.pinned ? "Unpin" : "Pin"}
            </DropdownMenu.Item>
            <DropdownMenu.Item
              className="cursor-pointer rounded-md px-2.5 py-1.5 text-sm outline-none data-[highlighted]:bg-accent-soft"
              onSelect={() =>
                void run(
                  () =>
                    archiveSession(session.session_id, !session.archived),
                  session.archived ? "Unarchived" : "Archived",
                )
              }
            >
              {session.archived ? "Unarchive" : "Archive"}
            </DropdownMenu.Item>
            <DropdownMenu.Item
              className="cursor-pointer rounded-md px-2.5 py-1.5 text-sm text-danger outline-none data-[highlighted]:bg-danger-soft"
              onSelect={onDelete}
            >
              Delete…
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </li>
  );
}

interface SessionsRailProps {
  open?: boolean;
  onClose?: () => void;
}

export function SessionsRail({ open = false, onClose }: SessionsRailProps) {
  const sessions = useAppStore((s) => s.sessions);
  const sessionId = useAppStore((s) => s.sessionId);
  const openSession = useAppStore((s) => s.openSession);
  const newChat = useAppStore((s) => s.newChat);
  const [query, setQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);

  const filtered = useMemo(
    () =>
      filterSessionsForRail(sessions, {
        showArchived,
        query,
      }),
    [sessions, query, showArchived],
  );

  return (
    <aside
      className={cn(
        "fixed inset-y-0 left-0 z-50 flex w-64 flex-col border-r border-line bg-canvas transition-transform md:static md:z-0 md:translate-x-0",
        open ? "translate-x-0" : "-translate-x-full md:translate-x-0",
      )}
      id="sessions-panel"
    >
      <header className="flex h-12 items-center justify-between gap-2 border-b border-line px-3">
        <p className="text-xs font-semibold uppercase tracking-[0.08em] text-faint">
          Sessions
        </p>
        <button
          type="button"
          className="rounded-md px-2.5 py-1 text-sm font-medium text-accent hover:bg-accent-soft"
          id="btn-new-session"
          title="New chat"
          onClick={() => {
            newChat()
              .then(() => onClose?.())
              .catch((err) => alert(err.message || err));
          }}
        >
          New
        </button>
      </header>

      <div className="px-3 py-2">
        <label className="sr-only" htmlFor="sessions-search-input">
          Search sessions
        </label>
        <input
          id="sessions-search-input"
          type="search"
          className="w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm outline-none placeholder:text-faint focus:border-accent/40"
          placeholder="Search…"
          autoComplete="off"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {sessions.length === 0 ? (
        <div className="px-4 py-8 text-sm text-muted">
          <p className="font-medium text-ink">No sessions yet</p>
          <p className="mt-1">Start a new chat to begin.</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="px-4 py-8 text-sm text-muted">
          <p className="font-medium text-ink">No matches</p>
          <p className="mt-1">
            {showArchived
              ? "Try another title or id."
              : "Try another title, or show archived."}
          </p>
        </div>
      ) : (
        <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-2">
          {filtered.map((session) => (
            <SessionRow
              key={session.session_id}
              session={session}
              active={session.session_id === sessionId}
              onOpen={() => {
                openSession(session.session_id)
                  .then(() => onClose?.())
                  .catch((err) => alert(err.message || err));
              }}
            />
          ))}
        </ul>
      )}

      <footer className="border-t border-line px-3 py-2">
        <label className="flex cursor-pointer items-center gap-2 text-xs text-muted">
          <input
            type="checkbox"
            className="accent-accent"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
          />
          <span>Show archived</span>
        </label>
      </footer>
    </aside>
  );
}
