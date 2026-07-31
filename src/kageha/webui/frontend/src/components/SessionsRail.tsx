import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useEffect, useMemo, useRef, useState } from "react";
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
  const renameSession = useAppStore((s) => s.renameSession);
  const openSession = useAppStore((s) => s.openSession);
  const showToast = useAppStore((s) => s.showToast);
  const [renaming, setRenaming] = useState(false);
  const [renameDraft, setRenameDraft] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const renameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (renaming) {
      renameRef.current?.focus();
      renameRef.current?.select();
    }
  }, [renaming]);

  // Auto-dismiss delete confirmation after 4s
  useEffect(() => {
    if (!confirmDelete) return;
    const timer = setTimeout(() => setConfirmDelete(false), 4000);
    return () => clearTimeout(timer);
  }, [confirmDelete]);

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

  const commitRename = async () => {
    setRenaming(false);
    const next = renameDraft.trim();
    if (!next || next === title) return;
    // Need to open the session first to rename it (renameSession works on active session)
    try {
      if (!active) await openSession(session.session_id);
      await renameSession(next);
      showToast(`Renamed to "${next}"`);
    } catch (err) {
      showToast(`Rename failed: ${err instanceof Error ? err.message : err}`);
    }
  };

  const onDelete = () => {
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setConfirmDelete(false);
    void run(
      () => deleteSession(session.session_id),
      `Deleted · ${shortId(session.session_id)}`,
    );
  };

  if (renaming) {
    return (
      <li className="px-1">
        <input
          ref={renameRef}
          type="text"
          className="w-full rounded-md border border-accent/40 bg-surface px-2.5 py-1.5 text-[0.85rem] outline-none focus:ring-2 focus:ring-accent/20"
          value={renameDraft}
          onChange={(e) => setRenameDraft(e.target.value)}
          onBlur={() => void commitRename()}
          onKeyDown={(e) => {
            if (e.key === "Enter") void commitRename();
            if (e.key === "Escape") setRenaming(false);
          }}
          placeholder="Session name…"
        />
      </li>
    );
  }

  return (
    <li className="group relative">
      <button
        type="button"
        className={cn(
          "flex w-full flex-col gap-0.5 rounded-md px-2.5 py-2 text-left",
          "transition-colors duration-150",
          active
            ? "bg-accent-soft text-ink ring-1 ring-accent/15"
            : "hover:bg-line/60",
          session.archived && "opacity-60",
        )}
        onClick={onOpen}
      >
        <span className="truncate text-[0.85rem] font-medium leading-tight">
          {session.pinned ? (
            <span className="mr-1 text-accent" aria-hidden="true">
              ✦
            </span>
          ) : null}
          {title}
        </span>
        <span className="font-mono text-[0.65rem] text-faint">
          {shortId(session.session_id)}
          {session.archived ? " · archived" : ""}
        </span>
      </button>

      {/* Delete confirmation inline */}
      {confirmDelete && (
        <div className="absolute inset-0 z-10 flex items-center justify-center rounded-md bg-danger-soft/95 backdrop-blur-sm px-2 animate-[fadeInUp_150ms_ease-out]">
          <span className="text-xs text-danger font-medium mr-2">Delete?</span>
          <button
            type="button"
            className="rounded px-2 py-0.5 text-xs font-medium bg-danger text-white hover:opacity-90"
            onClick={onDelete}
          >
            Yes
          </button>
          <button
            type="button"
            className="rounded px-2 py-0.5 text-xs text-muted ml-1 hover:text-ink"
            onClick={() => setConfirmDelete(false)}
          >
            No
          </button>
        </div>
      )}

      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button
            type="button"
            className={cn(
              "absolute right-1 top-1.5 h-7 w-7 items-center justify-center rounded-md text-muted",
              "hover:bg-surface transition-colors duration-150",
              // Always visible on touch, hover-visible on desktop
              "inline-flex opacity-0 group-hover:opacity-100 data-[state=open]:opacity-100",
              "touch:opacity-100",
            )}
            aria-label="Session actions"
            onClick={(e) => e.stopPropagation()}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
              <circle cx="8" cy="3" r="1.5" />
              <circle cx="8" cy="8" r="1.5" />
              <circle cx="8" cy="13" r="1.5" />
            </svg>
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content
            className="z-50 min-w-40 rounded-lg border border-line bg-surface p-1 shadow-lg animate-[fadeInUp_150ms_ease-out]"
            sideOffset={4}
            align="end"
          >
            <DropdownMenu.Item
              className="cursor-pointer rounded-md px-2.5 py-1.5 text-sm outline-none transition-colors data-[highlighted]:bg-accent-soft"
              onSelect={() => {
                setRenameDraft(title);
                setRenaming(true);
              }}
            >
              Rename
            </DropdownMenu.Item>
            <DropdownMenu.Item
              className="cursor-pointer rounded-md px-2.5 py-1.5 text-sm outline-none transition-colors data-[highlighted]:bg-accent-soft"
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
              className="cursor-pointer rounded-md px-2.5 py-1.5 text-sm outline-none transition-colors data-[highlighted]:bg-accent-soft"
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
            <DropdownMenu.Separator className="my-1 h-px bg-line" />
            <DropdownMenu.Item
              className="cursor-pointer rounded-md px-2.5 py-1.5 text-sm text-danger outline-none transition-colors data-[highlighted]:bg-danger-soft"
              onSelect={onDelete}
            >
              Delete
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
  const showToast = useAppStore((s) => s.showToast);
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
        "fixed inset-y-0 left-0 z-50 flex w-64 flex-col border-r border-line bg-canvas",
        "transition-transform duration-300 ease-in-out",
        "md:static md:z-0 md:translate-x-0",
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
          className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-sm font-medium text-accent hover:bg-accent-soft transition-colors duration-150"
          id="btn-new-session"
          title="New chat (⌘N)"
          onClick={() => {
            newChat()
              .then(() => onClose?.())
              .catch((err) =>
                showToast(`Failed: ${err instanceof Error ? err.message : err}`),
              );
          }}
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" className="shrink-0">
            <path d="M6 2v8M2 6h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
          New
        </button>
      </header>

      <div className="px-3 py-2">
        <label className="sr-only" htmlFor="sessions-search-input">
          Search sessions
        </label>
        <div className="relative">
          <input
            id="sessions-search-input"
            type="search"
            className="w-full rounded-md border border-line bg-surface px-2.5 py-1.5 pr-7 text-sm outline-none placeholder:text-faint focus:border-accent/40 transition-colors duration-150"
            placeholder="Search…"
            autoComplete="off"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button
              type="button"
              className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-faint hover:text-ink transition-colors"
              onClick={() => setQuery("")}
              aria-label="Clear search"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {sessions.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center px-4 text-center">
          <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-xl bg-line/50">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" className="text-muted">
              <rect x="3" y="4" width="14" height="12" rx="2" stroke="currentColor" strokeWidth="1.5" />
              <path d="M7 8h6M7 11h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </div>
          <p className="text-sm font-medium text-ink">No sessions yet</p>
          <p className="mt-1 text-xs text-muted">Start a new chat to begin.</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center px-4 text-center">
          <p className="text-sm font-medium text-ink">No matches</p>
          <p className="mt-1 text-xs text-muted">
            {showArchived
              ? "Try a different search term."
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
                  .catch((err) =>
                    showToast(
                      `Open failed: ${err instanceof Error ? err.message : err}`,
                    ),
                  );
              }}
            />
          ))}
        </ul>
      )}

      <footer className="border-t border-line px-3 py-2">
        <label className="flex cursor-pointer items-center gap-2 text-xs text-muted hover:text-ink transition-colors">
          <input
            type="checkbox"
            className="accent-accent h-3.5 w-3.5 rounded"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
          />
          <span>Show archived</span>
        </label>
      </footer>
    </aside>
  );
}
