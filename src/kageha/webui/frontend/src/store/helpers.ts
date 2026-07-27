import type {
  AgentMode,
  ChatMessage,
  ComputerFrame,
  DesignPanelState,
  RunStatus,
  SessionRun,
  TodoBoard,
  ToolCard,
  WorkbenchTab,
} from "../api/types";
import type { AppState } from "./types";

export const LAST_SESSION_KEY = "kageha.lastSessionId";
export const WORKBENCH_TAB_KEY = "kageha.workbenchTab";
export const MODE_SLASH_RE = /^\/(plan|spec|goal|normal)\b/i;
export const AGENT_MODES: AgentMode[] = ["normal", "plan", "spec", "goal"];

export function uid(prefix = "m"): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

export function rememberSession(id: string | null) {
  try {
    if (id) localStorage.setItem(LAST_SESSION_KEY, id);
    else localStorage.removeItem(LAST_SESSION_KEY);
  } catch {
    /* ignore */
  }
}

export function loadLastSession(): string | null {
  try {
    return localStorage.getItem(LAST_SESSION_KEY);
  } catch {
    return null;
  }
}

export function loadWorkbenchTab(): WorkbenchTab {
  try {
    const v = localStorage.getItem(WORKBENCH_TAB_KEY);
    return v === "review" ? "review" : "bon";
  } catch {
    return "bon";
  }
}

export async function apiSoft(
  path: string,
  options: RequestInit = {},
): Promise<{ ok: boolean; status: number; data: Record<string, unknown> }> {
  try {
    const headers = new Headers(options.headers || {});
    const isForm =
      typeof FormData !== "undefined" && options.body instanceof FormData;
    if (!isForm && options.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    const res = await fetch(path, { ...options, headers });
    const data = (await res.json().catch(() => ({}))) as Record<
      string,
      unknown
    >;
    return { ok: res.ok, status: res.status, data };
  } catch {
    return { ok: false, status: 0, data: {} };
  }
}

export function emptyRun(
  sessionId: string,
  threadId: string | null = null,
): SessionRun {
  return {
    sessionId,
    threadId,
    messages: [],
    sending: false,
    status: "idle",
    statusLabel: "Ready",
    queue: [],
    abort: null,
    waitingApproval: false,
    needsAttention: false,
    pendingFiles: [],
  };
}

export function emptyDesign(): DesignPanelState {
  return {
    files: {},
    activeFile: "plan.md",
    agentMode: "plan",
    phases: [],
    awaitingClarify: false,
    awaitingBuild: false,
    dirty: false,
    saving: false,
    exploreStatus: null,
    exploreDegraded: false,
  };
}

export function normalizeTodoBoard(payload: unknown): TodoBoard | null {
  if (!payload || typeof payload !== "object") return null;
  const p = payload as Record<string, unknown>;
  const items = Array.isArray(p.items) ? p.items : [];
  const total = Number(p.total);
  if ((!Number.isFinite(total) || total <= 0) && !items.length) return null;
  const normalizedItems: TodoBoard["items"] = items
    .filter(
      (it): it is Record<string, unknown> =>
        Boolean(it) && typeof it === "object",
    )
    .slice(0, 24)
    .map((it) => ({
      id: String(it.id || ""),
      text: String(it.text || ""),
      done: Boolean(it.done),
    }));
  const done = Number.isFinite(Number(p.done))
    ? Number(p.done)
    : normalizedItems.filter((it) => it.done).length;
  return {
    label: String(p.label || "todos"),
    done,
    total: Number.isFinite(total) && total > 0 ? total : normalizedItems.length,
    items: normalizedItems,
  };
}

function pickFirst(...vals: unknown[]): unknown {
  for (const v of vals) {
    if (v == null) continue;
    if (typeof v === "string" && !v.trim()) continue;
    return v;
  }
  return undefined;
}

export function normalizeToolCard(
  raw: Record<string, unknown>,
  kind?: string,
): ToolCard | null {
  const nested =
    raw.tool_card && typeof raw.tool_card === "object"
      ? (raw.tool_card as Record<string, unknown>)
      : raw;
  const name = pickFirst(
    nested.name,
    nested.tool,
    nested.tool_name,
    nested.toolName,
    raw.name,
    raw.tool,
    raw.tool_name,
  );
  if (!name) return null;
  const durationMs = Number(
    pickFirst(
      nested.duration_ms,
      nested.durationMs,
      nested.elapsed_ms,
      nested.ms,
      raw.duration_ms,
    ),
  );
  const artifactRefs = pickFirst(
    nested.artifact_refs,
    nested.artifactRefs,
    nested.artifacts,
    nested.paths,
    raw.artifact_refs,
  );
  const refs = Array.isArray(artifactRefs)
    ? artifactRefs
        .map((a) =>
          typeof a === "string"
            ? a
            : String(
                (a as { path?: string; url?: string })?.path ||
                  (a as { url?: string })?.url ||
                  "",
              ),
        )
        .filter(Boolean)
    : [];
  const status = String(
    pickFirst(
      nested.status,
      nested.state,
      raw.status,
      kind === "tool_completed"
        ? "ok"
        : kind === "tool_started"
          ? "running"
          : "",
    ) || "running",
  ).toLowerCase();
  const resultPreview = pickFirst(
    nested.result_preview,
    nested.resultPreview,
    nested.result,
    nested.output_preview,
    raw.result_preview,
  );
  const argsPreview = pickFirst(
    nested.args_preview,
    nested.argsPreview,
    nested.args,
    nested.arguments_preview,
    nested.preview,
    raw.args_preview,
  );
  return {
    id: String(
      pickFirst(nested.id, nested.card_id, nested.attempt_id, raw.id, name) ||
        name,
    ),
    name: String(name),
    argsPreview: argsPreview != null ? String(argsPreview).slice(0, 400) : "",
    status,
    durationMs: Number.isFinite(durationMs) ? durationMs : null,
    artifactRefs: refs.slice(0, 8),
    resultPreview:
      resultPreview != null ? String(resultPreview).slice(0, 400) : "",
  };
}

export function normalizeComputerFrame(
  raw: Record<string, unknown>,
  sessionId: string | null,
): ComputerFrame | null {
  const nested =
    raw.computer_frame && typeof raw.computer_frame === "object"
      ? (raw.computer_frame as Record<string, unknown>)
      : raw.frame && typeof raw.frame === "object"
        ? (raw.frame as Record<string, unknown>)
        : raw.thumb && typeof raw.thumb === "object"
          ? (raw.thumb as Record<string, unknown>)
          : raw;
  const path = pickFirst(
    nested.thumb_path,
    nested.thumbPath,
    nested.thumb,
    nested.path,
    nested.artifact,
    nested.artifact_path,
    nested.artifactPath,
    nested.rel_path,
    nested.file,
    raw.thumb_path,
    raw.path,
  );
  const url = pickFirst(
    nested.thumb_url,
    nested.thumbUrl,
    nested.url,
    nested.src,
    nested.thumbnail_url,
    nested.image_url,
    raw.thumb_url,
    raw.url,
  );
  const b64 = pickFirst(
    nested.thumb_b64,
    nested.b64,
    nested.base64,
    nested.thumbnail_b64,
    raw.thumb_b64,
  );
  const sid =
    sessionId ||
    String(
      pickFirst(
        nested.session_id,
        nested.sessionId,
        raw.session_id,
        raw.sessionId,
      ) || "",
    ) ||
    null;
  let resolvedUrl = url ? String(url) : "";
  if (!resolvedUrl && path) {
    const p = String(path);
    if (/^https?:\/\//i.test(p) || p.startsWith("/api/")) resolvedUrl = p;
    else if (sid) {
      resolvedUrl = `/api/sessions/${encodeURIComponent(sid)}/files/${encodeURIComponent(p)}`;
    } else {
      resolvedUrl = p;
    }
  }
  if (!resolvedUrl && b64) {
    const mime = String(
      pickFirst(nested.mime, nested.content_type, "image/png"),
    );
    resolvedUrl = `data:${mime};base64,${String(b64).replace(/^data:[^;]+;base64,/, "")}`;
  }
  if (!resolvedUrl) return null;
  const action = pickFirst(
    nested.action,
    nested.label,
    nested.tool,
    raw.action,
    raw.label,
  );
  const app = pickFirst(nested.app, nested.application, nested.window, raw.app);
  return {
    url: resolvedUrl,
    path: path ? String(path) : "",
    action: action ? String(action) : "",
    app: app ? String(app) : "",
    caption: [app, action].filter(Boolean).join(" · ") || undefined,
  };
}

export function parseModeSlash(text: string): AgentMode | null {
  const m = MODE_SLASH_RE.exec(String(text || "").trim());
  if (!m) return null;
  const mode = m[1].toLowerCase() as AgentMode;
  return AGENT_MODES.includes(mode) ? mode : null;
}

export function isModeOnlyComposerText(text: string): boolean {
  const t = String(text || "").trim();
  return Boolean(
    parseModeSlash(t) && t.replace(MODE_SLASH_RE, "").trim() === "",
  );
}

const COMPUTER_ADMIN_ACTIONS = new Set([
  "status",
  "doctor",
  "pack",
  "allowlist",
  "apps",
  "list",
  "allow",
  "deny",
  "clear",
  "help",
  "-h",
  "--help",
  "?",
]);

/** Bare `/computer` or pack admin verbs — not a computer_use skill task. */
export function isComputerAdminSlash(text: string): boolean {
  const t = String(text || "").trim();
  const low = t.toLowerCase();
  if (low !== "/computer" && !low.startsWith("/computer ")) return false;
  const parts = t.split(/\s+/).filter(Boolean);
  if (parts.length <= 1) return true;
  return COMPUTER_ADMIN_ACTIONS.has(parts[1].toLowerCase());
}

/**
 * Slash that should never mint a chat session / agent turn by itself:
 * mode-only, bare skill prefixes, computer/browser admin.
 */
export function isMetaOnlySlash(text: string): boolean {
  const t = String(text || "").trim();
  if (!t.startsWith("/")) return false;
  if (isModeOnlyComposerText(t)) return true;
  if (isComputerAdminSlash(t)) return true;
  const low = t.toLowerCase();
  if (
    low === "/browser" ||
    low.startsWith("/browser ") ||
    low === "/comet" ||
    low.startsWith("/comet ")
  ) {
    return true;
  }
  // Bare skill invoke with no task body: `/computer_use` or `/web_research`
  if (/^\/[a-z][a-z0-9_-]*$/i.test(t) && !isModeOnlyComposerText(t)) {
    const id = t.slice(1).toLowerCase();
    if (
      id === "multitask" ||
      id === "new" ||
      id === "task" ||
      id === "tabs" ||
      id === "ask" ||
      id === "auto" ||
      id === "model" ||
      id === "labs" ||
      id === "memory" ||
      id === "artifacts" ||
      id === "review" ||
      id === "permissions"
    ) {
      return false;
    }
    return true;
  }
  return false;
}

export function mapHistoryMessages(
  rows: Array<{ role?: string; text?: string }> | undefined,
): ChatMessage[] {
  return (rows || []).map((m) => ({
    id: uid(),
    role: m.role === "assistant" || m.role === "system" ? m.role : "user",
    text: m.text || "",
  }));
}

export function syncFromRun(
  run: SessionRun | undefined,
  extras: Partial<AppState> = {},
): Partial<AppState> {
  if (!run) {
    return {
      messages: [],
      sending: false,
      runStatus: "idle",
      statusLabel: "Ready",
      abort: null,
      pendingFiles: [],
      threadId: null,
      ...extras,
    };
  }
  return {
    messages: run.messages,
    sending: run.sending,
    runStatus: run.waitingApproval ? "waiting_approval" : run.status,
    statusLabel: run.statusLabel,
    abort: run.abort,
    pendingFiles: run.pendingFiles,
    threadId: run.threadId,
    ...extras,
  };
}

export type { RunStatus };
