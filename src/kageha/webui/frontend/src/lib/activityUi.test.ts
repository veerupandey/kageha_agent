import { describe, expect, it } from "vitest";
import {
  activityGlyph,
  activityLineText,
  friendlyActivityLabel,
} from "./activityUi";

describe("activityUi", () => {
  it("maps labels to CLI-style glyphs", () => {
    expect(activityGlyph("Thinking…")).toBe("✦");
    expect(activityGlyph("Running bash")).toBe("▸");
    expect(activityGlyph("Waiting for approval")).toBe("⏸");
    expect(activityGlyph("Error")).toBe("!");
  });

  it("friendly-labels noisy telemetry", () => {
    expect(friendlyActivityLabel("Accepted")).toBe("Starting…");
    expect(friendlyActivityLabel("Planning…")).toBe("Planning…");
    expect(friendlyActivityLabel("action: bash")).toBe("Running bash…");
    expect(friendlyActivityLabel("tools: read_file, write_file")).toBe(
      "Running read_file…",
    );
  });

  it("keeps one short detail hint on a line", () => {
    expect(
      activityLineText({
        label: "Plan ready",
        detail: ["agent=normal · stage=act"],
      }),
    ).toContain("Planning…");
  });
});
