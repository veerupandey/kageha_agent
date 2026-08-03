import { streamChat } from "../api/stream";
import type {
  AgentMode,
  ChatMessage,
  PendingApproval,
  RunStatus,
  SessionRun,
} from "../api/types";
import { speakText } from "../lib/voiceClient";
import { appendActivityStep, asDetailLines } from "./activity";
import {
  normalizeComputerFrame,
  normalizeToolCard,
  syncFromRun,
  uid,
} from "./helpers";
import type { AppState } from "./types";

type SetState = (
  partial:
    | Partial<AppState>
    | ((state: AppState) => Partial<AppState> | AppState),
) => void;
type GetState = () => AppState;

export type RunTurnDeps = {
  set: SetState;
  get: GetState;
  updateRun: (
    sessionId: string,
    updater: (run: SessionRun) => SessionRun,
  ) => void;
  patchAssistant: (
    sessionId: string,
    assistantId: string,
    patch: Partial<ChatMessage>,
  ) => void;
  flushQueue: (sessionId: string) => Promise<void>;
};

export async function runTurn(
  deps: RunTurnDeps,
  sessionId: string,
  threadId: string,
  text: string,
  attachments: string[],
  displayText: string,
  opts: { autoBuild?: boolean; agentMode?: AgentMode } = {},
): Promise<void> {
  const { set, get, updateRun, patchAssistant, flushQueue } = deps;
  const assistantId = uid("a");
  const abort = new AbortController();
  const userMsg: ChatMessage = {
    id: uid("u"),
    role: "user",
    text: displayText || text || "(attachments)",
  };

  updateRun(sessionId, (run) => ({
    ...run,
    sending: true,
    abort,
    status: "running",
    statusLabel: "Working…",
    waitingApproval: false,
    needsAttention: false,
    messages: [
      ...run.messages,
      userMsg,
      {
        id: assistantId,
        role: "assistant",
        text: "",
        streaming: true,
        statusLabel: "Working…",
        steps: [],
        toolCards: [],
        computerFrames: [],
      },
    ],
  }));
  set({ error: null, draft: "", canvasTurnPaths: new Set() });

  const mode = opts.agentMode || get().agentMode;
  const model = get().modelOverride.trim() || undefined;

  try {
    const done = await streamChat(
      {
        thread_id: threadId,
        session_id: sessionId,
        message: text,
        attachments: attachments.length ? attachments : undefined,
        auto_approve: get().autoApprove,
        auto_build: opts.autoBuild,
        agent_mode: mode,
        model,
        loop_mode: mode !== "normal" ? "full" : undefined,
        max_steps: mode !== "normal" ? 40 : undefined,
      },
      {
        onStatus: (label, data) => {
          const detail = asDetailLines(data, 2);
          const statusDetail = detail[0] || undefined;
          updateRun(sessionId, (r) => ({
            ...r,
            status: "running",
            statusLabel: label,
          }));
          // Status drives the live pulse only; Activity rows come from events.
          set((s) => {
            const run = s.runs[sessionId];
            if (!run) return s;
            const messages = run.messages.map((m) => {
              if (m.id !== assistantId) return m;
              return {
                ...m,
                statusLabel: label,
                statusDetail,
              };
            });
            const next = {
              ...run,
              messages,
              statusLabel: label,
              status: "running" as RunStatus,
            };
            const sync = s.sessionId === sessionId ? syncFromRun(next) : {};
            return {
              ...sync,
              runs: { ...s.runs, [sessionId]: next },
            };
          });
        },
        onDelta: (assembled) =>
          patchAssistant(sessionId, assistantId, { text: assembled }),
        onMessage: (full) =>
          patchAssistant(sessionId, assistantId, { text: full }),
        onToolCard: (data) => {
          const card = normalizeToolCard(data, "tool_card");
          if (!card) return;
          if (card.artifactRefs?.length) {
            // Canvas only tracks user deliverables (filters noise internally).
            get().upsertCanvasPaths(card.artifactRefs);
          }
          set((s) => {
            const run = s.runs[sessionId];
            if (!run) return s;
            const messages = run.messages.map((m) => {
              if (m.id !== assistantId) return m;
              const cards = [...(m.toolCards || [])];
              const idx = cards.findIndex((c) => c.id === card.id);
              if (idx >= 0) cards[idx] = { ...cards[idx], ...card };
              else cards.push(card);
              return { ...m, toolCards: cards.slice(-24) };
            });
            const next = { ...run, messages };
            const sync = s.sessionId === sessionId ? syncFromRun(next) : {};
            return {
              ...sync,
              runs: { ...s.runs, [sessionId]: next },
            };
          });
        },
        onComputerFrame: (data) => {
          const frame = normalizeComputerFrame(data, sessionId);
          if (!frame) return;
          // Computer captures stay in the live frames strip — not Canvas.
          set((s) => {
            const run = s.runs[sessionId];
            if (!run) return s;
            const messages = run.messages.map((m) => {
              if (m.id !== assistantId) return m;
              const frames = [...(m.computerFrames || []), frame].slice(-12);
              return { ...m, computerFrames: frames };
            });
            const next = { ...run, messages };
            const sync = s.sessionId === sessionId ? syncFromRun(next) : {};
            return {
              ...sync,
              runs: { ...s.runs, [sessionId]: next },
            };
          });
        },
        onEvent: (data) => {
          const kind = String(data.kind || "");
          const payload =
            data.payload && typeof data.payload === "object"
              ? (data.payload as Record<string, unknown>)
              : {};
          const label = String(data.label || kind || "");
          const detail = asDetailLines(data);
          const interesting =
            data.interesting === undefined ? true : Boolean(data.interesting);
          if (label) {
            set((s) => {
              const run = s.runs[sessionId];
              if (!run) return s;
              const messages = run.messages.map((m) => {
                if (m.id !== assistantId) return m;
                const steps = interesting
                  ? appendActivityStep(m.steps || [], {
                      label,
                      detail,
                      kind,
                      interesting,
                    })
                  : m.steps || [];
                return {
                  ...m,
                  steps,
                  statusLabel: label,
                  statusDetail: detail[0] || m.statusDetail,
                };
              });
              const next = {
                ...run,
                messages,
                statusLabel: label,
                status: "running" as RunStatus,
              };
              const sync = s.sessionId === sessionId ? syncFromRun(next) : {};
              return {
                ...sync,
                runs: { ...s.runs, [sessionId]: next },
              };
            });
          }

          if (kind === "goal_qa_misfit") {
            const msg = String(
              payload.message || data.label || "This looks like Normal",
            );
            get().showToast(`${msg} — answering without Goal theater`);
            get().setAgentMode("normal");
          }
          // Live todo/milestone board updates.
          if (kind === "todo_board") {
            const items = Array.isArray(payload.items)
              ? (payload.items as { id?: string; text?: string; done?: boolean }[]).map(
                  (it, i) => ({
                    id: String(it.id || `t${i}`),
                    text: String(it.text || ""),
                    done: Boolean(it.done),
                  }),
                )
              : [];
            const board = {
              done: typeof payload.done === "number" ? payload.done : items.filter((i) => i.done).length,
              total: typeof payload.total === "number" ? payload.total : items.length,
              items,
            };
            set({ todoBoard: board });
          }
          if (kind === "approval_required") {
            const approvalId = String(
              payload.approval_id || data.approval_id || "",
            );
            if (approvalId) {
              const pending: PendingApproval = {
                approval_id: approvalId,
                sessionId,
                action: String(payload.action || data.label || ""),
                risk_class: payload.risk_class
                  ? String(payload.risk_class)
                  : undefined,
                detail: payload.detail as string | string[] | undefined,
              };
              const isPlanBuild =
                pending.risk_class === "plan" ||
                pending.action === "approve_plan";
              if (isPlanBuild && get().agentMode === "normal") {
                get().setAgentMode("plan");
              }
              updateRun(sessionId, (r) => ({
                ...r,
                waitingApproval: true,
                status: "waiting_approval",
                statusLabel: isPlanBuild ? "Awaiting Build" : "Approval needed",
              }));
              patchAssistant(sessionId, assistantId, { approval: pending });
              if (sessionId === get().sessionId) {
                set({
                  pendingApproval: pending,
                  runStatus: "waiting_approval",
                  statusLabel: isPlanBuild
                    ? "Awaiting Build"
                    : "Approval needed",
                });
              }
            }
          } else if (kind === "approval_resolved") {
            updateRun(sessionId, (r) => ({
              ...r,
              waitingApproval: false,
            }));
            if (
              get().pendingApproval?.sessionId === sessionId ||
              get().pendingApproval?.session_id === sessionId
            ) {
              set({ pendingApproval: null });
            }
          }

          if (kind === "tool_card" || data.tool_card) {
            const card = normalizeToolCard(
              { ...payload, ...data },
              kind || "tool_card",
            );
            if (card) {
              set((s) => {
                const run = s.runs[sessionId];
                if (!run) return s;
                const messages = run.messages.map((m) => {
                  if (m.id !== assistantId) return m;
                  const cards = [...(m.toolCards || [])];
                  const idx = cards.findIndex((c) => c.id === card.id);
                  if (idx >= 0) cards[idx] = { ...cards[idx], ...card };
                  else cards.push(card);
                  return { ...m, toolCards: cards.slice(-24) };
                });
                const next = { ...run, messages };
                const sync = s.sessionId === sessionId ? syncFromRun(next) : {};
                return {
                  ...sync,
                  runs: { ...s.runs, [sessionId]: next },
                };
              });
            }
          }
          if (
            kind === "computer_frame" ||
            kind === "artifact_thumb" ||
            data.computer_frame
          ) {
            const frame = normalizeComputerFrame(
              { ...payload, ...data },
              sessionId,
            );
            if (frame) {
              set((s) => {
                const run = s.runs[sessionId];
                if (!run) return s;
                const messages = run.messages.map((m) => {
                  if (m.id !== assistantId) return m;
                  const frames = [...(m.computerFrames || []), frame].slice(
                    -12,
                  );
                  return { ...m, computerFrames: frames };
                });
                const next = { ...run, messages };
                const sync = s.sessionId === sessionId ? syncFromRun(next) : {};
                return {
                  ...sync,
                  runs: { ...s.runs, [sessionId]: next },
                };
              });
            }
          }
        },
      },
      { signal: abort.signal },
    );

    const runAfter = get().runs[sessionId];
    const finalText = String(
      done.message ||
        runAfter?.messages.find((m) => m.id === assistantId)?.text ||
        "",
    );
    const status = String(done.status || "success");
    const awaitingBuild = status === "awaiting_plan_approval";
    const awaitingClarify = status === "awaiting_clarify";
    if (awaitingBuild && get().agentMode === "normal") {
      get().setAgentMode("plan");
    }
    patchAssistant(sessionId, assistantId, {
      text: finalText,
      streaming: false,
      // Keep last activity label visible briefly; steps remain in Trace.
      statusLabel:
        status === "cancelled"
          ? "Cancelled"
          : status === "error"
            ? "Error"
            : awaitingBuild
              ? "Awaiting Build"
              : awaitingClarify
                ? "Awaiting clarification"
                : "Done",
      turnSteps: typeof done.steps === "number" ? done.steps : undefined,
      turnCostUsd: typeof done.spent_usd === "number" ? done.spent_usd : undefined,
    });
    updateRun(sessionId, (r) => ({
      ...r,
      sending: false,
      abort: null,
      waitingApproval: awaitingBuild,
      status: status === "cancelled"
        ? "cancelled"
        : awaitingBuild || awaitingClarify
          ? "waiting_approval"
          : "success",
      statusLabel:
        status === "cancelled"
          ? "Cancelled"
          : awaitingBuild
            ? "Awaiting Build"
            : awaitingClarify
              ? "Awaiting clarification"
              : "Done",
      needsAttention: sessionId !== get().sessionId,
    }));
    // Ensure chrome status clears off Working… after success.
    if (sessionId === get().sessionId) {
      const autoTitle =
        typeof done.title === "string" ? done.title.trim() : "";
      set({
        ...(autoTitle ? { sessionTitle: autoTitle } : {}),
        runStatus: awaitingBuild ? "waiting_approval" : status === "cancelled" ? "cancelled" : "success",
        statusLabel:
          status === "cancelled"
            ? "Cancelled"
            : awaitingBuild
              ? "Awaiting Build"
              : "Done",
        sending: false,
      });
    }
    if (sessionId !== get().sessionId) {
      get().showToast(`Task finished · ${sessionId.slice(0, 8)}`);
    }
    if (
      get().prefs.voiceReply &&
      sessionId === get().sessionId &&
      status !== "cancelled" &&
      status !== "error" &&
      !awaitingBuild &&
      !awaitingClarify &&
      finalText.trim()
    ) {
      void speakText(sessionId, finalText).catch((err) => {
        get().showToast(
          `Speak: ${err instanceof Error ? err.message : String(err)}`,
        );
      });
    }
    await get().refreshSessions();
    if (sessionId === get().sessionId) {
      await flushQueue(sessionId);
    }
  } catch (err) {
    const aborted =
      abort.signal.aborted ||
      (err instanceof Error && err.name === "AbortError");
    if (aborted) {
      patchAssistant(sessionId, assistantId, {
        streaming: false,
        statusLabel: "Cancelled",
      });
      updateRun(sessionId, (r) => ({
        ...r,
        sending: false,
        abort: null,
        status: "cancelled",
        statusLabel: "Cancelled",
      }));
      return;
    }
    const message = err instanceof Error ? err.message : String(err);
    patchAssistant(sessionId, assistantId, {
      text: message,
      streaming: false,
      statusLabel: "Error",
    });
    updateRun(sessionId, (r) => ({
      ...r,
      sending: false,
      abort: null,
      status: "error",
      statusLabel: "Error",
      needsAttention: sessionId !== get().sessionId,
    }));
    if (sessionId === get().sessionId) {
      set({ error: message });
    }
  }
}
