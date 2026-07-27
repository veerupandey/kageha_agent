/** Helpers for session artifact preview / media canvas. */

export type CanvasKind =
  | "image"
  | "video"
  | "pdf"
  | "markdown"
  | "text"
  | "download";

export interface CanvasItem {
  url: string;
  kind: CanvasKind;
  caption: string;
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
const MARKDOWN_EXT = new Set([".md", ".markdown"]);
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
  ".js",
  ".ts",
  ".tsx",
  ".jsx",
  ".py",
  ".sh",
]);
const OFFICE_EXT = new Set([
  ".ppt",
  ".pptx",
  ".doc",
  ".docx",
  ".xls",
  ".xlsx",
  ".zip",
]);

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
  if (IMAGE_EXT.has(ext) || kindHint === "image") return "image";
  if (VIDEO_EXT.has(ext) || kindHint === "video") return "video";
  if (ext === ".pdf" || kindHint === "pdf") return "pdf";
  if (MARKDOWN_EXT.has(ext) || kindHint === "markdown") return "markdown";
  if (TEXT_EXT.has(ext) || kindHint === "text") return "text";
  if (OFFICE_EXT.has(ext) || kindHint === "presentation" || kindHint === "document") {
    return "download";
  }
  return "download";
}

export function isPreviewableKind(kind: CanvasKind): boolean {
  return kind === "image" || kind === "video" || kind === "pdf" || kind === "markdown" || kind === "text";
}

export function artifactFileUrl(
  sessionId: string | null | undefined,
  path: string,
  existingUrl?: string,
): string | undefined {
  if (existingUrl) return existingUrl;
  if (!sessionId || !path) return undefined;
  return `/api/sessions/${encodeURIComponent(sessionId)}/files/${encodeURIComponent(path)}`;
}
