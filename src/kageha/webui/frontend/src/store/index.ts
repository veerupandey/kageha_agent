import { create } from "zustand";
import { api } from "../api/client";
import {
  filterSlashByCapabilities,
  mergeServerCatalog,
  SLASH_COMMANDS,
} from "../api/slashCatalog";
import { streamBestOfN } from "../api/stream";
import type {
  AgentMode,
  ArtifactEntry,
  BonAttempt,
  ChatMessage,
  JobSummary,
  JobsCounts,
  MemorySearchResult,
  MetaPayload,
  PendingApproval,
  QueuedMessage,
  ReviewResult,
  SessionRun,
  SessionSummary,
  ToastMessage,
} from "../api/types";
import {
  apiSoft,
  emptyDesign,
  emptyRun,
  isComputerAdminSlash,
  isMetaOnlySlash,
  isModeOnlyComposerText,
  loadLastSession,
  loadWorkbenchTab,
  mapHistoryMessages,
  normalizeTodoBoard,
  parseModeSlash,
  rememberSession,
  syncFromRun,
  uid,
  WORKBENCH_TAB_KEY,
} from "./helpers";
import {
  applyPrefsToDocument,
  loadPrefs,
  mergePrefs,
  savePrefs,
} from "./prefs";
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
    drawers: {
      design: false,
      artifacts: false,
      memory: false,
      jobs: false,
      labs: false,
      workbench: false,
      settings: false,
    },
    prefs: initialPrefs,
    design: emptyDesign(),
    artifacts: [],
    memoryKinds: [],
    memoryStates: [],
    memorySelectedKinds: [],
    memoryQuery: "",
    memoryStateFilter: "",
    memoryResults: [],
    memoryTraceId: null,
    memorySearching: false,
    jobs: [],
    jobsCounts: null,
    jobsFilter: "",
    jobsLoading: false,
    workbenchTab: loadWorkbenchTab(),
    bonLive: null,
    bonObjective: "",
    bonN: 2,
    reviewResult: null,
    todoBoards: {},
    todoBoardDismissed: [],
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
      set((s) => {
        const next: Partial<AppState> = { agentMode: mode };
        if (mode === "normal") {
          next.composerChip = { kind: null, value: null };
          // Leaving Plan/Spec: hide sticky Build foot unless a live approval waits.
          if (!s.pendingApproval && s.design.awaitingBuild) {
            next.design = { ...s.design, awaitingBuild: false };
          }
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
      set({ prefs, autoApprove: !ask });
    },

    setPrefs: (patch) => {
      const prefs = mergePrefs(get().prefs, patch);
      savePrefs(prefs);
      applyPrefsToDocument(prefs);
      const next: Partial<AppState> = { prefs };
      if (typeof patch.defaultAskMode === "boolean") {
        next.autoApprove = !prefs.defaultAskMode;
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

    openDrawer: (name) =>
      set((s) => ({ drawers: { ...s.drawers, [name]: true } })),
    closeDrawer: (name) =>
      set((s) => ({ drawers: { ...s.drawers, [name]: false } })),
    toggleDrawer: (name) =>
      set((s) => ({ drawers: { ...s.drawers, [name]: !s.drawers[name] } })),

    setWorkbenchTab: (tab) => {
      try {
        localStorage.setItem(WORKBENCH_TAB_KEY, tab);
      } catch {
        /* ignore */
      }
      set((s) => ({
        workbenchTab: tab,
        drawers: { ...s.drawers, workbench: true },
      }));
    },

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
        set({
          sessions: sortSessionsPinnedFirst(data.sessions || []),
          sessionsError: null,
          bootError: null,
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
        set({
          meta,
          memoryKinds: meta.memory_kinds || [],
          memoryStates: meta.memory_states || [],
        });
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
        if (get().todoBoards[sessionId]) {
          /* board already cached */
        }
        await get().refreshArtifacts().catch(() => {});
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
          todo_board?: unknown;
          pending_approval?: PendingApproval | null;
        }>(`/api/sessions/${encodeURIComponent(sessionId)}`);
        const messages = mapHistoryMessages(data.messages);
        const threadId = data.thread_id || `web-${data.session_id}`;
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
          runs: { ...s.runs, [data.session_id]: run },
          pendingApproval: data.pending_approval
            ? { ...data.pending_approval, sessionId: data.session_id }
            : null,
          ...syncFromRun(run),
        }));
        if (data.todo_board) get().applyTodoBoard(data.todo_board, data.session_id);
        await get().refreshArtifacts().catch(() => {});
        await get().refreshSessions();
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
      await get().newSession({ parallel: false });
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
          const todoBoards = { ...s.todoBoards };
          delete todoBoards[previousId];
          return {
            tabs: s.tabs.filter((id) => id !== previousId),
            runs: rest,
            todoBoards,
            todoBoardDismissed: s.todoBoardDismissed.filter(
              (id) => id !== previousId,
            ),
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
        design: emptyDesign(),
        artifacts: [],
        agentMode: prefs.defaultAgentMode,
        autoApprove: !prefs.defaultAskMode,
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
      get().applyTodoBoard(null, nextId);
      await get().refreshSessions();
    },

    closeTab: async (sessionId) => {
      if (!sessionId) return;
      set((s) => {
        const tabs = s.tabs.filter((id) => id !== sessionId);
        const todoBoards = { ...s.todoBoards };
        delete todoBoards[sessionId];
        return {
          tabs,
          todoBoards,
          todoBoardDismissed: s.todoBoardDismissed.filter((id) => id !== sessionId),
        };
      });
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
        const todoBoards = { ...s.todoBoards };
        delete todoBoards[sessionId];
        return {
          sessions,
          tabs,
          runs,
          todoBoards,
          todoBoardDismissed: s.todoBoardDismissed.filter((id) => id !== sessionId),
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

    resolveApproval: async (approved) => {
      const pending = get().pendingApproval;
      if (!pending?.approval_id) return;
      const sid = pending.sessionId || pending.session_id || get().sessionId;
      const isPlan =
        pending.risk_class === "plan" || pending.action === "approve_plan";
      const isClarify =
        pending.risk_class === "clarify" || pending.action === "spec_clarify";

      if (
        approved &&
        ((isPlan && get().design.dirty) ||
          (isClarify &&
            (get().design.dirty || get().design.activeFile === "requirements.md")))
      ) {
        try {
          await get().saveDesign({
            force: true,
            file: isClarify ? "requirements.md" : undefined,
          });
        } catch (err) {
          get().showToast(
            `Save failed before continue: ${err instanceof Error ? err.message : err}`,
          );
          return;
        }
      }

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
          }),
        });
        if (!sid || sid === get().sessionId) {
          const label = approved
            ? isClarify
              ? "Clarify · planning…"
              : isPlan
                ? "Build · executing…"
                : "Approved · continuing…"
            : "Denied · continuing…";
          set({ runStatus: "running", statusLabel: label });
          if (isClarify) {
            set((s) => ({
              design: { ...s.design, awaitingClarify: false },
            }));
          }
          if (isPlan) {
            set((s) => ({
              design: {
                ...s.design,
                awaitingBuild: false,
                dirty: false,
              },
            }));
          }
        }
      } catch (err) {
        get().showToast(
          `Approval failed: ${err instanceof Error ? err.message : err}`,
        );
      }
    },

    loadDesign: async (opts = {}) => {
      const { sessionId } = get();
      if (!sessionId) return;
      if (get().design.dirty) {
        try {
          await get().saveDesign({ force: true });
        } catch {
          /* continue load */
        }
      }
      const data = await api<{
        files?: Record<string, string>;
        agent_mode?: string;
        phases?: string[];
        awaiting_clarify?: boolean;
        awaiting_build?: boolean;
        explore_status?: Record<string, unknown> | null;
        explore_degraded?: boolean;
      }>(`/api/sessions/${encodeURIComponent(sessionId)}/design`);
      const files = data.files || {};
      if (!Object.keys(files).length && !opts.forceBuild) {
        set((s) => ({ drawers: { ...s.drawers, design: false } }));
        return;
      }
      const awaitingClarify = Boolean(
        data.awaiting_clarify || opts.awaitingClarify,
      );
      const designMode = (data.agent_mode || "plan") as AgentMode;
      const awaitingBuild = Boolean(
        (data.awaiting_build || opts.forceBuild) &&
          !awaitingClarify &&
          (opts.forceBuild ||
            get().agentMode === "plan" ||
            get().agentMode === "spec"),
      );
      set((s) => ({
        drawers: { ...s.drawers, design: true },
        agentMode:
          awaitingBuild && s.agentMode === "normal" ? designMode : s.agentMode,
        design: {
          ...s.design,
          files,
          agentMode: designMode,
          phases: Array.isArray(data.phases) ? data.phases : [],
          awaitingClarify,
          awaitingBuild,
          dirty: false,
          activeFile:
            opts.activeFile ||
            s.design.activeFile ||
            (awaitingClarify ? "requirements.md" : "plan.md"),
          exploreStatus: data.explore_status || null,
          exploreDegraded: Boolean(data.explore_degraded),
        },
      }));
    },

    saveDesign: async (opts = {}) => {
      const { sessionId, design } = get();
      if (!sessionId) return;
      const name = opts.file || design.activeFile || "plan.md";
      if (!design.dirty && !opts.force) return;
      const content = String(design.files[name] ?? "");
      set((s) => ({ design: { ...s.design, saving: true } }));
      try {
        const data = await api<{
          files?: Record<string, string>;
          awaiting_build?: boolean;
        }>(`/api/sessions/${encodeURIComponent(sessionId)}/design`, {
          method: "PUT",
          body: JSON.stringify({ file: name, content }),
        });
        set((s) => ({
          design: {
            ...s.design,
            files: data.files
              ? { ...s.design.files, ...data.files }
              : { ...s.design.files, [name]: content },
            dirty: false,
            saving: false,
            awaitingBuild:
              data.awaiting_build != null
                ? Boolean(data.awaiting_build)
                : s.design.awaitingBuild,
          },
        }));
      } catch (err) {
        set((s) => ({ design: { ...s.design, saving: false } }));
        throw err;
      }
    },

    setDesignFile: (name, content) =>
      set((s) => ({
        design: {
          ...s.design,
          files: { ...s.design.files, [name]: content },
          dirty: true,
        },
      })),

    setDesignActiveFile: (name) =>
      set((s) => ({ design: { ...s.design, activeFile: name } })),

    buildDesign: async () => {
      const { sessionId, threadId, design } = get();
      if (design.awaitingClarify) {
        get().showToast(
          "Use Continue on the approval banner after editing requirements.",
        );
        return;
      }
      if (!design.awaitingBuild) {
        get().showToast("Nothing waiting for Build.");
        return;
      }
      if (!sessionId || !threadId) {
        get().showToast("Open a session before Build.");
        return;
      }
      if (design.dirty) {
        await get().saveDesign({ force: true });
      }
      const mode = (design.agentMode || get().agentMode || "plan") as AgentMode;
      const message =
        mode === "spec" ? "Build the approved spec." : "Build the approved plan.";
      await runTurn(sessionId, threadId, message, [], message, {
        autoBuild: true,
        agentMode: mode,
      });
    },

    refreshArtifacts: async () => {
      const { sessionId } = get();
      if (!sessionId) {
        set({ artifacts: [] });
        return;
      }
      try {
        const data = await api<{
          artifacts?: ArtifactEntry[];
          items?: ArtifactEntry[];
        }>(`/api/sessions/${encodeURIComponent(sessionId)}/artifacts`);
        set({
          artifacts: data.artifacts || data.items || [],
        });
      } catch {
        set({ artifacts: [] });
      }
    },

    setMemoryQuery: (q) => set({ memoryQuery: q }),
    setMemoryStateFilter: (state) => set({ memoryStateFilter: state }),
    toggleMemoryKind: (kind) =>
      set((s) => {
        const has = s.memorySelectedKinds.includes(kind);
        return {
          memorySelectedKinds: has
            ? s.memorySelectedKinds.filter((k) => k !== kind)
            : [...s.memorySelectedKinds, kind],
        };
      }),

    searchMemory: async () => {
      set({ memorySearching: true });
      try {
        const data = await api<MemorySearchResult>("/api/memory/search", {
          method: "POST",
          body: JSON.stringify({
            query: get().memoryQuery.trim(),
            kinds: get().memorySelectedKinds,
            state: get().memoryStateFilter,
            session_id: "",
            max_results: 24,
          }),
        });
        set({
          memoryResults: data.items || [],
          memoryTraceId: data.trace_id || null,
          memorySearching: false,
        });
      } catch (err) {
        set({
          memorySearching: false,
          memoryResults: [],
          error: err instanceof Error ? err.message : String(err),
        });
      }
    },

    refreshJobs: async () => {
      set({ jobsLoading: true });
      try {
        const filter = String(get().jobsFilter || "").trim();
        const qs = filter
          ? `?limit=40&status=${encodeURIComponent(filter)}`
          : "?limit=40";
        const data = await api<{
          jobs?: JobSummary[];
          counts?: JobsCounts;
        }>(`/api/jobs${qs}`);
        set({
          jobs: data.jobs || [],
          jobsCounts: data.counts || null,
          jobsLoading: false,
        });
      } catch {
        set({ jobsLoading: false, jobs: [] });
      }
    },

    createJob: async (objective) => {
      const result = await api<JobSummary>("/api/jobs", {
        method: "POST",
        body: JSON.stringify({ objective: objective.trim() }),
      });
      get().showToast(`Job ${result.id} · ${result.status || "queued"}`);
      await get().refreshJobs();
    },

    cancelJob: async (jobId) => {
      await api(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      await get().refreshJobs();
    },

    attachJob: async (jobId) => {
      const info = await api<{ session_id?: string; run_id?: string }>(
        `/api/jobs/${encodeURIComponent(jobId)}/attach`,
      );
      const sid = info.session_id || info.run_id;
      if (sid) await get().openSession(sid);
    },

    setJobsFilter: (filter) => {
      set({ jobsFilter: filter });
      void get().refreshJobs();
    },

    runBestOfN: async (objective, n) => {
      const obj = (objective ?? get().bonObjective).trim();
      if (!obj) {
        get().setWorkbenchTab("bon");
        get().showToast("Enter a Best-of-N objective");
        return;
      }
      const count = Math.max(
        2,
        Math.min(5, n ?? get().bonN ?? 2),
      );
      get().setWorkbenchTab("bon");
      set({
        runStatus: "running",
        statusLabel: `Best-of-${count}…`,
        bonObjective: obj,
        bonN: count,
        bonLive: {
          n: count,
          attempts: [],
          placeholders: Object.fromEntries(
            Array.from({ length: count }, (_, i) => [
              i,
              {
                index: i,
                label: `n${i + 1}`,
                running: true,
                status: "queued",
                score: 0,
              },
            ]),
          ),
          winner_index: null,
          objective: obj,
        },
      });

      const upsert = (attempt: BonAttempt) => {
        set((s) => {
          if (!s.bonLive) return s;
          const idx = Number(attempt.index);
          const placeholders = {
            ...s.bonLive.placeholders,
            [idx]: {
              ...s.bonLive.placeholders[idx],
              ...attempt,
            },
          };
          const list = [...s.bonLive.attempts];
          const at = list.findIndex((a) => a.index === idx);
          if (at >= 0) list[at] = { ...list[at], ...attempt };
          else list.push(attempt);
          return {
            bonLive: {
              ...s.bonLive,
              placeholders,
              attempts: list,
            },
          };
        });
      };

      try {
        const streamed = await streamBestOfN(
          {
            objective: obj,
            n: count,
            auto_approve: get().autoApprove,
          },
          {
            onFrame: (event, data) => {
              if (event === "attempt_started" || event === "attempt_ready") {
                const idx = Number(data.index);
                upsert({
                  index: idx,
                  label: String(data.label || `n${idx + 1}`),
                  running: true,
                  status:
                    event === "attempt_ready" ? "running" : "starting",
                  worktree: data.worktree ? String(data.worktree) : "",
                  branch: data.branch ? String(data.branch) : "",
                  score: 0,
                });
              } else if (event === "attempt_done" || event === "attempt_failed") {
                upsert({
                  ...(data as BonAttempt),
                  index: Number(data.index),
                  running: false,
                  ok: event !== "attempt_failed",
                });
              } else if (event === "done") {
                set((s) =>
                  s.bonLive
                    ? {
                        bonLive: {
                          ...s.bonLive,
                          winner_index:
                            data.winner_index != null
                              ? Number(data.winner_index)
                              : s.bonLive.winner_index,
                          attempts: Array.isArray(data.attempts)
                            ? (data.attempts as BonAttempt[])
                            : s.bonLive.attempts,
                        },
                      }
                    : s,
                );
              }
            },
          },
        );

        if (!streamed) {
          const result = await api<{
            winner?: BonAttempt;
            winner_index?: number;
            attempts?: BonAttempt[];
            n?: number;
            message?: string;
          }>("/api/best-of-n", {
            method: "POST",
            body: JSON.stringify({
              objective: obj,
              n: count,
              auto_approve: get().autoApprove,
            }),
          });
          set({
            bonLive: {
              n: result.n || count,
              attempts: result.attempts || [],
              placeholders: {},
              winner_index:
                result.winner_index != null ? result.winner_index : null,
              objective: obj,
            },
            runStatus: "idle",
            statusLabel: "Ready",
          });
          const winner = result.winner;
          get().showToast(
            winner
              ? `Winner: ${winner.label} (score ${Number(winner.score || 0).toFixed(2)})`
              : "No winner selected.",
          );
          return;
        }

        set({ runStatus: "idle", statusLabel: "Ready" });
      } catch (err) {
        set({
          runStatus: "error",
          statusLabel: "Best-of-N failed",
          error: err instanceof Error ? err.message : String(err),
        });
      }
    },

    runReview: async (opts = {}) => {
      get().setWorkbenchTab("review");
      set({ runStatus: "running", statusLabel: "Review…" });
      try {
        const result = await api<ReviewResult>("/api/review", {
          method: "POST",
          body: JSON.stringify(opts),
        });
        set({
          reviewResult: result,
          runStatus: "idle",
          statusLabel: "Ready",
        });
      } catch (err) {
        set({
          runStatus: "error",
          statusLabel: "Review failed",
          error: err instanceof Error ? err.message : String(err),
        });
        throw err;
      }
    },

    applyTodoBoard: (board, sessionId) => {
      const sid = sessionId || get().sessionId;
      if (!sid) return;
      const normalized = normalizeTodoBoard(board);
      set((s) => {
        const todoBoards = { ...s.todoBoards };
        let dismissed = s.todoBoardDismissed;
        if (!normalized) {
          delete todoBoards[sid];
          dismissed = dismissed.filter((id) => id !== sid);
        } else {
          const prev = todoBoards[sid];
          todoBoards[sid] = normalized;
          if (
            dismissed.includes(sid) &&
            prev &&
            (prev.done !== normalized.done || prev.total !== normalized.total)
          ) {
            dismissed = dismissed.filter((id) => id !== sid);
          }
        }
        return { todoBoards, todoBoardDismissed: dismissed };
      });
    },

    dismissTodoBoard: (sessionId) => {
      const sid = sessionId || get().sessionId;
      if (!sid) return;
      set((s) =>
        s.todoBoardDismissed.includes(sid)
          ? s
          : { todoBoardDismissed: [...s.todoBoardDismissed, sid] },
      );
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
