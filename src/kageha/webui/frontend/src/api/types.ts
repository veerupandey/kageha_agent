export type AgentMode = "normal" | "plan" | "goal";

export type RunStatus =
  | "idle"
  | "running"
  | "success"
  | "error"
  | "cancelled"
  | "waiting_approval";

export type ComposerChipKind = "mode" | "multitask";

export type SlashCommandKind =
  | "mode"
  | "multitask"
  | "prefs"
  | "browser"
  | "computer"
  | "project"
  | "skill"
  | string;

export interface SessionSummary {
  session_id: string;
  thread_id?: string;
  title?: string | null;
  status?: string;
  turn_status?: string;
  turn_phase?: string | null;
  active?: boolean;
  updated_at?: string | number | null;
  objective?: string | null;
  pinned?: boolean;
  archived?: boolean;
}

export interface ToolCard {
  id: string;
  name: string;
  argsPreview?: string;
  status?: string;
  durationMs?: number | null;
  artifactRefs?: string[];
  resultPreview?: string;
}

export interface ComputerFrame {
  url: string;
  path?: string;
  action?: string;
  app?: string;
  caption?: string;
}

export interface PendingApproval {
  approval_id: string;
  sessionId?: string | null;
  session_id?: string | null;
  action?: string;
  risk_class?: string;
  detail?: string | string[];
  label?: string;
}

/** One human-readable Activity / Trace row from the live turn stream. */
export interface ActivityStep {
  label: string;
  detail?: string[];
  kind?: string;
  interesting?: boolean;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  streaming?: boolean;
  statusLabel?: string;
  /** Optional subtitle under the live pulse (first status/event detail line). */
  statusDetail?: string;
  steps?: ActivityStep[];
  toolCards?: ToolCard[];
  computerFrames?: ComputerFrame[];
  approval?: PendingApproval | null;
}

export interface QueuedMessage {
  text: string;
  files: File[];
}

export interface SessionRun {
  sessionId: string;
  threadId: string | null;
  messages: ChatMessage[];
  sending: boolean;
  status: RunStatus;
  statusLabel: string;
  queue: QueuedMessage[];
  abort: AbortController | null;
  waitingApproval: boolean;
  needsAttention: boolean;
  pendingFiles: File[];
}

export interface ComposerChip {
  kind: ComposerChipKind | null;
  /** Mode id or "multitask". */
  value: string | null;
}

export type Density = "comfortable" | "compact";

export type ThemeMode = "light" | "dark";

export interface UserPrefs {
  density: Density;
  /** Ask before risky tools on boot / new sessions. */
  defaultAskMode: boolean;
  defaultAgentMode: AgentMode;
  reduceMotion: boolean;
  theme: ThemeMode;
  /** Auto-speak assistant replies via Gemini TTS. */
  voiceReply: boolean;
  /** Enable new canvas UI. */
  newUi: boolean;
}

export interface ArtifactEntry {
  path: string;
  name?: string;
  size?: number;
  kind?: string;
  modified_at?: string | number | null;
  url?: string;
  [key: string]: unknown;
}

export interface MemoryItem {
  id?: string;
  kind?: string;
  state?: string;
  content?: string;
  summary?: string;
  task?: string;
  session_id?: string;
  provenance?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface MemorySearchResult {
  items: MemoryItem[];
  kinds?: string[];
  trace_id?: string | null;
  context?: string;
}

export interface JobSummary {
  id: string;
  status?: string;
  objective?: string;
  can_cancel?: boolean;
  attachable?: boolean;
  session_id?: string | null;
  run_id?: string | null;
  [key: string]: unknown;
}

export interface JobsCounts {
  queued?: number;
  running?: number;
  done?: number;
  [key: string]: number | undefined;
}

export interface BonAttempt {
  index: number;
  label?: string;
  running?: boolean;
  ok?: boolean;
  status?: string;
  error?: string;
  score?: number;
  worktree?: string;
  branch?: string;
  artifacts?: string[];
  message?: string;
  [key: string]: unknown;
}

export interface BonLiveState {
  n: number;
  attempts: BonAttempt[];
  placeholders: Record<number, BonAttempt>;
  winner_index: number | null;
  objective?: string;
}

export interface ReviewFinding {
  severity?: string;
  summary?: string;
  [key: string]: unknown;
}

export interface ReviewResult {
  ok?: boolean;
  findings?: ReviewFinding[];
  diff_stat?: string;
  message?: string;
  [key: string]: unknown;
}

export interface SlashCommand {
  id: string;
  label: string;
  description: string;
  kind: SlashCommandKind;
  title?: string;
}

export interface SlashCatalogResponse {
  ok?: boolean;
  commands?: SlashCommand[];
  capabilities?: Partial<SlashCatalogCapabilities>;
}

export interface SlashCatalogCapabilities {
  comet: boolean;
  browser: boolean;
  computer: boolean;
  models: boolean;
  permissions: boolean;
  cmd: boolean;
  memory: boolean;
  labs: boolean;
  artifacts: boolean;
}

export interface WebUiCapabilities {
  projectFiles: boolean | null;
  /** null = probe pending / unknown — treat as available for slash filtering */
  cometApi: boolean | null;
  browserApi: boolean | null;
  computerApi: boolean | null;
  modelApi: boolean | null;
  slashCatalogApi: boolean;
}

export interface MetaPayload {
  brand?: string;
  memory_kinds?: string[];
  memory_states?: string[];
  memory_scopes?: string[];
  media_exts?: string[];
  project_root?: string;
  features?: Record<string, boolean>;
  project?: Record<string, unknown> | null;
  hitl?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ToastMessage {
  id: string;
  text: string;
  createdAt: number;
}

export interface ChatStreamBody {
  thread_id: string;
  session_id: string;
  message: string;
  attachments?: string[];
  auto_approve?: boolean;
  auto_build?: boolean;
  agent_mode?: AgentMode;
  loop_mode?: string;
  max_steps?: number;
  model?: string;
}

export interface StreamHandlers {
  onStatus?: (label: string, data: Record<string, unknown>) => void;
  onDelta?: (text: string) => void;
  onMessage?: (text: string, partial?: boolean) => void;
  onEvent?: (data: Record<string, unknown>) => void;
  onToolCard?: (data: Record<string, unknown>) => void;
  onComputerFrame?: (data: Record<string, unknown>, event: string) => void;
  onDone?: (data: Record<string, unknown>) => void;
  onError?: (error: string) => void;
}

export interface NewSessionOptions {
  /** Open as a parallel multitask tab (park previous). */
  parallel?: boolean;
  /** Carry composer File attachments onto the new session. */
  keepPending?: boolean;
}
