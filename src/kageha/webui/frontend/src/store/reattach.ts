import { api } from "../api/client";
import type { PendingApproval, RunStatus, SessionRun } from "../api/types";
import { appendActivityStep } from "./activity";
import { normalizeComputerFrame, normalizeToolCard, syncFromRun, uid } from "./helpers";
import type { AppState } from "./types";

type SetState = (
  partial:
    | Partial<AppState>
    | ((state: AppState) => Partial<AppState> | AppState),
) => void;
type GetState = () => AppState;

export type ReattachDeps = {
  set: SetState;
  get: GetState;
  updateRun: (
    sessionId: string,
    updater: (run: SessionRun) => SessionRun,
  ) => void;
};

interface EventFrame {
  sequence?: number;
  kind?: string;
  label?: string;
  detail?: string[];
  interesting?: boolean;
  tool_card?: Record<string, unknown>;
  computer_frame?: Record<string, unknown>;
  payload?: Record<string, unknown>;
}

interface SessionSnapshot {
  messages?: Array<{ role: string; text: string }>;
  pending_approval?: PendingApproval | null;
  active_turn?: { turn_id?: string; status?: string; phase?: string } | null;
  title?: string | null;
}

const POLL_MS = 1200;
const POLL_MS_RETRY = 2400;

/**
 * Reattach to a turn that is still running on the backend after a page
 * reload / session switch. Replays events emitted since the frontend lost
 * connection, then keeps polling until the turn reaches a terminal state.
 */
export function reattachToActiveTurn(
  deps: ReattachDeps,
  sessionId: string,
  threadId: string,
  turnId: string,
  opts: { pendingApproval?: PendingApproval | null } = {},
): void {
  const { set, get, updateRun } = deps;
  const assistantId = uid("a");
  let afterSeq = 0;
  let stopped = false;

  updateRun(sessionId, (run) => ({
    ...run,
    sending: true,
    status: "running",
    statusLabel: "Reconnecting…",
    waitingApproval: Boolean(opts.pendingApproval),
    messages: [
      ...run.messages,
      {
        id: assistantId,
        role: "assistant",
        text: "",
        streaming: true,
        statusLabel: "Reconnecting…",
        steps: [],
        toolCards: [],
        computerFrames: [],
      },
    ],
  }));
  if (sessionId === get().sessionId) {
    set({ runStatus: "running", statusLabel: "Reconnecting…", sending: true });
  }
  if (opts.pendingApproval && sessionId === get().sessionId) {
    set({ pendingApproval: { ...opts.pendingApproval, sessionId } });
  }

  const applyFrame = (frame: EventFrame) => {
    if (typeof frame.sequence === "number" && frame.sequence > afterSeq) {
      afterSeq = frame.sequence;
    }
    const kind = String(frame.kind || "");
    const label = String(frame.label || kind || "");
    const detail = Array.isArray(frame.detail) ? frame.detail : [];
    const interesting = frame.interesting !== false;

    set((s) => {
      const run = s.runs[sessionId];
      if (!run) return s;
      const messages = run.messages.map((m) => {
        if (m.id !== assistantId) return m;
        let next = m;
        if (label) {
          next = {
            ...next,
            statusLabel: label,
            statusDetail: detail[0] || next.statusDetail,
            steps: interesting
              ? appendActivityStep(next.steps || [], {
                  label,
                  detail,
                  kind,
                  interesting,
                })
              : next.steps || [],
          };
        }
        if (frame.tool_card) {
          const card = normalizeToolCard(frame.tool_card, kind || "tool_card");
          if (card) {
            const cards = [...(next.toolCards || [])];
            const idx = cards.findIndex((c) => c.id === card.id);
            if (idx >= 0) cards[idx] = { ...cards[idx], ...card };
            else cards.push(card);
            next = { ...next, toolCards: cards.slice(-24) };
            if (card.artifactRefs?.length) {
              get().upsertCanvasPaths(card.artifactRefs);
            }
          }
        }
        if (frame.computer_frame) {
          const cf = normalizeComputerFrame(frame.computer_frame, sessionId);
          if (cf) {
            next = {
              ...next,
              computerFrames: [...(next.computerFrames || []), cf].slice(-12),
            };
          }
        }
        return next;
      });
      const nextRun = { ...run, messages, statusLabel: label || run.statusLabel };
      const sync = s.sessionId === sessionId ? syncFromRun(nextRun) : {};
      return { ...sync, runs: { ...s.runs, [sessionId]: nextRun } };
    });
  };

  const finalize = async () => {
    stopped = true;
    let finalText = "";
    let autoTitle: string | null = null;
    let rawStatus = "";
    try {
      const data = await api<SessionSnapshot & { status?: string }>(
        `/api/sessions/${encodeURIComponent(sessionId)}`,
      );
      const rows = data.messages || [];
      const lastAssistant = [...rows].reverse().find((m) => m.role === "assistant");
      finalText = String(lastAssistant?.text || "");
      autoTitle = data.title ? String(data.title) : null;
      rawStatus = String(data.status || "").toLowerCase();
    } catch {
      /* best-effort — keep whatever streamed text we accumulated */
    }
    const status: RunStatus =
      rawStatus === "cancelled"
        ? "cancelled"
        : rawStatus === "error" || rawStatus === "failed"
          ? "error"
          : "success";
    const label =
      status === "cancelled" ? "Cancelled" : status === "error" ? "Error" : "Done";
    updateRun(sessionId, (r) => {
      const messages = r.messages.map((m) =>
        m.id === assistantId
          ? { ...m, text: finalText || m.text, streaming: false, statusLabel: label }
          : m,
      );
      return {
        ...r,
        messages,
        sending: false,
        abort: null,
        waitingApproval: false,
        status,
        statusLabel: label,
      };
    });
    if (sessionId === get().sessionId) {
      set({
        ...(autoTitle ? { sessionTitle: autoTitle } : {}),
        runStatus: status,
        statusLabel: label,
        sending: false,
        pendingApproval: null,
      });
    }
    void get().refreshArtifacts();
  };

  const tick = async () => {
    if (stopped) return;
    try {
      const events = await api<{
        events?: EventFrame[];
        pending_approval?: PendingApproval | null;
      }>(
        `/api/sessions/${encodeURIComponent(sessionId)}/events` +
          `?thread_id=${encodeURIComponent(threadId)}` +
          `&turn_id=${encodeURIComponent(turnId)}` +
          `&after_sequence=${afterSeq}`,
      );
      for (const frame of events.events || []) applyFrame(frame);

      if (events.pending_approval && events.pending_approval.approval_id) {
        const pending: PendingApproval = {
          ...events.pending_approval,
          sessionId,
        };
        updateRun(sessionId, (r) => ({
          ...r,
          waitingApproval: true,
          status: "waiting_approval",
          statusLabel: "Approval needed",
        }));
        if (sessionId === get().sessionId) {
          set({
            pendingApproval: pending,
            runStatus: "waiting_approval",
            statusLabel: "Approval needed",
          });
        }
      }

      const session = await api<SessionSnapshot>(
        `/api/sessions/${encodeURIComponent(sessionId)}`,
      );
      const stillActive =
        session.active_turn &&
        String(session.active_turn.turn_id || "") === turnId;
      if (stillActive) {
        window.setTimeout(() => void tick(), POLL_MS);
      } else {
        await finalize();
      }
    } catch {
      if (stopped) return;
      window.setTimeout(() => void tick(), POLL_MS_RETRY);
    }
  };

  void tick();
}
