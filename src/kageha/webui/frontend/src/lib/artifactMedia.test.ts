import { describe, expect, it } from "vitest";
import {
  canvasKindForPath,
  fileBasename,
  isPreviewableKind,
} from "./artifactMedia";

describe("artifactMedia", () => {
  it("classifies known formats", () => {
    expect(canvasKindForPath("artifacts/shot.png")).toBe("image");
    expect(canvasKindForPath("artifacts/clip.mp4")).toBe("video");
    expect(canvasKindForPath("outputs/report.pdf")).toBe("pdf");
    expect(canvasKindForPath("plan.md")).toBe("markdown");
    expect(canvasKindForPath("deck.pptx")).toBe("download");
  });

  it("marks previewable kinds", () => {
    expect(isPreviewableKind("image")).toBe(true);
    expect(isPreviewableKind("pdf")).toBe(true);
    expect(isPreviewableKind("download")).toBe(false);
  });

  it("basenames paths", () => {
    expect(fileBasename("artifacts/kageha_ca_screenshot.png")).toBe(
      "kageha_ca_screenshot.png",
    );
  });
});
