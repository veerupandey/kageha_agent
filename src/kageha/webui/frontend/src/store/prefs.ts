import type { AgentMode, Density, UserPrefs } from "../api/types";

export const PREFS_KEY = "kageha.prefs";
/** Legacy ask-mode flag — migrated into prefs.defaultAskMode. */
export const ASK_MODE_KEY = "kageha.askMode";

export const DEFAULT_PREFS: UserPrefs = {
  density: "comfortable",
  defaultAskMode: false,
  defaultAgentMode: "normal",
  reduceMotion: false,
  showToolCards: true,
};

const AGENT_MODES: AgentMode[] = ["normal", "plan", "spec", "goal"];

function isDensity(v: unknown): v is Density {
  return v === "comfortable" || v === "compact";
}

function isAgentMode(v: unknown): v is AgentMode {
  return typeof v === "string" && AGENT_MODES.includes(v as AgentMode);
}

function readLegacyAskMode(): boolean | null {
  try {
    const raw = localStorage.getItem(ASK_MODE_KEY);
    if (raw === "1") return true;
    if (raw === "0") return false;
    return null;
  } catch {
    return null;
  }
}

/** Load prefs from localStorage (with legacy ask-mode migration). */
export function loadPrefs(): UserPrefs {
  const base = { ...DEFAULT_PREFS };
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<UserPrefs>;
      if (isDensity(parsed.density)) base.density = parsed.density;
      if (typeof parsed.defaultAskMode === "boolean") {
        base.defaultAskMode = parsed.defaultAskMode;
      }
      if (isAgentMode(parsed.defaultAgentMode)) {
        base.defaultAgentMode = parsed.defaultAgentMode;
      }
      if (typeof parsed.reduceMotion === "boolean") {
        base.reduceMotion = parsed.reduceMotion;
      }
      if (typeof parsed.showToolCards === "boolean") {
        base.showToolCards = parsed.showToolCards;
      }
    } else {
      const legacy = readLegacyAskMode();
      if (legacy != null) base.defaultAskMode = legacy;
    }
  } catch {
    const legacy = readLegacyAskMode();
    if (legacy != null) base.defaultAskMode = legacy;
  }
  return base;
}

/** Persist prefs and keep legacy ask-mode key in sync. */
export function savePrefs(prefs: UserPrefs): void {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    localStorage.setItem(ASK_MODE_KEY, prefs.defaultAskMode ? "1" : "0");
  } catch {
    /* ignore quota / private mode */
  }
}

/** Apply density / reduce-motion attributes on the shell. */
export function applyPrefsToDocument(prefs: UserPrefs): void {
  if (typeof document === "undefined") return;
  const root = document.getElementById("app") || document.documentElement;
  root.setAttribute("data-density", prefs.density);
  if (prefs.reduceMotion) {
    root.setAttribute("data-reduce-motion", "true");
  } else {
    root.removeAttribute("data-reduce-motion");
  }
  document.documentElement.setAttribute("data-density", prefs.density);
  if (prefs.reduceMotion) {
    document.documentElement.setAttribute("data-reduce-motion", "true");
  } else {
    document.documentElement.removeAttribute("data-reduce-motion");
  }
}

export function mergePrefs(
  current: UserPrefs,
  patch: Partial<UserPrefs>,
): UserPrefs {
  const next: UserPrefs = { ...current };
  if (isDensity(patch.density)) next.density = patch.density;
  if (typeof patch.defaultAskMode === "boolean") {
    next.defaultAskMode = patch.defaultAskMode;
  }
  if (isAgentMode(patch.defaultAgentMode)) {
    next.defaultAgentMode = patch.defaultAgentMode;
  }
  if (typeof patch.reduceMotion === "boolean") {
    next.reduceMotion = patch.reduceMotion;
  }
  if (typeof patch.showToolCards === "boolean") {
    next.showToolCards = patch.showToolCards;
  }
  return next;
}
