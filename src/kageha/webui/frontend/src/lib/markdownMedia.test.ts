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

  it("turns artifact code spans into canvas links", () => {
    const html = "<p>See <code>artifacts/market_research.md</code></p>";
    const out = rewriteMarkdownMediaHtml(html, "e3ad0a53425b");
    expect(out).toContain('class="artifact-path"');
    expect(out).toContain('data-artifact="artifacts/market_research.md"');
    expect(out).toContain(
      "/api/sessions/e3ad0a53425b/files/artifacts/market_research.md",
    );
  });

  it("extracts backtick artifact paths", () => {
    expect(
      extractArtifactPaths(
        "Research at `artifacts/market_research.md` and `artifacts/nano_banana_edit.png`",
      ),
    ).toEqual([
      "artifacts/market_research.md",
      "artifacts/nano_banana_edit.png",
    ]);
  });
});
