import { useEffect, useMemo, useRef, useState } from "react";
import { useAppStore } from "../store";
import type { SessionSummary } from "../api/types";
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
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPos, setMenuPos] = useState({ x: 0, y: 0 });
  const rowRef = useRef<HTMLLIElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const title =
    (session.title && String(session.title).trim()) ||
    shortId(session.session_id);

  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (
        !menuRef.current?.contains(e.target as Node) &&
        !rowRef.current?.contains(e.target as Node)
      ) {
        setMenuOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const run = async (action: () => Promise<void>, okToast?: string) => {
    setMenuOpen(false);
    try {
      await action();
      if (okToast) showToast(okToast);
    } catch {
      /* toast already shown by store */
    }
  };

  const onDelete = () => {
    setMenuOpen(false);
    const label = title;
    if (
      !window.confirm(
        `Delete session “${label}”? This cannot be undone.`,
      )
    ) {
      return;
    }
    void run(
      () => deleteSession(session.session_id),
      `Deleted · ${shortId(session.session_id)}`,
    );
  };

  return (
    <li
      ref={rowRef}
      className={`session-row${session.pinned ? " is-pinned" : ""}${session.archived ? " is-archived" : ""}`}
    >
      <button
        type="button"
        className={`session-item${active ? " active" : ""}`}
        onClick={onOpen}
        onContextMenu={(e) => {
          e.preventDefault();
          setMenuPos({ x: e.clientX, y: e.clientY });
          setMenuOpen(true);
        }}
      >
        <span className="session-item-title">
          {session.pinned ? (
            <span className="session-pin-mark" aria-hidden="true">
              ✦
            </span>
          ) : null}
          {title}
          {session.archived ? (
            <span className="session-archived-badge">archived</span>
          ) : null}
        </span>
        <span className="mono session-item-id">{shortId(session.session_id)}</span>
      </button>
      <div className="session-row-actions" role="group" aria-label="Session actions">
        <button
          type="button"
          className="btn ghost compact session-action"
          title={session.pinned ? "Unpin" : "Pin"}
          aria-label={session.pinned ? "Unpin session" : "Pin session"}
          onClick={(e) => {
            e.stopPropagation();
            void run(
              () => pinSession(session.session_id, !session.pinned),
              session.pinned ? "Unpinned" : "Pinned",
            );
          }}
        >
          {session.pinned ? "Unpin" : "Pin"}
        </button>
        <button
          type="button"
          className="btn ghost compact session-action"
          title={session.archived ? "Unarchive" : "Archive"}
          aria-label={session.archived ? "Unarchive session" : "Archive session"}
          onClick={(e) => {
            e.stopPropagation();
            void run(
              () => archiveSession(session.session_id, !session.archived),
              session.archived ? "Unarchived" : "Archived",
            );
          }}
        >
          {session.archived ? "Restore" : "Archive"}
        </button>
        <button
          type="button"
          className="btn ghost compact session-action session-action-danger"
          title="Delete"
          aria-label="Delete session"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
        >
          Delete
        </button>
      </div>
      {menuOpen ? (
        <div
          ref={menuRef}
          className="session-context-menu"
          role="menu"
          style={{ left: menuPos.x, top: menuPos.y }}
        >
          <button
            type="button"
            role="menuitem"
            className="session-context-item"
            onClick={() =>
              void run(
                () => pinSession(session.session_id, !session.pinned),
                session.pinned ? "Unpinned" : "Pinned",
              )
            }
          >
            {session.pinned ? "Unpin" : "Pin"}
          </button>
          <button
            type="button"
            role="menuitem"
            className="session-context-item"
            onClick={() =>
              void run(
                () => archiveSession(session.session_id, !session.archived),
                session.archived ? "Unarchived" : "Archived",
              )
            }
          >
            {session.archived ? "Unarchive" : "Archive"}
          </button>
          <button
            type="button"
            role="menuitem"
            className="session-context-item session-action-danger"
            onClick={onDelete}
          >
            Delete…
          </button>
        </div>
      ) : null}
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
      className={`sessions${open ? " open" : ""}`}
      id="sessions-panel"
    >
      <header className="sessions-head">
        <p className="eyebrow">Sessions</p>
        <div className="sessions-head-actions">
          <button
            type="button"
            className="btn ghost"
            id="btn-new-session"
            title="New chat (in place)"
            onClick={() => {
              newChat()
                .then(() => onClose?.())
                .catch((err) => alert(err.message || err));
            }}
          >
            New
          </button>
        </div>
      </header>

      <div className="sessions-search">
        <label className="sr-only" htmlFor="sessions-search-input">
          Search sessions
        </label>
        <input
          id="sessions-search-input"
          type="search"
          className="sessions-search-input"
          placeholder="Search title or id…"
          autoComplete="off"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {sessions.length === 0 ? (
        <div className="sessions-empty" id="sessions-empty">
          <p className="sessions-empty-title">No sessions yet</p>
          <p className="sessions-empty-hint">Start a new chat to begin.</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="sessions-empty" id="sessions-empty">
          <p className="sessions-empty-title">No matches</p>
          <p className="sessions-empty-hint">
            {showArchived
              ? "Try another title or id."
              : "Try another title, or show archived."}
          </p>
        </div>
      ) : (
        <ul className="session-list" id="session-list">
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

      <footer className="sessions-foot">
        <label className="sessions-archived-toggle">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
          />
          <span>Show archived</span>
        </label>
      </footer>
    </aside>
  );
}
