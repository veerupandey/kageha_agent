import { api } from "../api/client";
import type { AgentMode, SlashCommand } from "../api/types";
import { useAppStore } from "../store";

export interface TokenContext {
  start: number;
  end: number;
  query: string;
  token?: string;
}

const AGENT_MODES: AgentMode[] = ["normal", "plan", "spec", "goal"];

const SLASH_PRIMARY_IDS = [
  "plan",
  "spec",
  "goal",
  "normal",
  "multitask",
  "tabs",
  "ask",
  "auto",
  "model",
  "labs",
  "best-of-n",
  "review",
  "memory",
  "artifacts",
  "browser",
  "research",
  "comet",
  "computer",
];

const SLASH_KIND_ORDER = [
  "mode",
  "multitask",
  "prefs",
  "labs",
  "browser",
  "computer",
  "project",
  "skill",
];

const SLASH_ID_ORDER = Object.fromEntries(
  SLASH_PRIMARY_IDS.map((id, i) => [id, i]),
);

export type SlashApplyResult = "attach" | "focus-model" | void;

function slashCommandTitle(cmd: SlashCommand): string {
  if (cmd.title) return String(cmd.title);
  const id = String(cmd.id || "");
  if (cmd.kind === "mode" && id) {
    return id[0].toUpperCase() + id.slice(1);
  }
  if (id === "multitask" || id === "new" || id === "task") return "Multitask";
  if (id === "tabs") return "Tabs";
  const label = String(cmd.label || "")
    .replace(/^\//, "")
    .trim();
  if (!label) return id;
  return label
    .split(/[\s./-]+/)
    .filter(Boolean)
    .map((p) => p[0].toUpperCase() + p.slice(1))
    .join(" ");
}

function slashCommandRank(cmd: SlashCommand): number {
  const id = String(cmd?.id || "");
  if (Object.prototype.hasOwnProperty.call(SLASH_ID_ORDER, id)) {
    return SLASH_ID_ORDER[id];
  }
  const ka = SLASH_KIND_ORDER.indexOf(String(cmd?.kind || ""));
  return 1000 + (ka < 0 ? 99 : ka);
}

/** `/token` at caret — returns start/end/query or null. */
export function getSlashContext(
  text: string,
  caret: number,
): TokenContext | null {
  const value = String(text || "");
  const pos = Math.max(0, Math.min(Number(caret) || 0, value.length));
  const before = value.slice(0, pos);
  const m = before.match(/(^|[\s\n])(\/[a-z0-9./-]*)$/i);
  if (!m) return null;
  const token = m[2];
  return {
    start: before.length - token.length,
    end: pos,
    token,
    query: token.slice(1).toLowerCase(),
  };
}

/** `@token` at caret — returns start/end/query or null. */
export function getAtContext(
  text: string,
  caret: number,
): TokenContext | null {
  const value = String(text || "");
  const pos = Math.max(0, Math.min(Number(caret) || 0, value.length));
  const before = value.slice(0, pos);
  const m = before.match(/(^|[\s\n])(@[^\s]*)$/);
  if (!m) return null;
  const token = m[2];
  return {
    start: before.length - token.length,
    end: pos,
    token,
    query: token.slice(1),
  };
}

/** Filter slash catalog by typed query (id, label, title, description). */
export function filterSlashCommands(
  cmds: SlashCommand[],
  query: string,
): SlashCommand[] {
  const list = Array.isArray(cmds) ? cmds : [];
  const q = String(query || "")
    .toLowerCase()
    .trim();
  let matched: SlashCommand[];
  if (!q) {
    const primary = new Set(SLASH_PRIMARY_IDS);
    matched = list.filter((c) => primary.has(String(c.id || "")));
  } else {
    matched = list.filter((c) => {
      const id = String(c.id || "").toLowerCase();
      const label = String(c.label || "")
        .replace(/^\//, "")
        .toLowerCase();
      const title = slashCommandTitle(c).toLowerCase();
      if (id.startsWith(q) || label.startsWith(q) || title.startsWith(q)) {
        return true;
      }
      if (title.includes(q) || label.includes(q)) return true;
      return String(c.description || "")
        .toLowerCase()
        .includes(q);
    });
  }
  return matched.slice().sort((a, b) => {
    const ra = slashCommandRank(a);
    const rb = slashCommandRank(b);
    if (ra !== rb) return ra - rb;
    return String(a.label || "").localeCompare(String(b.label || ""));
  });
}

function replaceTokenInDraft(start: number, end: number, insert: string) {
  const store = useAppStore.getState();
  const draft = store.draft;
  const head = draft.slice(0, start);
  const after = draft.slice(end);
  store.setDraft(head + insert + after);
}

function toastApiResult(
  store: ReturnType<typeof useAppStore.getState>,
  data: { message?: string; status?: string },
  fallback: string,
) {
  const status = data.status != null ? String(data.status) : "";
  const message = data.message != null ? String(data.message) : "";
  let text = message || status || fallback;
  if (status && message && !message.toLowerCase().includes(status.toLowerCase())) {
    text = `${status} · ${message}`;
  }
  if (text.length > 180) text = `${text.slice(0, 177)}…`;
  store.showToast(text);
}

async function postBrowser(command: string) {
  const store = useAppStore.getState();
  const cmd = String(command || "/browser").trim();
  // Don't auto-send (avoids minting a session + recurse via sendMessage).
  if (store.capabilities.browserApi === false) {
    store.setDraft(cmd);
    store.showToast("Browser · API unavailable — press Send");
    return;
  }
  store.showToast(cmd.startsWith("/research") ? "Research…" : "Browser…");
  try {
    const data = await api<{ message?: string; status?: string; ok?: boolean }>(
      "/api/browser",
      {
        method: "POST",
        body: JSON.stringify({ command: cmd }),
      },
    );
    const full = String(data.message || data.status || "Done");
    if (store.sessionId) store.appendLocalMessage("assistant", full);
    toastApiResult(store, data, cmd.startsWith("/research") ? "Research" : "Browser");
  } catch (err) {
    const msg = err instanceof Error ? err.message : `Browser failed: ${err}`;
    if (store.sessionId) store.appendLocalMessage("assistant", msg);
    store.showToast(msg.slice(0, 180));
  }
}

async function postComputer(command: string) {
  const store = useAppStore.getState();
  const cmd = String(command || "/computer").trim();
  if (store.capabilities.computerApi === false) {
    store.setDraft(cmd);
    store.showToast("Computer · API unavailable — press Send");
    return;
  }
  store.showToast("Computer…");
  try {
    const data = await api<{ message?: string; status?: string; ok?: boolean }>(
      "/api/computer",
      {
        method: "POST",
        body: JSON.stringify({ command: cmd }),
      },
    );
    const full = String(data.message || data.status || "Done");
    // Only append into an existing chat — never mint a session for status/tips.
    if (store.sessionId) {
      store.appendLocalMessage("assistant", full);
    }
    toastApiResult(store, data, "Computer");
  } catch (err) {
    const msg = err instanceof Error ? err.message : `Computer failed: ${err}`;
    if (store.sessionId) store.appendLocalMessage("assistant", msg);
    store.showToast(msg.slice(0, 180));
  }
}

/** Handle native admin slash without creating a chat session. */
export function dispatchNativeAdminSlash(command: string): boolean {
  const cmd = String(command || "").trim();
  const low = cmd.toLowerCase();
  if (low === "/computer" || low.startsWith("/computer ")) {
    void postComputer(cmd);
    return true;
  }
  if (
    low === "/browser" ||
    low.startsWith("/browser ") ||
    low === "/comet" ||
    low.startsWith("/comet ")
  ) {
    const browserCmd = low.startsWith("/comet")
      ? `/browser ${cmd.replace(/^\//, "")}`
      : cmd;
    void postBrowser(browserCmd);
    return true;
  }
  return false;
}

type StoreSlice = ReturnType<typeof useAppStore.getState>;

/**
 * Dispatch a slash command against the zustand store.
 * Returns 'attach' | 'focus-model' for UI side-effects the caller should handle.
 */
export function applySlashCommand(
  cmd: SlashCommand,
  storeOrOpts?: StoreSlice | { start?: number; end?: number },
  maybeOpts?: { start?: number; end?: number },
): SlashApplyResult {
  if (!cmd) return;
  const looksLikeStore =
    storeOrOpts != null &&
    typeof storeOrOpts === "object" &&
    "setAgentMode" in storeOrOpts;
  const store = looksLikeStore
    ? (storeOrOpts as StoreSlice)
    : useAppStore.getState();
  const opts = looksLikeStore
    ? maybeOpts
    : (storeOrOpts as { start?: number; end?: number } | undefined);
  const start = opts?.start ?? 0;
  const end = opts?.end ?? store.draft.length;
  const id = cmd.id;
  const clearToken = () => {
    const draft = store.draft;
    const head = draft.slice(0, start);
    const after = draft.slice(end);
    store.setDraft(head + after);
  };

  if (cmd.kind === "mode" && AGENT_MODES.includes(id as AgentMode)) {
    clearToken();
    store.setAgentMode(id as AgentMode);
    if (id === "normal") store.showToast("Normal mode");
    else store.showToast(`${id[0].toUpperCase()}${id.slice(1)} mode`);
    return;
  }

  if (id === "new" || id === "task" || id === "multitask") {
    clearToken();
    store.clearComposerChip({ resetMode: false });
    if (store.sessionId) {
      void store
        .newSession({ parallel: true })
        .then(() => store.showToast("Multitask · parallel tab opened"))
        .catch((err: Error) =>
          store.showToast(err.message || String(err)),
        );
    } else {
      store.setComposerChip("multitask", "multitask");
      store.showToast("Multitask · send your first task, then + or /multitask");
    }
    return;
  }

  if (id === "tabs") {
    clearToken();
    const n = store.tabs.length;
    store.showToast(
      n
        ? `Parallel tasks · ${n} open · + for another`
        : "No task tabs yet · /new to start one",
    );
    return;
  }

  if (id === "ask" || id === "permissions-ask") {
    clearToken();
    store.setAskMode(true);
    store.showToast("Ask mode · confirm risky tools");
    return;
  }

  if (id === "auto" || id === "permissions-auto") {
    clearToken();
    store.setAskMode(false);
    store.showToast("Auto mode · risky tools auto-approved");
    return;
  }

  if (id === "permissions") {
    clearToken();
    store.showToast(
      store.autoApprove
        ? "Permissions · auto (use /ask or /permissions ask)"
        : "Permissions · ask (use /auto or /permissions auto)",
    );
    return;
  }

  if (id === "labs") {
    clearToken();
    store.openDrawer("labs");
    return;
  }

  if (id === "best-of-n") {
    clearToken();
    store.setWorkbenchTab("bon");
    store.openDrawer("workbench");
    store.showToast("Workbench · Best-of-N");
    return;
  }

  if (id === "review") {
    clearToken();
    store.setWorkbenchTab("review");
    store.openDrawer("workbench");
    store.showToast("Workbench · Review diff");
    return;
  }

  if (id === "memory") {
    clearToken();
    store.openDrawer("memory");
    return;
  }

  if (id === "artifacts") {
    clearToken();
    store.openDrawer("artifacts");
    return;
  }

  if (id === "attach" || id === "files") {
    clearToken();
    store.showToast("Choose files · or drop / paste into the composer");
    return "attach";
  }

  if (id === "model") {
    clearToken();
    store.showToast(
      store.capabilities.modelApi !== false
        ? "Model override · API available"
        : "Model override",
    );
    return "focus-model";
  }

  if (id === "comet") {
    clearToken();
    void postBrowser("/browser comet start");
    return;
  }

  if (cmd.kind === "browser" || id === "browser" || id.startsWith("browser-") || id.startsWith("research")) {
    clearToken();
    const label = String(cmd.label || "").trim();
    if (label === "/research" || label.startsWith("/research")) {
      const parts = label.split(/\s+/).filter(Boolean);
      if (
        parts.length === 1 ||
        (parts.length === 2 &&
          ["flash", "standard", "deep"].includes(parts[1].toLowerCase()))
      ) {
        const prefix =
          parts.length === 1 ? "/research flash " : `${label} `;
        store.setDraft(prefix + store.draft.replace(/^\s*/, ""));
        store.showToast("Research · type your query, then send");
        return;
      }
    }
    void postBrowser(label || "/browser");
    return;
  }

  // Admin pack commands only (kind: computer). Primary /computer is kind: skill.
  if (cmd.kind === "computer" || id.startsWith("computer-")) {
    clearToken();
    const label = String(cmd.label || "/computer").trim();
    void postComputer(label);
    return;
  }

  if (cmd.kind === "project" && cmd.label) {
    replaceTokenInDraft(start, end, `${cmd.label} `);
    store.showToast("Project recipe · send to expand");
    return;
  }

  if (cmd.kind === "skill" && cmd.label) {
    replaceTokenInDraft(start, end, `${cmd.label} `);
    const skillName = cmd.title || cmd.id.replace(/^skill-/, "");
    store.showToast(
      id === "computer" || skillName === "computer_use"
        ? "Computer-use · type a task after /computer, then send"
        : `Skill · ${skillName} (explicit)`,
    );
    return;
  }

  // Fallback: insert the slash label into the composer.
  if (cmd.label) {
    replaceTokenInDraft(start, end, `${cmd.label} `);
  }
}

export { slashCommandTitle };
