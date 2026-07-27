import type {
  AgentMode,
  ArtifactEntry,
  BonLiveState,
  ChatMessage,
  ComposerChip,
  DesignPanelState,
  DrawerName,
  DrawersState,
  JobSummary,
  JobsCounts,
  MemoryItem,
  MetaPayload,
  NewSessionOptions,
  PendingApproval,
  ReviewResult,
  RunStatus,
  SessionRun,
  SessionSummary,
  SlashCommand,
  TodoBoard,
  ToastMessage,
  UserPrefs,
  WebUiCapabilities,
  WorkbenchTab,
} from "../api/types";

export type { UserPrefs };

export interface AppState {
  sessions: SessionSummary[];
  sessionId: string | null;
  threadId: string | null;
  sessionTitle: string | null;
  /** Active-session convenience mirror of runs[sessionId].messages */
  messages: ChatMessage[];
  runStatus: RunStatus;
  statusLabel: string;
  agentMode: AgentMode;
  autoApprove: boolean;
  sending: boolean;
  draft: string;
  error: string | null;
  abort: AbortController | null;
  modelOverride: string;
  pendingFiles: File[];
  pendingApproval: PendingApproval | null;
  composerChip: ComposerChip;
  tabs: string[];
  runs: Record<string, SessionRun>;
  drawers: DrawersState;
  design: DesignPanelState;
  artifacts: ArtifactEntry[];
  memoryKinds: string[];
  memoryStates: string[];
  memorySelectedKinds: string[];
  memoryQuery: string;
  memoryStateFilter: string;
  memoryResults: MemoryItem[];
  memoryTraceId: string | null;
  memorySearching: boolean;
  jobs: JobSummary[];
  jobsCounts: JobsCounts | null;
  jobsFilter: string;
  jobsLoading: boolean;
  workbenchTab: WorkbenchTab;
  bonLive: BonLiveState | null;
  bonObjective: string;
  bonN: number;
  reviewResult: ReviewResult | null;
  todoBoards: Record<string, TodoBoard>;
  todoBoardDismissed: string[];
  slashCatalog: SlashCommand[];
  capabilities: WebUiCapabilities;
  meta: MetaPayload | null;
  toasts: ToastMessage[];
  models: string[];
  bootError: string | null;
  connectionOnline: boolean;
  sessionLoading: boolean;
  sessionsError: string | null;
  prefs: UserPrefs;

  setDraft: (value: string) => void;
  setConnectionOnline: (online: boolean) => void;
  clearError: () => void;
  retryBoot: () => Promise<void>;
  retryLastTurn: () => Promise<void>;
  setAgentMode: (mode: AgentMode) => void;
  setAskMode: (ask: boolean) => void;
  setPrefs: (patch: Partial<UserPrefs>) => void;
  setModelOverride: (model: string) => void;
  setComposerChip: (kind: ComposerChip["kind"], value: string | null) => void;
  clearComposerChip: (opts?: { resetMode?: boolean }) => void;
  showToast: (text: string) => void;
  dismissToast: (id: string) => void;

  openDrawer: (name: DrawerName) => void;
  closeDrawer: (name: DrawerName) => void;
  toggleDrawer: (name: DrawerName) => void;
  setWorkbenchTab: (tab: WorkbenchTab) => void;

  addPendingFiles: (files: FileList | File[] | null | undefined) => void;
  removePendingFile: (index: number) => void;
  clearPendingFiles: () => void;

  refreshSessions: () => Promise<void>;
  probeCapabilities: () => Promise<void>;
  loadSlashCatalog: () => Promise<void>;
  loadMeta: () => Promise<void>;
  loadModels: () => Promise<void>;
  boot: () => Promise<void>;

  openSession: (sessionId: string) => Promise<void>;
  /** Fresh chat in place — previous stays in sidebar history. */
  newChat: () => Promise<void>;
  /** Create session; pass `{ parallel: true }` for multitask tab. */
  newSession: (opts?: NewSessionOptions) => Promise<void>;
  closeTab: (sessionId: string) => Promise<void>;
  ensureSession: () => Promise<{ sessionId: string; threadId: string }>;
  renameSession: (title: string) => Promise<void>;
  deleteSession: (sessionId: string) => Promise<void>;
  archiveSession: (sessionId: string, archived: boolean) => Promise<void>;
  pinSession: (sessionId: string, pinned: boolean) => Promise<void>;

  sendMessage: (textOverride?: string) => Promise<void>;
  stopGeneration: () => Promise<void>;
  resolveApproval: (approved: boolean) => Promise<void>;

  loadDesign: (opts?: {
    forceBuild?: boolean;
    activeFile?: string;
    awaitingClarify?: boolean;
  }) => Promise<void>;
  saveDesign: (opts?: { force?: boolean; file?: string }) => Promise<void>;
  setDesignFile: (name: string, content: string) => void;
  setDesignActiveFile: (name: string) => void;
  buildDesign: () => Promise<void>;

  refreshArtifacts: () => Promise<void>;

  setMemoryQuery: (q: string) => void;
  setMemoryStateFilter: (state: string) => void;
  toggleMemoryKind: (kind: string) => void;
  searchMemory: () => Promise<void>;

  refreshJobs: () => Promise<void>;
  createJob: (objective: string) => Promise<void>;
  cancelJob: (jobId: string) => Promise<void>;
  attachJob: (jobId: string) => Promise<void>;
  setJobsFilter: (filter: string) => void;

  runBestOfN: (objective?: string, n?: number) => Promise<void>;
  runReview: (opts?: Record<string, unknown>) => Promise<void>;

  applyTodoBoard: (board: unknown, sessionId?: string | null) => void;
  dismissTodoBoard: (sessionId?: string | null) => void;

  /** Local transcript note (browser/computer slash results, system notices). */
  appendLocalMessage: (
    role: "assistant" | "system" | "user",
    text: string,
  ) => void;

  effectiveSlashCommands: () => SlashCommand[];
}
