/** Rewrite session-relative media in markdown HTML and extract artifact paths. */

import { artifactFileUrl } from "./artifactMedia";

const REL_MEDIA =
  /(?:^|[\s("'`(])((?:artifacts|outputs|inputs|slides|diagrams|research|carousel)\/[A-Za-z0-9._\-\/]+)/g;

/** Collect session-relative artifact paths mentioned in assistant text. */
export function extractArtifactPaths(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  const src = String(text || "");
  // Markdown images / links
  for (const m of src.matchAll(/!\[[^\]]*]\(([^)\s]+)\)|\[[^\]]*]\(([^)\s]+)\)/g)) {
    const raw = (m[1] || m[2] || "").trim();
    const path = normalizeRelPath(raw);
    if (path && !seen.has(path)) {
      seen.add(path);
      out.push(path);
    }
  }
  // Bare paths
  for (const m of src.matchAll(REL_MEDIA)) {
    const path = normalizeRelPath(m[1] || "");
    if (path && !seen.has(path)) {
      seen.add(path);
      out.push(path);
    }
  }
  // Inline `artifacts/…` backticks (common in assistant replies)
  for (const m of src.matchAll(/`((?:artifacts|outputs|slides|research|carousel|diagrams)\/[^`]+)`/g)) {
    const path = normalizeRelPath(m[1] || "");
    if (path && !seen.has(path)) {
      seen.add(path);
      out.push(path);
    }
  }
  // Bare deliverable filenames in backticks (e.g. `report.html`) → artifacts/{name}
  for (const m of src.matchAll(/`([A-Za-z0-9][A-Za-z0-9._-]*\.(?:html?|pdf|pptx?|docx?|xlsx?|png|jpe?g|gif|webp|svg|mp4|webm|mov|wav|mp3|csv|zip|md))`/gi)) {
    const name = (m[1] || "").trim();
    if (!name) continue;
    const path = `artifacts/${name}`;
    if (!seen.has(path)) {
      seen.add(path);
      out.push(path);
    }
  }
  return out;
}

function normalizeRelPath(raw: string): string | null {
  let path = String(raw || "")
    .trim()
    .replace(/^<|>$/g, "")
    .replace(/^file:\/\//, "")
    .replace(/^\.\//, "")
    .replace(/^\/+/, "")
    .split(/[?#]/)[0];
  if (!path) return null;
  if (path.startsWith("http://") || path.startsWith("https://") || path.startsWith("data:")) {
    return null;
  }
  // Absolute home/session paths → keep trailing artifacts/… if present
  const idx = path.search(/(?:^|\/)(artifacts|outputs|inputs|slides)\//);
  if (idx >= 0) {
    path = path.slice(path[idx] === "/" ? idx + 1 : idx);
  }
  if (!/^(artifacts|outputs|inputs|slides|diagrams|research|carousel)\//.test(path)) {
    return null;
  }
  return path.replace(/\\/g, "/");
}

/** Point <img>/<a>/<source>/<video> at the session files API. */
export function rewriteMarkdownMediaHtml(
  html: string,
  sessionId: string | null | undefined,
): string {
  if (!sessionId || !html) return html;
  let out = html.replace(
    /\b(src|href)=["']([^"']+)["']/gi,
    (full, attr: string, src: string) => {
      if (
        src.startsWith("/api/") ||
        src.startsWith("http://") ||
        src.startsWith("https://") ||
        src.startsWith("data:") ||
        src.startsWith("#") ||
        src.startsWith("mailto:")
      ) {
        return full;
      }
      const path = normalizeRelPath(src);
      if (!path) return full;
      const url = artifactFileUrl(sessionId, path);
      if (!url) return full;
      return `${attr}="${url}"`;
    },
  );
  // Turn bare `artifacts/…` code spans into openable links.
  out = out.replace(
    /<code>((?:artifacts|outputs|slides|research|carousel|diagrams)\/[^<]+)<\/code>/gi,
    (_full, raw: string) => {
      const path = normalizeRelPath(raw);
      if (!path) return `<code>${raw}</code>`;
      const url = artifactFileUrl(sessionId, path);
      if (!url) return `<code>${raw}</code>`;
      return `<a href="${url}" class="artifact-path" data-artifact="${path}"><code>${raw}</code></a>`;
    },
  );
  // Turn bare deliverable filenames in code spans (e.g. `report.html`)
  // into clickable artifact links — maps to artifacts/{filename}.
  out = out.replace(
    /<code>([A-Za-z0-9][A-Za-z0-9._-]*\.(?:html?|pdf|pptx?|docx?|xlsx?|png|jpe?g|gif|webp|svg|mp4|webm|mov|wav|mp3|csv|zip|md|txt))<\/code>/gi,
    (_full, raw: string) => {
      // Skip if already wrapped in an <a> tag (handled above).
      const path = `artifacts/${raw.trim()}`;
      const url = artifactFileUrl(sessionId, path);
      if (!url) return `<code>${raw}</code>`;
      return `<a href="${url}" class="artifact-path" data-artifact="${path}"><code>${raw}</code></a>`;
    },
  );
  return out;
}
