import type {
  AgentMode,
  ChatMessage,
  ComposerChip,
  MetaPayload,
  NewSessionOptions,
  PendingApproval,
  RunStatus,
  SessionRun,
  SessionSummary,
  SlashCommand,
  ToastMessage,
  UserPrefs,
  WebUiCapabilities,
} from "../api/types";
import type { CanvasItem } from "../lib/artifactMedia";

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
  /** ask | session | full — mirrors CLI /permissions */
  permissionScope: "ask" | "session" | "full";
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

  /** Artifact canvas (images / video / pdf / office files). */
  canvasOpen: boolean;
  canvasExpanded: boolean;
  canvasItems: CanvasItem[];
  canvasSelectedPath: string | null;

  setDraft: (value: string) => void;
  setConnectionOnline: (online: boolean) => void;
  clearError: () => void;
  retryBoot: () => Promise<void>;
  retryLastTurn: () => Promise<void>;
  setAgentMode: (mode: AgentMode) => void;
  setAskMode: (ask: boolean) => void;
  setPermissionsMode: (mode: "ask" | "auto" | "full") => Promise<void>;
  setPrefs: (patch: Partial<UserPrefs>) => void;
  setModelOverride: (model: string) => void;
  setComposerChip: (kind: ComposerChip["kind"], value: string | null) => void;
  clearComposerChip: (opts?: { resetMode?: boolean }) => void;
  showToast: (text: string) => void;
  dismissToast: (id: string) => void;

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
  resolveApproval: (
    approved: boolean,
    feedback?: string,
    scope?: "once" | "session" | "full",
  ) => Promise<void>;

  setCanvasOpen: (open: boolean) => void;
  setCanvasExpanded: (expanded: boolean) => void;
  selectCanvasItem: (path: string | null) => void;
  openCanvasItem: (path: string, opts?: { expand?: boolean }) => void;
  refreshArtifacts: () => Promise<void>;
  upsertCanvasPaths: (paths: string[]) => void;

  /** Local transcript note (browser/computer slash results, system notices). */
  appendLocalMessage: (
    role: "assistant" | "system" | "user",
    text: string,
  ) => void;

  effectiveSlashCommands: () => SlashCommand[];
}
