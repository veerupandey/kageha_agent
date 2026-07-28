import { describe, expect, it } from "vitest";
import {
  artifactFileUrl,
  canvasKindForPath,
  fileBasename,
  isChatMediaArtifact,
  isPreviewableKind,
  isShowcaseArtifact,
  kindLabel,
  toCanvasItem,
} from "./artifactMedia";

describe("artifactMedia", () => {
  it("classifies known formats", () => {
    expect(canvasKindForPath("artifacts/shot.png")).toBe("image");
    expect(canvasKindForPath("artifacts/clip.mp4")).toBe("video");
    expect(canvasKindForPath("outputs/report.pdf")).toBe("pdf");
    expect(canvasKindForPath("plan.md")).toBe("markdown");
    expect(canvasKindForPath("deck.pptx")).toBe("presentation");
    expect(canvasKindForPath("notes.docx")).toBe("document");
    expect(canvasKindForPath("artifacts/product-hero.jpg")).toBe("image");
  });

  it("marks previewable kinds", () => {
    expect(isPreviewableKind("image")).toBe(true);
    expect(isPreviewableKind("pdf")).toBe(true);
    expect(isPreviewableKind("presentation")).toBe(false);
    expect(isPreviewableKind("download")).toBe(false);
  });

  it("filters noise vs showcase deliverables", () => {
    expect(isShowcaseArtifact("artifacts/ad.png")).toBe(true);
    expect(isChatMediaArtifact("artifacts/ad.png")).toBe(true);
    expect(isShowcaseArtifact("artifacts/computer/thumbs/screen_thumb.jpg")).toBe(
      false,
    );
    expect(isShowcaseArtifact("gen_carousel_bright.py")).toBe(false);
    expect(isShowcaseArtifact("SKILL.md")).toBe(false);
    expect(isShowcaseArtifact("artifacts/notes.md")).toBe(true);
    expect(isChatMediaArtifact("artifacts/notes.md")).toBe(false);
  });

  it("basenames paths", () => {
    expect(fileBasename("artifacts/kageha_ca_screenshot.png")).toBe(
      "kageha_ca_screenshot.png",
    );
  });

  it("builds per-segment encoded file urls", () => {
    expect(artifactFileUrl("abc", "artifacts/my file.png")).toBe(
      "/api/sessions/abc/files/artifacts/my%20file.png",
    );
  });

  it("builds canvas items", () => {
    const item = toCanvasItem("s1", "slides/deck.pptx", {
      kindHint: "presentation",
      size: 1200,
    });
    expect(item?.kind).toBe("presentation");
    expect(kindLabel(item!.kind)).toBe("Slides");
    expect(item?.url).toContain("/files/slides/deck.pptx");
  });
});
