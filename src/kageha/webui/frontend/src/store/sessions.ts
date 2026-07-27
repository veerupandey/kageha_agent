import { ApiError, api } from "../api/client";
import type { SessionSummary } from "../api/types";

export type SessionPatch = {
  title?: string;
  archived?: boolean;
  pinned?: boolean;
};

/** Sort pinned sessions to the top; preserve relative order otherwise. */
export function sortSessionsPinnedFirst(
  sessions: SessionSummary[],
): SessionSummary[] {
  return sessions.slice().sort((a, b) => {
    const ap = a.pinned ? 1 : 0;
    const bp = b.pinned ? 1 : 0;
    if (ap !== bp) return bp - ap;
    return 0;
  });
}

/** Filter archived unless showArchived is true. */
export function filterSessionsForRail(
  sessions: SessionSummary[],
  opts: { showArchived: boolean; query?: string },
): SessionSummary[] {
  const q = String(opts.query || "")
    .trim()
    .toLowerCase();
  let list = sessions;
  if (!opts.showArchived) {
    list = list.filter((s) => !s.archived);
  }
  list = sortSessionsPinnedFirst(list);
  if (!q) return list;
  return list.filter((s) => {
    const title = String(s.title || "").toLowerCase();
    const id = String(s.session_id || "").toLowerCase();
    return title.includes(q) || id.includes(q);
  });
}

function unavailableMessage(status: number, action: string): string {
  if (status === 404 || status === 405) {
    return `${action} isn’t available on this server yet`;
  }
  return `${action} failed (${status})`;
}

/** PATCH session fields (title / archived / pinned). */
export async function patchSession(
  sessionId: string,
  patch: SessionPatch,
): Promise<SessionSummary> {
  try {
    return await api<SessionSummary>(
      `/api/sessions/${encodeURIComponent(sessionId)}`,
      { method: "PATCH", body: JSON.stringify(patch) },
    );
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 405)) {
      throw new ApiError(
        unavailableMessage(
          err.status,
          patch.pinned != null
            ? "Pin"
            : patch.archived != null
              ? "Archive"
              : "Update",
        ),
        err.status,
        err.data,
      );
    }
    throw err;
  }
}

/** DELETE session. */
export async function deleteSessionApi(sessionId: string): Promise<void> {
  try {
    await api(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 405)) {
      throw new ApiError(
        unavailableMessage(err.status, "Delete"),
        err.status,
        err.data,
      );
    }
    throw err;
  }
}

/** Optimistic local list update after pin/archive. */
export function applySessionFlagsLocally(
  sessions: SessionSummary[],
  sessionId: string,
  flags: { pinned?: boolean; archived?: boolean; title?: string },
): SessionSummary[] {
  return sessions.map((s) => {
    if (s.session_id !== sessionId) return s;
    return {
      ...s,
      ...(flags.pinned != null ? { pinned: flags.pinned } : {}),
      ...(flags.archived != null ? { archived: flags.archived } : {}),
      ...(flags.title != null ? { title: flags.title } : {}),
    };
  });
}
