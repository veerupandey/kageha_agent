import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_PREFS,
  loadPrefs,
  PREFS_KEY,
  savePrefs,
} from "./prefs";
import type { UserPrefs } from "../api/types";

describe("prefs load/save", () => {
  const store = new Map<string, string>();

  beforeEach(() => {
    store.clear();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => {
        store.set(k, String(v));
      },
      removeItem: (k: string) => {
        store.delete(k);
      },
      clear: () => store.clear(),
      key: () => null,
      get length() {
        return store.size;
      },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns defaults when empty", () => {
    expect(loadPrefs()).toEqual(DEFAULT_PREFS);
  });

  it("roundtrips prefs through localStorage", () => {
    const prefs: UserPrefs = {
      density: "compact",
      defaultAskMode: true,
      defaultAgentMode: "plan",
      reduceMotion: true,
      showToolCards: false,
      theme: "dark",
    };
    savePrefs(prefs);
    expect(store.get(PREFS_KEY)).toBeTruthy();
    expect(loadPrefs()).toEqual(prefs);
  });

  it("migrates legacy ask mode when prefs missing", () => {
    store.set("kageha.askMode", "1");
    expect(loadPrefs().defaultAskMode).toBe(true);
  });
});
