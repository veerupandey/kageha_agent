import { describe, expect, it } from "vitest";
import {
  extractArtifactPaths,
  rewriteMarkdownMediaHtml,
} from "./markdownMedia";

describe("markdownMedia", () => {
  it("extracts artifact paths from markdown", () => {
    const text = `Here's the ad:\n\n![Ad](artifacts/nano_banana_edit.png)\n\nAlso see artifacts/deck.pptx`;
    expect(extractArtifactPaths(text)).toEqual([
      "artifacts/nano_banana_edit.png",
      "artifacts/deck.pptx",
    ]);
  });

  it("rewrites img src to session file API", () => {
    const html =
      '<p><img src="artifacts/nano_banana_edit.png" alt="Ad"></p>';
    const out = rewriteMarkdownMediaHtml(html, "e3ad0a53425b");
    expect(out).toContain(
      '/api/sessions/e3ad0a53425b/files/artifacts/nano_banana_edit.png',
    );
  });
});
