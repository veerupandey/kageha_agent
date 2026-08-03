import { create } from "zustand";
import { api } from "../api/client";
import {
  filterSlashByCapabilities,
  mergeServerCatalog,
  SLASH_COMMANDS,
} from "../api/slashCatalog";
import type {
  AgentMode,
  ArtifactEntry,
  ChatMessage,
  MetaPayload,
  PendingApproval,
  QueuedMessage,
  SessionRun,
  SessionSummary,
  ToastMessage,
} from "../api/types";
import {
  isShowcaseArtifact,
  showcaseSortKey,
  toCanvasItem,
} from "../lib/artifactMedia";
import type { CanvasItem } from "../lib/artifactMedia";
import {
  apiSoft,
  emptyRun,
  isComputerAdminSlash,
  isMetaOnlySlash,
  isModeOnlyComposerText,
  loadLastSession,
  mapHistoryMessages,
  parseModeSlash,
  rememberSession,
  syncFromRun,
  uid,
} from "./helpers";
import {
  applyPrefsToDocument,
  loadPrefs,
  mergePrefs,
  savePrefs,
} from "./prefs";
import { reattachToActiveTurn } from "./reattach";
import { runTurn as runTurnStream } from "./runTurn";
import {
  applySessionFlagsLocally,
  deleteSessionApi,
  patchSession,
  sortSessionsPinnedFirst,
} from "./sessions";
import type { AppState } from "./types";

export const useAppStore = create<AppState>((set, get) => {
  const initialPrefs = loadPrefs();
  applyPrefsToDocument(initialPrefs);

  const ensureRun = (sessionId: string): SessionRun => {
    const existing = get().runs[sessionId];
    if (existing) return existing;
    const run = emptyRun(sessionId);
    set((s) => ({ runs: { ...s.runs, [sessionId]: run } }));
    return run;
  };

  const updateRun = (
    sessionId: string,
    updater: (run: SessionRun) => SessionRun,
  ) => {
    set((s) => {
      const prev = s.runs[sessionId] || emptyRun(sessionId, s.threadId);
      const next = updater(prev);
      if (next === prev) return s;
      const runs =
        s.runs[sessionId] === next
          ? s.runs
          : { ...s.runs, [sessionId]: next };
      if (sessionId === s.sessionId) {
        return {
          runs,
          ...syncFromRun(next),
        };
      }
      return { runs };
    });
  };

  /** Coalesce stream text patches to one Zustand update per animation frame. */
  const pendingTextPatches = new Map<
    string,
    { sessionId: string; assistantId: string; text: string }
  >();
  let textPatchRaf = 0;

  const flushTextPatches = () => {
    textPatchRaf = 0;
    if (!pendingTextPatches.size) return;
    const batch = [...pendingTextPatches.values()];
    pendingTextPatches.clear();
    set((s) => {
      let runs = s.runs;
      let syncRun: SessionRun | undefined;
      let any = false;
      const bySession = new Map<string, Map<string, string>>();
      for (const p of batch) {
        let m = bySession.get(p.sessionId);
        if (!m) {
          m = new Map();
          bySession.set(p.sessionId, m);
        }
        m.set(p.assistantId, p.text);
      }
      for (const [sessionId, idToText] of bySession) {
        const prev = runs[sessionId];
        if (!prev) continue;
        let changed = false;
        const messages = prev.messages.map((m) => {
          const text = idToText.get(m.id);
          if (text == null || m.text === text) return m;
          changed = true;
          return { ...m, text };
        });
        if (!changed) continue;
        any = true;
        const next = { ...prev, messages };
        runs = { ...runs, [sessionId]: next };
        if (sessionId === s.sessionId) syncRun = next;
      }
      if (!any) return s;
      if (syncRun) return { runs, ...syncFromRun(syncRun) };
      return { runs };
    });
  };

  const patchAssistantText = (
    sessionId: string,
    assistantId: string,
    text: string,
  ) => {
    pendingTextPatches.set(`${sessionId}:${assistantId}`, {
      sessionId,
      assistantId,
      text,
    });
    if (!textPatchRaf) {
      textPatchRaf = requestAnimationFrame(flushTextPatches);
    }
  };

  const patchAssistant = (
    sessionId: string,
    assistantId: string,
    patch: Partial<ChatMessage>,
  ) => {
    const keys = Object.keys(patch);
    if (keys.length === 1 && keys[0] === "text" && typeof patch.text === "string") {
      patchAssistantText(sessionId, assistantId, patch.text);
      return;
    }
    if (textPatchRaf) {
      cancelAnimationFrame(textPatchRaf);
      flushTextPatches();
    }
    updateRun(sessionId, (run) => {
      let changed = false;
      const messages = run.messages.map((m) => {
        if (m.id !== assistantId) return m;
        changed = true;
        return { ...m, ...patch };
      });
      if (!changed) return run;
      return { ...run, messages };
    });
  };

  const touchTab = (sessionId: string) => {
    if (!sessionId) return;
    set((s) =>
      s.tabs.includes(sessionId) ? s : { tabs: [...s.tabs, sessionId] },
    );
  };

  const parkActive = () => {
    const { sessionId, pendingFiles, threadId } = get();
    if (!sessionId) return;
    updateRun(sessionId, (run) => ({
      ...run,
      pendingFiles: pendingFiles.slice(),
      threadId: threadId || run.threadId,
    }));
    // Clear global pending — restored when switching back.
    set({ pendingFiles: [] });
  };

  const uploadPendingFiles = async (sessionId: string, files: File[]) => {
    const paths: string[] = [];
    for (const file of files) {
      const fd = new FormData();
      fd.append("file", file, file.name);
      const data = await api<{ path?: string }>(
        `/api/sessions/${encodeURIComponent(sessionId)}/upload`,
        { method: "POST", body: fd },
      );
      if (data.path) paths.push(data.path);
    }
    return paths;
  };

  const flushQueue = async (sessionId: string) => {
    const run = get().runs[sessionId];
    if (!run || run.sending || sessionId !== get().sessionId) return;
    const next = run.queue[0];
    if (!next) return;
    updateRun(sessionId, (r) => ({
      ...r,
      queue: r.queue.slice(1),
      pendingFiles: next.files || [],
    }));
    set({ draft: next.text || "", pendingFiles: next.files || [] });
    await get().sendMessage(next.text || "");
  };

  const runTurn = async (
    sessionId: string,
    threadId: string,
    text: string,
    attachments: string[],
    displayText: string,
    opts: { autoBuild?: boolean; agentMode?: AgentMode } = {},
  ) =>
    runTurnStream(
      { set, get, updateRun, patchAssistant, flushQueue },
      sessionId,
      threadId,
      text,
      attachments,
      displayText,
      opts,
    );

  const reattach = (
    sessionId: string,
    threadId: string,
    turnId: string,
    opts: { pendingApproval?: PendingApproval | null } = {},
  ) =>
    reattachToActiveTurn({ set, get, updateRun }, sessionId, threadId, turnId, opts);

  return {
    sessions: [],
    sessionId: null,
    threadId: null,
    sessionTitle: null,
    messages: [],
    runStatus: "idle",
    statusLabel: "Ready",
    agentMode: initialPrefs.defaultAgentMode,
    autoApprove: !initialPrefs.defaultAskMode,
    permissionScope: initialPrefs.defaultAskMode ? "ask" : "session",
    sending: false,
    draft: "",
    error: null,
    abort: null,
    modelOverride: "",
    pendingFiles: [],
    pendingApproval: null,
    composerChip: { kind: null, value: null },
    tabs: [],
    runs: {},
    prefs: initialPrefs,
    canvasOpen: false,
    canvasExpanded: false,
    canvasItems: [],
    canvasSelectedPath: null,
    todoBoard: null,
    slashCatalog: SLASH_COMMANDS.slice(),
    capabilities: {
      projectFiles: null,
      cometApi: null,
      browserApi: null,
      computerApi: null,
      modelApi: null,
      slashCatalogApi: false,
    },
    meta: null,
    toasts: [],
    models: [],
    bootError: null,
    connectionOnline:
      typeof navigator === "undefined" ? true : navigator.onLine !== false,
    sessionLoading: false,
    sessionsError: null,

    setDraft: (value) => set({ draft: value }),

    setConnectionOnline: (online) => set({ connectionOnline: online }),

    clearError: () => set({ error: null }),

    retryBoot: async () => {
      await get().boot();
    },

    retryLastTurn: async () => {
      const messages = get().messages;
      for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i].role === "user" && messages[i].text?.trim()) {
          await get().sendMessage(messages[i].text);
          return;
        }
      }
    },

    setAgentMode: (mode) => {
      set(() => {
        const next: Partial<AppState> = { agentMode: mode };
        if (mode === "normal") {
          next.composerChip = { kind: null, value: null };
        } else {
          next.composerChip = { kind: "mode", value: mode };
        }
        return next;
      });
    },

    setAskMode: (ask) => {
      const prefs = mergePrefs(get().prefs, { defaultAskMode: ask });
      savePrefs(prefs);
      applyPrefsToDocument(prefs);
      set({
        prefs,
        autoApprove: !ask,
        permissionScope: ask ? "ask" : "session",
      });
    },

    setPermissionsMode: async (mode) => {
      const normalized = mode === "auto" ? "session" : mode;
      try {
        await api("/api/permissions", {
          method: "POST",
          body: JSON.stringify({ mode: normalized === "session" ? "auto" : normalized }),
        });
      } catch (err) {
        get().showToast(
          `Permissions failed: ${err instanceof Error ? err.message : err}`,
        );
        return;
      }
      const ask = normalized === "ask";
      const prefs = mergePrefs(get().prefs, { defaultAskMode: ask });
      savePrefs(prefs);
      applyPrefsToDocument(prefs);
      set({
        prefs,
        autoApprove: !ask,
        permissionScope: normalized,
      });
      const label =
        normalized === "full"
          ? "Full · auto-approve + sandbox network"
          : normalized === "session"
            ? "Auto · risky tools auto-approved"
            : "Ask · confirm risky tools";
      get().showToast(label);
    },

    setPrefs: (patch) => {
      const prefs = mergePrefs(get().prefs, patch);
      savePrefs(prefs);
      applyPrefsToDocument(prefs);
      const next: Partial<AppState> = { prefs };
      if (typeof patch.defaultAskMode === "boolean") {
        next.autoApprove = !prefs.defaultAskMode;
        next.permissionScope = prefs.defaultAskMode ? "ask" : "session";
      }
      if (patch.defaultAgentMode) {
        next.agentMode = prefs.defaultAgentMode;
      }
      set(next);
    },

    setModelOverride: (model) => set({ modelOverride: model }),

    setComposerChip: (kind, value) => set({ composerChip: { kind, value } }),

    clearComposerChip: (opts) => {
      set({ composerChip: { kind: null, value: null } });
      if (opts?.resetMode) set({ agentMode: "normal" });
    },

    showToast: (text) => {
      const toast: ToastMessage = {
        id: uid("t"),
        text,
        createdAt: Date.now(),
      };
      set((s) => ({ toasts: [...s.toasts.slice(-4), toast] }));
      window.setTimeout(() => {
        get().dismissToast(toast.id);
      }, 4200);
    },

    dismissToast: (id) =>
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

    addPendingFiles: (files) => {
      if (!files) return;
      const list = Array.from(files);
      if (!list.length) return;
      set((s) => {
        const keys = new Set(
          s.pendingFiles.map((f) => `${f.name}:${f.size}:${f.lastModified}`),
        );
        const next = [...s.pendingFiles];
        for (const file of list) {
          const key = `${file.name}:${file.size}:${file.lastModified}`;
          if (!keys.has(key)) {
            keys.add(key);
            next.push(file);
          }
        }
        return { pendingFiles: next };
      });
    },

    removePendingFile: (index) =>
      set((s) => ({
        pendingFiles: s.pendingFiles.filter((_, i) => i !== index),
      })),

    clearPendingFiles: () => set({ pendingFiles: [] }),

    refreshSessions: async () => {
      try {
        const data = await api<{ sessions?: SessionSummary[] }>(
          "/api/sessions?limit=40",
        );
        const sessions = sortSessionsPinnedFirst(data.sessions || []);
        const sid = get().sessionId;
        const active = sid
          ? sessions.find((s) => s.session_id === sid)
          : undefined;
        set({
          sessions,
          sessionsError: null,
          bootError: null,
          ...(active && active.title != null
            ? { sessionTitle: String(active.title || "") || null }
            : {}),
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        set({ sessionsError: message });
      }
    },

    probeCapabilities: async () => {
      const files = await apiSoft("/api/project/files?q=&limit=1");
      const comet = await apiSoft("/api/comet");
      const browser = await apiSoft("/api/browser");
      const computer = await apiSoft("/api/computer");
      const models = await apiSoft("/api/models");
      const model = models.ok ? models : await apiSoft("/api/model");
      set((s) => ({
        capabilities: {
          ...s.capabilities,
          projectFiles: files.ok === true,
          cometApi: comet.ok === true,
          browserApi: browser.ok === true,
          computerApi: computer.ok === true,
          modelApi: model.ok === true,
        },
      }));
    },

    loadSlashCatalog: async () => {
      const catalog = await apiSoft("/api/slash-catalog");
      if (catalog.ok && Array.isArray(catalog.data.commands)) {
        const caps = (catalog.data.capabilities || {}) as Record<string, unknown>;
        set((s) => ({
          slashCatalog: mergeServerCatalog(
            catalog.data.commands as unknown[],
            SLASH_COMMANDS,
          ),
          capabilities: {
            ...s.capabilities,
            slashCatalogApi: true,
            cometApi:
              typeof caps.comet === "boolean"
                ? caps.comet
                : s.capabilities.cometApi,
            browserApi:
              typeof caps.browser === "boolean"
                ? caps.browser
                : s.capabilities.browserApi,
            computerApi:
              typeof caps.computer === "boolean"
                ? caps.computer
                : s.capabilities.computerApi,
            modelApi:
              typeof caps.models === "boolean"
                ? caps.models
                : s.capabilities.modelApi,
          },
        }));
      } else {
        set((s) => ({
          slashCatalog: SLASH_COMMANDS.slice(),
          capabilities: { ...s.capabilities, slashCatalogApi: false },
        }));
      }
    },

    loadMeta: async () => {
      try {
        const meta = await api<MetaPayload>("/api/meta");
        set({ meta });
      } catch {
        /* optional */
      }
    },

    loadModels: async () => {
      const res = await apiSoft("/api/models");
      if (!res.ok) return;
      const list = Array.isArray(res.data.models)
        ? res.data.models
        : Array.isArray(res.data.items)
          ? res.data.items
          : [];
      set({
        models: list.map((m) =>
          typeof m === "string"
            ? m
            : String((m as { id?: string; name?: string }).id || (m as { name?: string }).name || ""),
        ).filter(Boolean),
      });
    },

    boot: async () => {
      try {
        void get().probeCapabilities().then(() => get().loadSlashCatalog());
        void get().loadMeta();
        void get().loadModels();
        await get().refreshSessions();
        if (get().sessionsError) {
          set({ bootError: get().sessionsError });
          return;
        }
        set({ bootError: null });
        const restoreId = loadLastSession();
        if (
          restoreId &&
          get().sessions.some((s) => s.session_id === restoreId)
        ) {
          await get().openSession(restoreId);
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        set({ bootError: message });
      }
    },

    openSession: async (sessionId) => {
      if (!sessionId) return;
      const prevId = get().sessionId;
      if (sessionId === prevId) {
        updateRun(sessionId, (r) => ({ ...r, needsAttention: false }));
        const pending = get().pendingApproval;
        if (pending?.sessionId === sessionId || pending?.session_id === sessionId) {
          /* keep banner */
        } else if (!pending) {
          set({ pendingApproval: null });
        }
        return;
      }

      const prevRun = prevId ? get().runs[prevId] : null;
      const stayMultitask =
        get().tabs.length > 1 ||
        get().tabs.includes(sessionId) ||
        Boolean(prevRun?.sending);

      if (stayMultitask) {
        parkActive();
        if (prevId) touchTab(prevId);
        touchTab(sessionId);
      } else if (prevId) {
        set((s) => ({
          tabs: s.tabs.filter((id) => id !== prevId),
        }));
      }

      const parked = get().runs[sessionId];
      if (parked && parked.messages.length) {
        rememberSession(sessionId);
        const run = ensureRun(sessionId);
        set({
          sessionId,
          sessionTitle: get().sessionTitle,
          sessionLoading: false,
          pendingApproval:
            get().pendingApproval?.sessionId === sessionId ||
            get().pendingApproval?.session_id === sessionId
              ? get().pendingApproval
              : null,
          ...syncFromRun({
            ...run,
            needsAttention: false,
            pendingFiles: run.pendingFiles,
          }),
        });
        updateRun(sessionId, (r) => ({ ...r, needsAttention: false }));
        await get().refreshSessions();
        return;
      }

      set({
        runStatus: "running",
        statusLabel: "Opening…",
        error: null,
        sessionLoading: true,
      });
      try {
        const data = await api<{
          session_id: string;
          thread_id?: string;
          title?: string | null;
          status?: string;
          messages?: Array<{ role: string; text: string }>;
          pending_approval?: PendingApproval | null;
          active_turn?: { turn_id?: string; status?: string; phase?: string } | null;
        }>(`/api/sessions/${encodeURIComponent(sessionId)}`);
        const messages = mapHistoryMessages(data.messages);
        const threadId = data.thread_id || `web-${data.session_id}`;
        const turnId = String(data.active_turn?.turn_id || "").trim();
        const run: SessionRun = {
          ...emptyRun(data.session_id, threadId),
          messages,
          status: "idle",
          statusLabel: messages.length
            ? `${messages.length} messages · ${data.status || "Ready"}`
            : "Opened · no chat history yet",
          pendingFiles: [],
        };
        rememberSession(data.session_id);
        set((s) => ({
          sessionId: data.session_id,
          sessionTitle: data.title ?? null,
          sessionLoading: false,
          canvasItems: [],
          canvasSelectedPath: null,
          runs: { ...s.runs, [data.session_id]: run },
          pendingApproval: data.pending_approval
            ? { ...data.pending_approval, sessionId: data.session_id }
            : null,
          ...syncFromRun(run),
        }));
        await get().refreshSessions();
        void get().refreshArtifacts();
        // Backend still has this turn running (survived a reload / tab
        // switch) — reattach and rebuild the live-run UI (Stop button,
        // activity feed) instead of treating it as finished history.
        if (turnId) {
          reattach(data.session_id, threadId, turnId, {
            pendingApproval: data.pending_approval,
          });
        }
      } catch (err) {
        set({
          runStatus: "error",
          statusLabel: "Open failed",
          sessionLoading: false,
          error: err instanceof Error ? err.message : String(err),
        });
        throw err;
      }
    },

    newChat: async () => {
      // Show the CommandCenter hero input — session is created on first send
      const previousId = get().sessionId;
      if (previousId) {
        const prevRun = get().runs[previousId];
        if (prevRun?.sending) {
          // Keep active run in background tab
          await get().newSession({ parallel: true });
          return;
        }
      }
      set({
        sessionId: null,
        threadId: null,
        sessionTitle: null,
        messages: [],
        pendingFiles: [],
        pendingApproval: null,
        sending: false,
        abort: null,
        runStatus: "idle",
        statusLabel: "Ready",
        error: null,
        canvasItems: [],
        canvasSelectedPath: null,
      });
    },

    newSession: async (opts = {}) => {
      const parallel = opts.parallel === true;
      const keepPending =
        opts.keepPending === true ||
        (parallel && get().pendingFiles.length > 0);
      const previousId = get().sessionId;
      const previousRun = previousId ? get().runs[previousId] : null;
      const keepPreviousTab = parallel || Boolean(previousRun?.sending);
      const carriedPending = keepPending ? get().pendingFiles.slice() : [];

      if (keepPreviousTab) {
        parkActive();
      } else if (previousId) {
        set((s) => {
          const rest = { ...s.runs };
          delete rest[previousId];
          return {
            tabs: s.tabs.filter((id) => id !== previousId),
            runs: rest,
            pendingApproval:
              s.pendingApproval?.sessionId === previousId
                ? null
                : s.pendingApproval,
          };
        });
      }

      const data = await api<{ session_id: string; thread_id?: string }>(
        "/api/sessions",
        { method: "POST", body: JSON.stringify({}) },
      );
      const nextId = String(data.session_id || "").trim();
      if (!nextId) throw new Error("server returned no session_id");
      if (previousId && nextId === previousId) {
        throw new Error("new session reused the current session id");
      }
      const threadId = data.thread_id || `web-${nextId}`;
      const run = emptyRun(nextId, threadId);
      run.pendingFiles = carriedPending;
      rememberSession(nextId);
      const prefs = get().prefs;
      set((s) => ({
        sessionId: nextId,
        sessionTitle: null,
        draft: "",
        error: null,
        pendingApproval: null,
        agentMode: prefs.defaultAgentMode,
        autoApprove: !prefs.defaultAskMode,
        permissionScope: prefs.defaultAskMode
          ? "ask"
          : get().permissionScope === "full"
            ? "full"
            : "session",
        runs: { ...s.runs, [nextId]: run },
        tabs: parallel || keepPreviousTab ? [...s.tabs.filter((id) => id !== nextId), nextId] : [],
        pendingFiles: carriedPending,
        ...syncFromRun(run),
      }));
      if (prefs.defaultAgentMode === "normal") {
        get().clearComposerChip({ resetMode: false });
      } else {
        get().setComposerChip("mode", prefs.defaultAgentMode);
      }
      await get().refreshSessions();
    },

    closeTab: async (sessionId) => {
      if (!sessionId) return;
      set((s) => ({
        tabs: s.tabs.filter((id) => id !== sessionId),
      }));
      if (get().sessionId === sessionId) {
        const next = get().tabs[get().tabs.length - 1];
        if (next) await get().openSession(next);
        else {
          set({
            sessionId: null,
            threadId: null,
            sessionTitle: null,
            messages: [],
            pendingFiles: [],
            pendingApproval: null,
            sending: false,
            abort: null,
            runStatus: "idle",
            statusLabel: "Ready",
          });
        }
      }
      await get().refreshSessions();
    },

    ensureSession: async () => {
      const { sessionId, threadId } = get();
      if (sessionId && threadId) return { sessionId, threadId };
      // Never mint a second session just because threadId was dropped from
      // top-level state — recover from the run or the stable web-{id} binding.
      if (sessionId) {
        const run = get().runs[sessionId];
        const recovered =
          (run?.threadId && String(run.threadId)) ||
          threadId ||
          `web-${sessionId}`;
        if (run && !run.threadId) {
          updateRun(sessionId, (r) => ({ ...r, threadId: recovered }));
        } else {
          set({ threadId: recovered });
        }
        return { sessionId, threadId: recovered };
      }
      const data = await api<{ session_id: string; thread_id?: string }>(
        "/api/sessions",
        { method: "POST", body: JSON.stringify({}) },
      );
      const nextId = String(data.session_id || "").trim();
      const nextThread = data.thread_id || `web-${nextId}`;
      rememberSession(nextId);
      const run = emptyRun(nextId, nextThread);
      set((s) => ({
        sessionId: nextId,
        sessionTitle: null,
        runs: { ...s.runs, [nextId]: run },
        ...syncFromRun(run),
      }));
      await get().refreshSessions();
      return { sessionId: nextId, threadId: nextThread };
    },

    renameSession: async (title) => {
      const { sessionId } = get();
      if (!sessionId) return;
      const data = await patchSession(sessionId, { title });
      set({
        sessionTitle:
          data.title != null ? data.title : title,
      });
      await get().refreshSessions();
    },

    deleteSession: async (sessionId) => {
      if (!sessionId) return;
      try {
        await deleteSessionApi(sessionId);
      } catch (err) {
        get().showToast(err instanceof Error ? err.message : String(err));
        throw err;
      }
      set((s) => {
        const sessions = s.sessions.filter((x) => x.session_id !== sessionId);
        const tabs = s.tabs.filter((id) => id !== sessionId);
        const runs = { ...s.runs };
        delete runs[sessionId];
        return {
          sessions,
          tabs,
          runs,
          pendingApproval:
            s.pendingApproval?.sessionId === sessionId ||
            s.pendingApproval?.session_id === sessionId
              ? null
              : s.pendingApproval,
        };
      });
      if (get().sessionId === sessionId) {
        const next = get().tabs[get().tabs.length - 1];
        if (next) await get().openSession(next);
        else {
          rememberSession(null);
          set({
            sessionId: null,
            threadId: null,
            sessionTitle: null,
            messages: [],
            pendingFiles: [],
            pendingApproval: null,
            sending: false,
            abort: null,
            runStatus: "idle",
            statusLabel: "Ready",
          });
        }
      }
      await get().refreshSessions().catch(() => {});
    },

    archiveSession: async (sessionId, archived) => {
      if (!sessionId) return;
      const prev = get().sessions;
      set({
        sessions: applySessionFlagsLocally(prev, sessionId, { archived }),
      });
      try {
        await patchSession(sessionId, { archived });
        await get().refreshSessions();
        // If we archived the focused chat and archived are hidden, stay on it
        // but surface a toast so the rail change is obvious.
        if (archived && get().sessionId === sessionId) {
          get().showToast("Archived · toggle “Show archived” in the rail to find it");
        }
      } catch (err) {
        set({ sessions: prev });
        get().showToast(err instanceof Error ? err.message : String(err));
        throw err;
      }
    },

    pinSession: async (sessionId, pinned) => {
      if (!sessionId) return;
      const prev = get().sessions;
      set({
        sessions: sortSessionsPinnedFirst(
          applySessionFlagsLocally(prev, sessionId, { pinned }),
        ),
      });
      try {
        await patchSession(sessionId, { pinned });
        await get().refreshSessions();
      } catch (err) {
        set({ sessions: prev });
        get().showToast(err instanceof Error ? err.message : String(err));
        throw err;
      }
    },

    sendMessage: async (textOverride) => {
      const text = (textOverride ?? get().draft).trim();
      const files = get().pendingFiles;
      if (!text && !files.length) return;

      if (isModeOnlyComposerText(text) && !files.length) {
        const mode = text.replace(/^\//, "").trim().toLowerCase() as AgentMode;
        get().setAgentMode(mode);
        set({ draft: "" });
        const label = mode[0].toUpperCase() + mode.slice(1);
        get().showToast(
          mode === "normal"
            ? "Normal mode"
            : `${label} mode · describe the real task, then Send`,
        );
        return;
      }

      // Meta slash (admin / bare skill prefix): never mint a session.
      if (!files.length && isMetaOnlySlash(text)) {
        const low = text.toLowerCase();
        if (
          isComputerAdminSlash(text) &&
          get().capabilities.computerApi !== false
        ) {
          const { dispatchNativeAdminSlash } = await import("../lib/slash");
          set({ draft: "" });
          dispatchNativeAdminSlash(text);
          return;
        }
        if (
          (low === "/browser" ||
            low.startsWith("/browser ") ||
            low === "/comet" ||
            low.startsWith("/comet ")) &&
          get().capabilities.browserApi !== false
        ) {
          const { dispatchNativeAdminSlash } = await import("../lib/slash");
          set({ draft: "" });
          dispatchNativeAdminSlash(text);
          return;
        }
        // Bare `/computer` / `/computer_use` / other skill with no task body.
        if (/^\/[a-z][a-z0-9_-]*$/i.test(text)) {
          set({ draft: `${text} ` });
          get().showToast(
            low === "/computer" || low === "/computer_use"
              ? "Computer-use · type a task after /computer, then send"
              : `Skill · type a task after ${text}, then send`,
          );
          return;
        }
      }

      if (get().composerChip.kind === "multitask") {
        get().clearComposerChip({ resetMode: false });
        try {
          await get().newSession({ parallel: true, keepPending: true });
        } catch (err) {
          set({
            runStatus: "error",
            statusLabel: "Error",
            error: err instanceof Error ? err.message : String(err),
          });
          return;
        }
      }

      const { sessionId, threadId } = await get().ensureSession();
      ensureRun(sessionId);

      const active = get().runs[sessionId];
      if (active?.sending) {
        const queued: QueuedMessage = {
          text,
          files: get().pendingFiles.slice(),
        };
        updateRun(sessionId, (r) => ({
          ...r,
          queue: [...r.queue, queued],
          status: "running",
          statusLabel: `Queued · ${r.queue.length + 1}`,
        }));
        set({ draft: "", pendingFiles: [] });
        return;
      }

      let attachments: string[] = [];
      try {
        attachments = await uploadPendingFiles(sessionId, get().pendingFiles);
      } catch (err) {
        set({
          error: err instanceof Error ? err.message : String(err),
          runStatus: "error",
          statusLabel: "Upload failed",
        });
        return;
      }
      set({ pendingFiles: [] });
      updateRun(sessionId, (r) => ({ ...r, pendingFiles: [] }));

      const display =
        text +
        (attachments.length
          ? `\n\nAttached files:\n${attachments.map((p) => `- \`${p}\``).join("\n")}`
          : "");

      const slashMode = parseModeSlash(text);
      if (slashMode) get().setAgentMode(slashMode);
      const mode = slashMode || get().agentMode;

      await runTurn(sessionId, threadId, text, attachments, display, {
        agentMode: mode,
      });
    },

    stopGeneration: async () => {
      const { sessionId, threadId, abort } = get();
      abort?.abort();
      if (sessionId) {
        try {
          await api("/api/chat/cancel", {
            method: "POST",
            body: JSON.stringify({
              session_id: sessionId,
              thread_id: threadId,
            }),
          });
        } catch {
          /* best-effort */
        }
        updateRun(sessionId, (r) => ({
          ...r,
          sending: false,
          abort: null,
          status: "cancelled",
          statusLabel: "Cancelled",
        }));
      } else {
        set({
          sending: false,
          abort: null,
          runStatus: "cancelled",
          statusLabel: "Cancelled",
        });
      }
    },

    setCanvasOpen: (open) => set({ canvasOpen: open }),
    setCanvasExpanded: (expanded) => set({ canvasExpanded: expanded }),
    selectCanvasItem: (path) => set({ canvasSelectedPath: path }),

    openCanvasItem: (path, opts) => {
      const sid = get().sessionId;
      if (!sid || !path) return;
      get().upsertCanvasPaths([path]);
      set({
        canvasOpen: true,
        canvasSelectedPath: path,
        canvasExpanded: Boolean(opts?.expand),
      });
    },

    upsertCanvasPaths: (paths) => {
      const sid = get().sessionId;
      if (!sid || !paths.length) return;
      const hadItems = get().canvasItems.length > 0;
      set((s) => {
        const byPath = new Map(s.canvasItems.map((i) => [i.path, i]));
        for (const raw of paths) {
          const path = String(raw || "").replace(/\\/g, "/").replace(/^\/+/, "");
          if (!path || !isShowcaseArtifact(path) || byPath.has(path)) continue;
          const item = toCanvasItem(sid, path);
          if (item) byPath.set(path, item);
        }
        const canvasItems = Array.from(byPath.values()).sort((a, b) => {
          const [ra, pa] = showcaseSortKey(a.path);
          const [rb, pb] = showcaseSortKey(b.path);
          return ra - rb || pa.localeCompare(pb);
        });
        // Auto-open canvas when first artifacts appear.
        const shouldAutoOpen = !hadItems && canvasItems.length > 0 && !s.canvasOpen;
        return {
          canvasItems,
          canvasOpen: shouldAutoOpen ? true : s.canvasOpen,
          canvasSelectedPath:
            s.canvasSelectedPath && byPath.has(s.canvasSelectedPath)
              ? s.canvasSelectedPath
              : canvasItems[0]?.path || null,
        };
      });
    },

    refreshArtifacts: async () => {
      const sid = get().sessionId;
      if (!sid) {
        set({ canvasItems: [], canvasSelectedPath: null });
        return;
      }
      try {
        const data = await api<{ artifacts?: ArtifactEntry[] }>(
          `/api/sessions/${encodeURIComponent(sid)}/artifacts`,
        );
        const items: CanvasItem[] = [];
        for (const row of data.artifacts || []) {
          const path = String(row.path || "").replace(/\\/g, "/");
          if (!path || !isShowcaseArtifact(path)) continue;
          const item = toCanvasItem(sid, path, {
            kindHint: row.kind,
            url: row.url,
            size: typeof row.size === "number" ? row.size : undefined,
            name: row.name,
          });
          if (item) items.push(item);
        }
        items.sort((a, b) => {
          const [ra, pa] = showcaseSortKey(a.path);
          const [rb, pb] = showcaseSortKey(b.path);
          return ra - rb || pa.localeCompare(pb);
        });
        set((s) => ({
          canvasItems: items,
          canvasSelectedPath:
            s.canvasSelectedPath &&
            items.some((i) => i.path === s.canvasSelectedPath)
              ? s.canvasSelectedPath
              : items[0]?.path || null,
        }));
      } catch (err) {
        get().showToast(
          `Artifacts: ${err instanceof Error ? err.message : err}`,
        );
      }
    },

    resolveApproval: async (approved, feedback, scope = "once") => {
      const pending = get().pendingApproval;
      if (!pending?.approval_id) return;
      const sid = pending.sessionId || pending.session_id || get().sessionId;
      const isPlan =
        pending.risk_class === "plan" || pending.action === "approve_plan";
      const note = String(feedback || "").trim();
      const grant =
        approved && !isPlan && (scope === "session" || scope === "full")
          ? scope
          : "once";

      set({ pendingApproval: null });
      if (sid) {
        updateRun(sid, (r) => ({ ...r, waitingApproval: false }));
      }
      try {
        await api("/api/approvals", {
          method: "POST",
          body: JSON.stringify({
            approval_id: pending.approval_id,
            approved: Boolean(approved),
            feedback: note,
            scope: grant,
          }),
        });
        if (approved && grant !== "once") {
          const prefs = mergePrefs(get().prefs, { defaultAskMode: false });
          savePrefs(prefs);
          applyPrefsToDocument(prefs);
          set({
            prefs,
            autoApprove: true,
            permissionScope: grant,
          });
        }
        if (!sid || sid === get().sessionId) {
          let label: string;
          if (approved) {
            if (isPlan) label = "Build · executing…";
            else if (grant === "full") label = "Full access · continuing…";
            else if (grant === "session") label = "Session grant · continuing…";
            else label = "Approved · continuing…";
          } else if (note) {
            label = isPlan
              ? "Suggestion · revising plan…"
              : "Suggestion · continuing…";
          } else {
            label = "Denied · continuing…";
          }
          set({ runStatus: "running", statusLabel: label });
        }
      } catch (err) {
        get().showToast(
          `Approval failed: ${err instanceof Error ? err.message : err}`,
        );
      }
    },

    appendLocalMessage: (role, text) => {
      const body = String(text || "").trim();
      if (!body) return;
      const sid = get().sessionId;
      const msg: ChatMessage = {
        id: uid(role === "user" ? "u" : "a"),
        role,
        text: body,
      };
      if (!sid) {
        set((s) => ({ messages: [...s.messages, msg] }));
        return;
      }
      updateRun(sid, (run) => ({
        ...run,
        messages: [...run.messages, msg],
      }));
    },

    effectiveSlashCommands: () =>
      filterSlashByCapabilities(get().slashCatalog, get().capabilities),
  };
});
