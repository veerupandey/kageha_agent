import { describe, expect, it } from "vitest";
import {
  filterSlashByCapabilities,
  mergeServerCatalog,
  SLASH_COMMANDS,
} from "./slashCatalog";
import type { SlashCommand, WebUiCapabilities } from "./types";

const caps = (
  partial: Partial<WebUiCapabilities> = {},
): WebUiCapabilities => ({
  projectFiles: null,
  cometApi: null,
  browserApi: null,
  computerApi: null,
  modelApi: null,
  slashCatalogApi: false,
  ...partial,
});

describe("mergeServerCatalog", () => {
  it("returns fallback when server list empty", () => {
    expect(mergeServerCatalog([])).toEqual(SLASH_COMMANDS);
    expect(mergeServerCatalog(null)).toEqual(SLASH_COMMANDS);
  });

  it("lets server commands override same id", () => {
    const server = [
      {
        id: "plan",
        label: "/plan",
        description: "Server plan",
        kind: "mode",
        title: "Plan+",
      },
    ];
    const merged = mergeServerCatalog(server, SLASH_COMMANDS);
    const plan = merged.find((c) => c.id === "plan");
    expect(plan?.description).toBe("Server plan");
    expect(plan?.title).toBe("Plan+");
    expect(merged.length).toBeGreaterThanOrEqual(SLASH_COMMANDS.length);
  });

  it("appends new server-only commands", () => {
    const server = [
      { id: "custom", label: "/custom", description: "Custom", kind: "prefs" },
    ];
    const merged = mergeServerCatalog(server, [
      {
        id: "ask",
        label: "/ask",
        description: "Ask",
        kind: "prefs",
      },
    ]);
    expect(merged.map((c) => c.id)).toEqual(["ask", "custom"]);
  });
});

describe("filterSlashByCapabilities", () => {
  const browserCmd: SlashCommand = {
    id: "browser",
    label: "/browser",
    description: "Browser",
    kind: "browser",
  };
  const computerCmd: SlashCommand = {
    id: "computer",
    label: "/computer",
    description: "Computer",
    kind: "computer",
  };
  const askCmd: SlashCommand = {
    id: "ask",
    label: "/ask",
    description: "Ask",
    kind: "prefs",
  };

  it("keeps browser when capability is null (unknown)", () => {
    const out = filterSlashByCapabilities(
      [browserCmd, askCmd],
      caps({ browserApi: null }),
    );
    expect(out.some((c) => c.id === "browser")).toBe(true);
  });

  it("hides browser when capability is explicitly false", () => {
    const out = filterSlashByCapabilities(
      [browserCmd, askCmd],
      caps({ browserApi: false }),
    );
    expect(out.some((c) => c.id === "browser")).toBe(false);
    expect(out.some((c) => c.id === "ask")).toBe(true);
  });

  it("hides computer when capability is false", () => {
    const out = filterSlashByCapabilities(
      [computerCmd, askCmd],
      caps({ computerApi: false }),
    );
    expect(out.some((c) => c.id === "computer")).toBe(false);
  });
});
