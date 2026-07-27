import { describe, expect, it } from "vitest";
import {
  applySessionFlagsLocally,
  filterSessionsForRail,
  sortSessionsPinnedFirst,
} from "./sessions";
import type { SessionSummary } from "../api/types";

function sid(
  id: string,
  opts: Partial<SessionSummary> = {},
): SessionSummary {
  return { session_id: id, title: opts.title ?? id, ...opts };
}

describe("sortSessionsPinnedFirst", () => {
  it("moves pinned sessions to the top without reordering peers", () => {
    const input = [
      sid("a"),
      sid("b", { pinned: true }),
      sid("c"),
      sid("d", { pinned: true }),
    ];
    expect(sortSessionsPinnedFirst(input).map((s) => s.session_id)).toEqual([
      "b",
      "d",
      "a",
      "c",
    ]);
  });
});

describe("filterSessionsForRail", () => {
  const rows = [
    sid("alpha", { title: "Alpha plan", pinned: true }),
    sid("beta", { title: "Beta draft", archived: true }),
    sid("gamma", { title: "Gamma work" }),
  ];

  it("hides archived by default and keeps pinned first", () => {
    const out = filterSessionsForRail(rows, { showArchived: false });
    expect(out.map((s) => s.session_id)).toEqual(["alpha", "gamma"]);
  });

  it("includes archived when requested", () => {
    const out = filterSessionsForRail(rows, { showArchived: true });
    expect(out.map((s) => s.session_id)).toEqual(["alpha", "beta", "gamma"]);
  });

  it("filters by title or id query", () => {
    const byTitle = filterSessionsForRail(rows, {
      showArchived: true,
      query: "draft",
    });
    expect(byTitle.map((s) => s.session_id)).toEqual(["beta"]);
    const byId = filterSessionsForRail(rows, {
      showArchived: true,
      query: "gam",
    });
    expect(byId.map((s) => s.session_id)).toEqual(["gamma"]);
  });
});

describe("applySessionFlagsLocally", () => {
  it("patches only the matching session", () => {
    const rows = [sid("a"), sid("b")];
    const next = applySessionFlagsLocally(rows, "b", {
      pinned: true,
      archived: true,
    });
    expect(next[0].pinned).toBeFalsy();
    expect(next[1]).toMatchObject({
      session_id: "b",
      pinned: true,
      archived: true,
    });
  });
});
