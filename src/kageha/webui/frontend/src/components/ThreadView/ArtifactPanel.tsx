import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import type { CanvasItem } from "../../lib/artifactMedia";
import {
  artifactDownloadUrl,
  fileExt,
  formatBytes,
  kindLabel,
} from "../../lib/artifactMedia";
import { cn } from "../../lib/cn";
import { useAppStore } from "../../store";
import { CodeBlock } from "../shared/CodeBlock";
import type { ArtifactFilter } from "./ThreadHeader";

function renderMd(text: string): string {
  const raw = marked.parse(text || "", { async: false }) as string;
  return DOMPurify.sanitize(raw);
}

interface ArtifactPanelProps {
  filter: ArtifactFilter;
  onOpenLightbox: (path: string) => void;
  onCollapse?: () => void;
}

function matchesFilter(item: CanvasItem, filter: ArtifactFilter): boolean {
  if (filter === "all") return true;
  if (filter === "images") return item.kind === "image";
  if (filter === "video") return item.kind === "video";
  if (filter === "code") return item.kind === "code";
  if (filter === "documents")
    return (
      item.kind === "pdf" ||
      item.kind === "document" ||
      item.kind === "text" ||
      item.kind === "spreadsheet" ||
      item.kind === "presentation"
    );
  if (filter === "webpages") return item.kind === "markdown" || item.kind === "webpage";
  return true;
}

// ── File type icons (text-based, no emoji) ────────────────────────────

function FileIcon({ kind, ext }: { kind: CanvasItem["kind"]; ext: string }) {
  const colors: Record<string, string> = {
    image: "bg-pink-500/15 text-pink-400",
    video: "bg-purple-500/15 text-purple-400",
    audio: "bg-indigo-500/15 text-indigo-400",
    pdf: "bg-red-500/15 text-red-400",
    webpage: "bg-cyan-500/15 text-cyan-400",
    code: "bg-emerald-500/15 text-emerald-400",
    markdown: "bg-blue-500/15 text-blue-400",
    text: "bg-slate-500/15 text-slate-400",
    document: "bg-blue-500/15 text-blue-400",
    spreadsheet: "bg-green-500/15 text-green-400",
    presentation: "bg-orange-500/15 text-orange-400",
  };
  const color = colors[kind] || "bg-slate-500/15 text-slate-400";
  const label = ext ? ext.replace(".", "").toUpperCase().slice(0, 4) : kindLabel(kind).slice(0, 3).toUpperCase();

  return (
    <div
      className={cn(
        "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[0.6rem] font-bold tracking-wide",
        color,
      )}
    >
      {label}
    </div>
  );
}

// ── Inline Preview ────────────────────────────────────────────────────

function InlinePreview({
  item,
  onOpenFull,
}: {
  item: CanvasItem;
  onOpenFull: () => void;
}) {
  const sessionId = useAppStore((s) => s.sessionId);
  const runStatus = useAppStore((s) => s.runStatus);
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);

  // Fetch file content — and poll every 2s while agent is running
  useEffect(() => {
    if (item.kind !== "code" && item.kind !== "text" && item.kind !== "markdown" && item.kind !== "webpage") {
      setText("");
      return;
    }
    let cancelled = false;
    const fetchContent = () => {
      void fetch(item.url)
        .then((r) => r.text())
        .then((body) => {
          if (!cancelled) {
            setText(body.slice(0, 50_000));
            setLoading(false);
          }
        })
        .catch(() => {
          if (!cancelled) {
            setText("Could not load.");
            setLoading(false);
          }
        });
    };
    setLoading(true);
    fetchContent();

    // Poll while agent is actively running
    let interval: ReturnType<typeof setInterval> | null = null;
    if (runStatus === "running") {
      interval = setInterval(fetchContent, 2000);
    }
    return () => {
      cancelled = true;
      if (interval) clearInterval(interval);
    };
  }, [item.url, item.kind, runStatus]);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden border-t border-line bg-[var(--color-canvas)]">
      {/* Preview header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-line/50">
        <div className="flex items-center gap-2 min-w-0">
          <FileIcon kind={item.kind} ext={fileExt(item.path)} />
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <p className="truncate text-xs font-medium text-ink">{item.caption}</p>
              {runStatus === "running" && (item.kind === "code" || item.kind === "text" || item.kind === "markdown" || item.kind === "webpage") && (
                <span className="flex items-center gap-1 rounded bg-accent/15 px-1.5 py-0.5 text-[0.55rem] font-medium text-accent">
                  <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
                  Live
                </span>
              )}
            </div>
            <p className="text-[0.6rem] text-faint">{kindLabel(item.kind)}{item.size ? ` · ${formatBytes(item.size)}` : ""}</p>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            className="inline-flex items-center gap-1 rounded-md bg-accent/15 px-2.5 py-1 text-[0.65rem] font-medium text-accent hover:bg-accent/25 transition-colors"
            onClick={onOpenFull}
            title="Open fullscreen"
          >
            <span>⤢</span> Expand
          </button>
          {sessionId && (
            <a
              href={artifactDownloadUrl(sessionId, item.path) || item.url}
              download={item.caption}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[0.65rem] text-muted hover:bg-line/50 hover:text-ink transition-colors"
              title="Download"
            >
              ↓
            </a>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="min-h-0 flex-1 overflow-auto overscroll-contain">
        {item.kind === "image" ? (
          <div className="flex items-center justify-center p-4 bg-[#0d1117]">
            <img
              src={item.url}
              alt={item.caption}
              className="max-h-[20rem] max-w-full rounded object-contain"
            />
          </div>
        ) : item.kind === "video" ? (
          <div className="p-3">
            <video src={item.url} controls className="w-full rounded-lg max-h-[16rem]" />
          </div>
        ) : item.kind === "audio" ? (
          <div className="p-4">
            <audio src={item.url} controls className="w-full" />
          </div>
        ) : item.kind === "pdf" ? (
          <iframe
            src={item.url}
            title={item.caption}
            className="h-full min-h-[20rem] w-full border-0 bg-white"
          />
        ) : item.kind === "webpage" ? (
          <iframe
            src={item.url}
            title={item.caption}
            sandbox="allow-scripts allow-same-origin"
            className="h-full w-full border-0"
            style={{ minHeight: "20rem" }}
          />
        ) : item.kind === "code" ? (
          loading ? (
            <div className="p-4 text-xs text-muted">Loading…</div>
          ) : (
            <div className="p-2">
              <CodeBlock code={text} filename={item.caption} maxHeight="calc(100% - 1rem)" />
            </div>
          )
        ) : item.kind === "markdown" ? (
          loading ? (
            <div className="p-4 text-xs text-muted">Loading…</div>
          ) : (
            <div
              className="markdown p-4 overflow-auto h-full"
              dangerouslySetInnerHTML={{ __html: renderMd(text) }}
            />
          )
        ) : item.kind === "text" ? (
          loading ? (
            <div className="p-4 text-xs text-muted">Loading…</div>
          ) : (
            <pre className="p-3 font-mono text-[0.7rem] leading-relaxed text-ink whitespace-pre-wrap overflow-auto h-full">
              {text}
            </pre>
          )
        ) : (
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <FileIcon kind={item.kind} ext={fileExt(item.path)} />
            <p className="mt-2 text-xs text-muted">{kindLabel(item.kind)}</p>
            <button
              type="button"
              className="mt-2 rounded-md bg-accent/15 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/25"
              onClick={onOpenFull}
            >
              Open
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── File list item ────────────────────────────────────────────────────

function FileListItem({
  item,
  active,
  updatedThisTurn,
  onSelect,
}: {
  item: CanvasItem;
  active: boolean;
  updatedThisTurn: boolean;
  onSelect: () => void;
}) {
  const ext = fileExt(item.path);
  const name = item.caption || item.path.split("/").pop() || "file";

  return (
    <button
      type="button"
      className={cn(
        "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors",
        active
          ? "bg-[var(--color-accent-soft)] ring-1 ring-accent/30"
          : updatedThisTurn
            ? "bg-accent/5 ring-1 ring-accent/20"
            : "hover:bg-line/40",
      )}
      onClick={onSelect}
    >
      <FileIcon kind={item.kind} ext={ext} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-[0.8rem] font-medium text-ink">
          {name}
          {updatedThisTurn && !active && (
            <span className="ml-1.5 inline-flex h-1.5 w-1.5 rounded-full bg-accent" />
          )}
        </p>
        <p className="text-[0.6rem] text-faint">
          {kindLabel(item.kind)}
          {item.size ? ` · ${formatBytes(item.size)}` : ""}
        </p>
      </div>
    </button>
  );
}

// ── Main Panel ────────────────────────────────────────────────────────

export function ArtifactPanel({ filter, onOpenLightbox, onCollapse }: ArtifactPanelProps) {
  const canvasItems = useAppStore((s) => s.canvasItems);
  const canvasTurnPaths = useAppStore((s) => s.canvasTurnPaths);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const splitRef = useRef<HTMLDivElement>(null);
  const [listHeight, setListHeight] = useState(() => Number(localStorage.getItem("kageha.artifactListHeight")) || 280);
  const [resizingSplit, setResizingSplit] = useState(false);

  useEffect(() => {
    if (!resizingSplit) return;
    const onMove = (event: PointerEvent) => {
      const bounds = splitRef.current?.getBoundingClientRect();
      if (!bounds) return;
      const max = Math.max(100, bounds.height - 180);
      setListHeight(Math.min(max, Math.max(100, event.clientY - bounds.top)));
    };
    const onUp = () => setResizingSplit(false);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp, { once: true });
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [resizingSplit]);

  useEffect(() => localStorage.setItem("kageha.artifactListHeight", String(Math.round(listHeight))), [listHeight]);

  const filtered = useMemo(
    () => canvasItems.filter((item) => matchesFilter(item, filter)),
    [canvasItems, filter],
  );

  const selected = useMemo(
    () => filtered.find((i) => i.path === selectedPath) || null,
    [filtered, selectedPath],
  );

  // Auto-select the first item when filter changes, or newest item during a run
  useEffect(() => {
    if (filtered.length > 0 && !filtered.find((i) => i.path === selectedPath)) {
      const turnItem =
        filtered.find(
          (i) => canvasTurnPaths.has(i.path) && (i.kind === "webpage" || i.kind === "markdown" || i.kind === "image"),
        ) ||
        filtered.find((i) => canvasTurnPaths.has(i.path));
      setSelectedPath(turnItem?.path || filtered[filtered.length - 1].path);
    }
  }, [filtered, selectedPath, canvasTurnPaths]);

  const handleSelect = useCallback((path: string) => {
    setSelectedPath((prev) => (prev === path ? null : path));
  }, []);

  if (filtered.length === 0) {
    return (
      <div ref={splitRef} className="flex min-h-0 flex-1 flex-col">
        {onCollapse && (
          <div className="flex h-10 shrink-0 items-center justify-between border-b border-line px-3">
            <span className="text-[0.65rem] font-semibold text-muted uppercase tracking-wide">
              Artifacts
            </span>
            <button
              type="button"
              className="rounded-md px-2 py-1 text-xs text-muted hover:bg-line/70 hover:text-ink"
              onClick={onCollapse}
            >
              ⊟
            </button>
          </div>
        )}
        <div className="flex flex-1 flex-col items-center justify-center px-6 py-12 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-line/30">
            <span className="text-2xl text-faint">📂</span>
          </div>
          <p className="mt-3 text-sm font-medium text-muted">No artifacts yet</p>
          <p className="mt-1 max-w-[200px] text-xs text-faint leading-relaxed">
            Files generated by the agent will appear here with live preview
          </p>
        </div>
      </div>
    );
  }

  return (
    <div ref={splitRef} className="flex min-h-0 flex-1 flex-col">
      {/* Header */}
      <div className="flex h-10 shrink-0 items-center justify-between border-b border-line px-3">
        <div className="flex items-center gap-2">
          <span className="text-[0.65rem] font-semibold text-muted uppercase tracking-wide">
            Files
          </span>
          <span className="rounded-full bg-accent/15 px-1.5 py-0.5 text-[0.55rem] font-medium text-accent">
            {filtered.length}
          </span>
        </div>
        {onCollapse && (
          <button
            type="button"
            className="rounded-md px-2 py-1 text-xs text-muted hover:bg-line/70 hover:text-ink"
            onClick={onCollapse}
          >
            ⊟
          </button>
        )}
      </div>

      {/* Split: file list + preview */}
      <div className="flex min-h-0 flex-1 flex-col">
        {/* File list */}
        <div
          className={cn("overflow-y-auto overscroll-contain px-2 py-1.5 space-y-0.5 shrink-0", !selected && "flex-1")}
          style={selected ? { height: `${listHeight}px` } : undefined}
        >
          {filtered.map((item) => (
            <FileListItem
              key={item.path}
              item={item}
              active={item.path === selectedPath}
              updatedThisTurn={canvasTurnPaths.has(item.path)}
              onSelect={() => handleSelect(item.path)}
            />
          ))}
        </div>

        {selected && (
          <div
            className="group relative z-20 h-2 shrink-0 cursor-row-resize border-y border-line bg-line/70 hover:bg-accent/60"
            role="separator"
            aria-label="Resize file list and artifact preview"
            aria-orientation="horizontal"
            onPointerDown={(event) => {
              event.preventDefault();
              event.currentTarget.setPointerCapture?.(event.pointerId);
              setResizingSplit(true);
            }}
          >
            <span className="absolute left-1/2 top-1/2 h-0.5 w-14 -translate-x-1/2 -translate-y-1/2 rounded bg-faint group-hover:bg-accent" />
          </div>
        )}

        {/* Inline preview */}
        {selected && (
          <div className="min-h-0 flex-1 overflow-hidden">
            <InlinePreview
              item={selected}
              onOpenFull={() => onOpenLightbox(selected.path)}
            />
          </div>
        )}
      </div>
    </div>
  );
}
