import { describe, expect, it } from "vitest";
import { activityTarget, activityUrl } from "./activityUi";

describe("activity navigation", () => {
  it("routes browser and computer activity", () => {
    expect(activityTarget("browser_navigate Opening page…")).toBe("browser");
    expect(activityTarget("computer_click Clicking…")).toBe("computer");
    expect(activityTarget("Reading file…")).toBeNull();
  });

  it("extracts a page URL from tool arguments", () => {
    expect(activityUrl('{"url":"https://example.com/path"}')).toBe(
      "https://example.com/path",
    );
  });
});
