import { useEffect, useMemo, useRef, useState } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import type { CanvasItem, CanvasKind } from "../lib/artifactMedia";
import { fileBasename, isPreviewableKind } from "../lib/artifactMedia";

marked.setOptions({ gfm: true, breaks: true });

interface MediaCanvasProps {
  open: boolean;
  items: CanvasItem[];
  index: number;
  onClose: () => void;
  onIndexChange: (index: number) => void;
}

export function MediaCanvas({
  open,
  items,
  index,
  onClose,
  onIndexChange,
}: MediaCanvasProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const [docHtml, setDocHtml] = useState<string | null>(null);
  const [docText, setDocText] = useState<string | null>(null);
  const [docError, setDocError] = useState<string | null>(null);
  const [docLoading, setDocLoading] = useState(false);

  const safeIndex = items.length
    ? Math.max(0, Math.min(index, items.length - 1))
    : 0;
  const item = items[safeIndex];

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    document.body.classList.add("media-canvas-open");
    return () => document.body.classList.remove("media-canvas-open");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        onClose();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        onIndexChange(safeIndex <= 0 ? items.length - 1 : safeIndex - 1);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        onIndexChange(safeIndex >= items.length - 1 ? 0 : safeIndex + 1);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, safeIndex, items.length, onClose, onIndexChange]);

  useEffect(() => {
    if (!open || !item) {
      setDocHtml(null);
      setDocText(null);
      setDocError(null);
      setDocLoading(false);
      return;
    }
    if (item.kind !== "markdown" && item.kind !== "text") {
      setDocHtml(null);
      setDocText(null);
      setDocError(null);
      setDocLoading(false);
      return;
    }

    let cancelled = false;
    const load = async () => {
      setDocLoading(true);
      setDocError(null);
      try {
        let text = item.text;
        if (text == null && item.url.startsWith("/")) {
          const res = await fetch(item.url);
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          text = await res.text();
        }
        if (cancelled) return;
        if (item.kind === "markdown") {
          const raw = marked.parse(text || "", { async: false }) as string;
          setDocHtml(DOMPurify.sanitize(raw));
          setDocText(null);
        } else {
          setDocHtml(null);
          setDocText(text || "");
        }
      } catch (err) {
        if (cancelled) return;
        setDocError(err instanceof Error ? err.message : "Failed to load");
        setDocHtml(null);
        setDocText(null);
      } finally {
        if (!cancelled) setDocLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [open, item]);

  useEffect(() => {
    return () => {
      const stage = stageRef.current;
      stage?.querySelectorAll("video").forEach((v) => {
        try {
          v.pause();
          v.removeAttribute("src");
          v.load();
        } catch {
          /* ignore */
        }
      });
    };
  }, [item?.url, open]);

  const stageClass = useMemo(() => {
    if (!item) return "media-canvas-stage";
    const parts = ["media-canvas-stage"];
    if (
      item.kind === "markdown" ||
      item.kind === "text" ||
      item.kind === "pdf"
    ) {
      parts.push("is-doc");
    }
    if (item.kind === "download") parts.push("is-file");
    return parts.join(" ");
  }, [item]);

  if (!open || !item) return null;

  const canOpen =
    Boolean(item.url) &&
    (item.url.startsWith("/") || /^https?:/i.test(item.url));
  const name = fileBasename(item.caption || item.url);

  return (
    <div
      className="media-canvas"
      role="dialog"
      aria-modal="true"
      aria-label="Media and document viewer"
    >
      <div
        className="media-canvas-backdrop"
        onClick={onClose}
        aria-hidden="true"
      />
      <div className="media-canvas-chrome">
        <div className="media-canvas-top">
          <p className="media-canvas-counter">
            {safeIndex + 1} / {items.length}
          </p>
          <p className="media-canvas-caption" title={item.caption || ""}>
            {item.caption || ""}
          </p>
          <div className="media-canvas-top-actions">
            {canOpen ? (
              <a
                className="btn ghost compact"
                href={item.url}
                target={item.kind === "download" ? undefined : "_blank"}
                rel="noopener noreferrer"
                download={item.kind === "download" ? name : undefined}
              >
                {item.kind === "download" ? "Download" : "Open"}
              </a>
            ) : null}
            <button
              ref={closeRef}
              type="button"
              className="btn ghost icon media-canvas-close"
              aria-label="Close media viewer"
              onClick={onClose}
            >
              ✕
            </button>
          </div>
        </div>
        <button
          type="button"
          className="media-canvas-nav media-canvas-prev"
          aria-label="Previous media"
          disabled={items.length <= 1}
          onClick={() =>
            onIndexChange(safeIndex <= 0 ? items.length - 1 : safeIndex - 1)
          }
        >
          ‹
        </button>
        <div ref={stageRef} className={stageClass}>
          <CanvasStage
            kind={item.kind}
            url={item.url}
            caption={item.caption}
            name={name}
            docLoading={docLoading}
            docError={docError}
            docHtml={docHtml}
            docText={docText}
          />
        </div>
        <button
          type="button"
          className="media-canvas-nav media-canvas-next"
          aria-label="Next media"
          disabled={items.length <= 1}
          onClick={() =>
            onIndexChange(safeIndex >= items.length - 1 ? 0 : safeIndex + 1)
          }
        >
          ›
        </button>
      </div>
    </div>
  );
}

function CanvasStage({
  kind,
  url,
  caption,
  name,
  docLoading,
  docError,
  docHtml,
  docText,
}: {
  kind: CanvasKind;
  url: string;
  caption: string;
  name: string;
  docLoading: boolean;
  docError: string | null;
  docHtml: string | null;
  docText: string | null;
}) {
  if (kind === "video") {
    return (
      <video
        controls
        playsInline
        preload="metadata"
        src={url}
        controlsList="nodownload"
        autoPlay
      />
    );
  }
  if (kind === "pdf") {
    return (
      <iframe className="media-canvas-pdf" src={url} title={caption || "PDF"} />
    );
  }
  if (kind === "markdown" || kind === "text") {
    return (
      <div className="media-canvas-doc">
        {docLoading ? (
          <p className="media-canvas-doc-loading">Loading…</p>
        ) : null}
        {docError ? (
          <p className="media-canvas-doc-error">{docError}</p>
        ) : null}
        {docHtml ? (
          <div
            className="media-canvas-md md"
            dangerouslySetInnerHTML={{ __html: docHtml }}
          />
        ) : null}
        {docText != null && !docHtml ? (
          <pre className="media-canvas-doc-pre">{docText}</pre>
        ) : null}
      </div>
    );
  }
  if (kind === "download") {
    return (
      <div className="media-canvas-file">
        <p className="media-canvas-file-title">{name}</p>
        <p className="media-canvas-file-hint">
          {/\.(pptx?|docx?|xlsx?)$/i.test(name)
            ? "Office files download here — open locally in Keynote, PowerPoint, etc."
            : "Download this file to your machine."}
        </p>
        <div className="media-canvas-file-actions">
          <a className="btn primary" href={url} download={name}>
            Download
          </a>
        </div>
      </div>
    );
  }
  return <img src={url} alt={caption || "Media"} draggable={false} />;
}

export function buildCanvasItems(
  artifacts: Array<{
    path?: string;
    name?: string;
    kind?: string;
    url?: string;
  }>,
  sessionId: string | null,
  fileUrl: (
    sessionId: string | null,
    path: string,
    existingUrl?: string,
  ) => string | undefined,
  kindFor: (path: string, kindHint?: string) => CanvasKind,
): CanvasItem[] {
  const out: CanvasItem[] = [];
  const seen = new Set<string>();
  for (const a of artifacts) {
    const path = String(a.path || a.name || "");
    if (!path) continue;
    const url = fileUrl(sessionId, path, a.url);
    if (!url || seen.has(url)) continue;
    const kind = kindFor(path, a.kind);
    if (!isPreviewableKind(kind) && kind !== "download") continue;
    // Prefer media / docs in the gallery; still allow office download cards.
    if (
      kind === "download" &&
      !/\.(pptx?|docx?|xlsx?|zip|pdf)$/i.test(path) &&
      a.kind !== "presentation" &&
      a.kind !== "document" &&
      a.kind !== "archive"
    ) {
      continue;
    }
    seen.add(url);
    out.push({ url, kind, caption: path });
  }
  return out;
}
