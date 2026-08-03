/** Helpers for session artifact preview / media canvas. */

export type CanvasKind =
  | "image"
  | "video"
  | "audio"
  | "pdf"
  | "markdown"
  | "text"
  | "code"
  | "presentation"
  | "document"
  | "spreadsheet"
  | "download";

export interface CanvasItem {
  path: string;
  url: string;
  kind: CanvasKind;
  caption: string;
  size?: number;
  text?: string;
}

const IMAGE_EXT = new Set([
  ".png",
  ".jpg",
  ".jpeg",
  ".webp",
  ".gif",
  ".bmp",
  ".svg",
]);
const VIDEO_EXT = new Set([".mp4", ".webm", ".mov", ".m4v"]);
const AUDIO_EXT = new Set([".wav", ".mp3", ".m4a", ".ogg", ".aac", ".flac"]);
const MARKDOWN_EXT = new Set([".md", ".markdown"]);
const CODE_EXT = new Set([
  ".py",
  ".js",
  ".ts",
  ".tsx",
  ".jsx",
  ".rs",
  ".go",
  ".java",
  ".rb",
  ".swift",
  ".kt",
  ".c",
  ".cpp",
  ".h",
  ".hpp",
  ".cs",
  ".php",
  ".lua",
  ".r",
  ".jl",
  ".scala",
  ".sh",
  ".bash",
  ".zsh",
  ".fish",
  ".sql",
  ".graphql",
  ".gql",
  ".proto",
  ".zig",
  ".nim",
  ".ex",
  ".exs",
  ".erl",
  ".hrl",
  ".clj",
  ".cljs",
  ".hs",
  ".ml",
  ".mli",
  ".v",
  ".sv",
  ".vhdl",
]);
const TEXT_EXT = new Set([
  ".txt",
  ".json",
  ".csv",
  ".log",
  ".yml",
  ".yaml",
  ".toml",
  ".xml",
  ".html",
  ".htm",
  ".css",
  ".env",
  ".ini",
  ".cfg",
  ".conf",
]);
const PRESENTATION_EXT = new Set([".ppt", ".pptx", ".key"]);
const DOCUMENT_EXT = new Set([".doc", ".docx", ".rtf", ".odt"]);
const SPREADSHEET_EXT = new Set([".xls", ".xlsx", ".csv"]); // csv also text

export function fileExt(path: string): string {
  const base = path.split(/[?#]/)[0] || path;
  const name = base.split("/").pop() || base;
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
}

export function fileBasename(path: string): string {
  const base = path.split(/[?#]/)[0] || path;
  return base.split("/").pop() || base;
}

export function canvasKindForPath(path: string, kindHint?: string): CanvasKind {
  const ext = fileExt(path);
  const hint = (kindHint || "").toLowerCase();
  if (IMAGE_EXT.has(ext) || hint === "image") return "image";
  if (VIDEO_EXT.has(ext) || hint === "video") return "video";
  if (AUDIO_EXT.has(ext) || hint === "audio") return "audio";
  if (ext === ".pdf" || hint === "pdf") return "pdf";
  if (MARKDOWN_EXT.has(ext) || hint === "markdown") return "markdown";
  if (PRESENTATION_EXT.has(ext) || hint === "presentation") return "presentation";
  if (DOCUMENT_EXT.has(ext) || hint === "document") return "document";
  if (SPREADSHEET_EXT.has(ext) || hint === "spreadsheet") {
    // Prefer editable text preview for CSV.
    if (ext === ".csv") return "text";
    return "spreadsheet";
  }
  if (CODE_EXT.has(ext) || hint === "code") return "code";
  if (TEXT_EXT.has(ext) || hint === "text") return "text";
  return "download";
}

export function isPreviewableKind(kind: CanvasKind): boolean {
  return (
    kind === "image" ||
    kind === "video" ||
    kind === "audio" ||
    kind === "pdf" ||
    kind === "markdown" ||
    kind === "text" ||
    kind === "code"
  );
}

export function kindLabel(kind: CanvasKind): string {
  switch (kind) {
    case "image":
      return "Image";
    case "video":
      return "Video";
    case "audio":
      return "Audio";
    case "pdf":
      return "PDF";
    case "markdown":
      return "Markdown";
    case "text":
      return "Text";
    case "code":
      return "Code";
    case "presentation":
      return "Slides";
    case "document":
      return "Document";
    case "spreadsheet":
      return "Spreadsheet";
    default:
      return "File";
  }
}

/** Agent / computer noise that should not appear as user deliverables. */
export function isArtifactNoise(path: string): boolean {
  const p = String(path || "").replace(/\\/g, "/");
  if (!p) return true;
  const name = fileBasename(p);
  if (!name || name === ".DS_Store") return true;
  if (/(?:^|\/)artifacts\/computer(?:\/|$)/i.test(p)) return true;
  if (/(?:^|\/)(?:__pycache__|\.git|node_modules|\.venv)(?:\/|$)/i.test(p)) {
    return true;
  }
  if (/^(?:SKILL|AGENTS|CLAUDE|TODO|todo)\.md$/i.test(name)) return true;
  if (
    /^(?:screen|state|thumb|screenshot|screen_thumb)(?:[_-]|\.)/i.test(name) &&
    IMAGE_EXT.has(fileExt(p))
  ) {
    // Bare capture names outside a deliverable folder are almost always noise.
    if (!/^(artifacts|outputs|slides|carousel|diagrams|research)\//i.test(p)) {
      return true;
    }
    if (/\/(?:computer|thumbs|screenshots?)\//i.test(p)) return true;
  }
  return false;
}

/**
 * User-facing deliverables for Canvas / chat strip.
 * Keeps media, docs, and code files in artifact folders.
 */
export function isShowcaseArtifact(path: string): boolean {
  const p = String(path || "").replace(/\\/g, "/").replace(/^\/+/, "");
  if (!p || isArtifactNoise(p)) return false;
  const kind = canvasKindForPath(p);
  if (
    kind === "image" ||
    kind === "video" ||
    kind === "audio" ||
    kind === "pdf" ||
    kind === "presentation" ||
    kind === "document" ||
    kind === "spreadsheet"
  ) {
    return true;
  }
  // Code files in deliverable folders are showcased.
  if (
    kind === "code" &&
    /^(artifacts|outputs|src|scripts)\//i.test(p)
  ) {
    return true;
  }
  // Markdown only when it lives in a deliverable folder and isn't a skill file.
  if (
    kind === "markdown" &&
    /^(artifacts|outputs|slides|research|carousel|diagrams)\//i.test(p)
  ) {
    return true;
  }
  // Text/config in artifacts folder.
  if (
    kind === "text" &&
    /^(artifacts|outputs)\//i.test(p)
  ) {
    return true;
  }
  return false;
}

/**
 * Chat strip + canvas from transcript mentions.
 * Includes markdown/docs/code deliverables (e.g. artifacts/icbc_reranker_time_decay.py).
 */
export function isChatMediaArtifact(path: string): boolean {
  if (!isShowcaseArtifact(path)) return false;
  const kind = canvasKindForPath(path);
  return (
    kind === "image" ||
    kind === "video" ||
    kind === "audio" ||
    kind === "pdf" ||
    kind === "presentation" ||
    kind === "markdown" ||
    kind === "document" ||
    kind === "spreadsheet" ||
    kind === "code" ||
    kind === "text"
  );
}

export function showcaseSortKey(path: string): [number, string] {
  const kind = canvasKindForPath(path);
  const rank =
    kind === "image"
      ? 0
      : kind === "video" || kind === "audio"
        ? 1
        : kind === "pdf" || kind === "presentation"
          ? 2
          : kind === "code"
            ? 3
            : kind === "markdown" || kind === "document" || kind === "spreadsheet"
              ? 4
              : 5;
  return [rank, path.toLowerCase()];
}

/** Same-origin download URL (forces save-as via Content-Disposition when possible). */
export function artifactDownloadUrl(
  sessionId: string | null | undefined,
  path: string,
): string | undefined {
  const url = artifactFileUrl(sessionId, path);
  if (!url) return undefined;
  return url.includes("?") ? `${url}&download=1` : `${url}?download=1`;
}

/** Build a session file URL with per-segment encoding (matches server). */
export function artifactFileUrl(
  sessionId: string | null | undefined,
  path: string,
  existingUrl?: string,
): string | undefined {
  if (existingUrl?.startsWith("/api/") || existingUrl?.startsWith("http")) {
    return existingUrl;
  }
  if (!sessionId || !path) return undefined;
  const rel = path.replace(/\\/g, "/").replace(/^\/+/, "");
  const encoded = rel
    .split("/")
    .filter(Boolean)
    .map((part) => encodeURIComponent(part))
    .join("/");
  return `/api/sessions/${encodeURIComponent(sessionId)}/files/${encoded}`;
}

export function formatBytes(n?: number): string {
  if (n == null || !Number.isFinite(n) || n < 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function toCanvasItem(
  sessionId: string | null | undefined,
  path: string,
  opts?: { kindHint?: string; url?: string; size?: number; name?: string },
): CanvasItem | null {
  const url = artifactFileUrl(sessionId, path, opts?.url);
  if (!url) return null;
  const kind = canvasKindForPath(path, opts?.kindHint);
  return {
    path,
    url,
    kind,
    caption: opts?.name || fileBasename(path),
    size: opts?.size,
  };
}
