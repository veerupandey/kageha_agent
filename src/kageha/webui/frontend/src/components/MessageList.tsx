import { memo, useEffect, useMemo, useRef, useState } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import type { ChatMessage, ComputerFrame, ToolCard } from "../api/types";
import { friendlyActivityLabel } from "../lib/activityUi";
import {
  artifactDownloadUrl,
  artifactFileUrl,
  canvasKindForPath,
  fileBasename,
  isChatMediaArtifact,
  isPreviewableKind,
  kindLabel,
  showcaseSortKey,
} from "../lib/artifactMedia";
import { cn } from "../lib/cn";
import {
  extractArtifactPaths,
  rewriteMarkdownMediaHtml,
} from "../lib/markdownMedia";
import { useAppStore } from "../store";
import { TerminalActivity } from "./TerminalActivity";

marked.setOptions({ gfm: true, breaks: true });

const MD_CACHE_MAX = 200;
const mdCache = new Map<string, string>();

function cacheKey(id: string, text: string, sessionId: string): string {
  return `${sessionId}\0${id}\0${text.length}\0${text.slice(0, 64)}\0${text.slice(-64)}`;
}

function renderMarkdownCached(
  id: string,
  text: string,
  sessionId: string | null,
): string {
  const sid = sessionId || "";
  const key = cacheKey(id, text, sid);
  const hit = mdCache.get(key);
  if (hit != null) return hit;
  const raw = marked.parse(text || "", { async: false }) as string;
  const html = rewriteMarkdownMediaHtml(DOMPurify.sanitize(raw), sessionId);
  mdCache.set(key, html);
  if (mdCache.size > MD_CACHE_MAX) {
    const first = mdCache.keys().next().value;
    if (first != null) mdCache.delete(first);
  }
  return html;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function lightMarkdown(text: string): string {
  const escaped = escapeHtml(text || "");
  return escaped
    .split(/\n\n+/)
    .map((p) => `<p>${p.replace(/\n/g, "<br>")}</p>`)
    .join("");
}

/** Ephemeral tool pulse — current tools only; gone when the turn finishes. */
const LiveToolPulse = memo(function LiveToolPulse({
  cards,
  streaming,
}: {
  cards: ToolCard[];
  streaming: boolean;
}) {
  if (!streaming || !cards.length) return null;

  const running = cards.filter(
    (c) => !c.status || c.status === "running" || c.status === "pending",
  );
  const source = running.length ? running : cards.slice(-2);
  const names: string[] = [];
  for (const card of source) {
    const name = String(card.name || "").trim();
    if (!name || names.includes(name)) continue;
    names.push(name);
    if (names.length >= 3) break;
  }
  if (!names.length) return null;

  return (
    <div
      className="mb-2 flex items-center gap-2 font-mono text-sm text-faint"
      aria-live="polite"
    >
      <span className="inline-block h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-accent" />
      <span className="min-w-0 truncate text-muted">{names.join(" · ")}</span>
    </div>
  );
});

const ComputerFrames = memo(function ComputerFrames({
  frames,
}: {
  frames: ComputerFrame[];
}) {
  const openCanvasItem = useAppStore((s) => s.openCanvasItem);
  if (!frames.length) return null;
  return (
    <div className="mb-2 flex gap-2 overflow-x-auto pb-1">
      {frames.map((frame, i) => {
        const path = String(frame.path || "").trim();
        return (
          <button
            key={`${frame.url}-${i}`}
            type="button"
            className="block w-36 shrink-0 overflow-hidden rounded-md border border-line bg-surface text-left"
            title={frame.caption || frame.action || frame.app || "Open in canvas"}
            onClick={() => {
              if (path) openCanvasItem(path, { expand: true });
              else window.open(frame.url, "_blank", "noopener,noreferrer");
            }}
          >
            <img
              src={frame.url}
              alt={frame.caption || "computer frame"}
              className="h-24 w-full object-cover"
            />
            {frame.caption ? (
              <span className="block truncate px-1.5 py-1 text-[0.7rem] text-muted">
                {frame.caption}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
});

function ArtifactThumb({
  path,
  url,
  kind,
}: {
  path: string;
  url?: string;
  kind: ReturnType<typeof canvasKindForPath>;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setFailed(false);
  }, [url]);
  const name = fileBasename(path);
  const showImage = kind === "image" && url && !failed;

  if (showImage) {
    return (
      <img
        key={url}
        src={url}
        alt={name}
        className="h-full w-full object-cover transition-transform duration-200 group-hover:scale-[1.02]"
        onError={() => setFailed(true)}
      />
    );
  }

  if (kind === "audio" && url) {
    return (
      <div className="flex h-full w-full flex-col justify-between bg-gradient-to-b from-canvas to-line/40 px-3 py-2.5">
        <div className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-surface text-xs font-semibold text-accent shadow-sm ring-1 ring-line">
            ♪
          </span>
          <span className="line-clamp-2 text-[0.72rem] font-medium leading-snug text-ink">
            {name}
          </span>
        </div>
        <audio
          src={url}
          controls
          preload="metadata"
          className="w-full"
          onClick={(e) => e.stopPropagation()}
        />
      </div>
    );
  }

  const glyph =
    kind === "video"
      ? "▶"
      : kind === "pdf"
        ? "PDF"
        : kind === "presentation"
          ? "PPT"
          : kind === "markdown"
            ? "MD"
            : kind === "document"
              ? "DOC"
              : "◇";

  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-2 bg-gradient-to-b from-canvas to-line/40 px-3">
      <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-surface text-[0.7rem] font-semibold tracking-wide text-accent shadow-sm ring-1 ring-line">
        {glyph}
      </span>
      <span className="line-clamp-2 text-center text-[0.72rem] font-medium leading-snug text-ink">
        {name}
      </span>
      <span className="text-[0.62rem] text-faint">{kindLabel(kind)}</span>
    </div>
  );
}

/** Inline media strip — deliverables only (no scripts / computer noise). */
const MessageArtifacts = memo(function MessageArtifacts({
  paths,
}: {
  paths: string[];
}) {
  const sessionId = useAppStore((s) => s.sessionId);
  const openCanvasItem = useAppStore((s) => s.openCanvasItem);
  const refs = useMemo(
    () =>
      [...paths]
        .filter(isChatMediaArtifact)
        .sort((a, b) => {
          const [ra, pa] = showcaseSortKey(a);
          const [rb, pb] = showcaseSortKey(b);
          return ra - rb || pa.localeCompare(pb);
        })
        .slice(0, 8),
    [paths],
  );

  if (!refs.length) return null;

  return (
    <div className="mb-3 flex gap-2.5 overflow-x-auto pb-1">
      {refs.map((path) => {
        const kind = canvasKindForPath(path);
        const url = artifactFileUrl(sessionId, path);
        const previewable = isPreviewableKind(kind);
        const dl = artifactDownloadUrl(sessionId, path);
        return (
          <div
            key={path}
            role="button"
            tabIndex={0}
            className="group relative h-32 w-44 shrink-0 cursor-pointer overflow-hidden rounded-xl border border-line bg-surface text-left shadow-[0_1px_2px_rgba(28,27,25,0.04)] transition hover:border-accent/35 hover:shadow-md"
            title={path}
            onClick={() => openCanvasItem(path, { expand: previewable })}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                openCanvasItem(path, { expand: previewable });
              }
            }}
          >
            <ArtifactThumb path={path} url={url} kind={kind} />
            {kind === "image" ? (
              <span className="pointer-events-none absolute inset-x-0 bottom-0 z-10 bg-gradient-to-t from-ink/55 to-transparent px-2.5 pb-2 pt-8 text-[0.68rem] font-medium text-white opacity-0 transition group-hover:opacity-100">
                {fileBasename(path)}
              </span>
            ) : null}
            {dl ? (
              <a
                href={dl}
                download={fileBasename(path)}
                className="absolute right-1.5 top-1.5 z-10 rounded-md bg-surface/95 px-1.5 py-0.5 text-[0.65rem] font-medium text-ink opacity-0 shadow-sm ring-1 ring-line transition group-hover:opacity-100"
                title="Download"
                onClick={(e) => e.stopPropagation()}
              >
                Save
              </a>
            ) : null}
          </div>
        );
      })}
    </div>
  );
});

const STREAM_THROTTLE_MS = 50;

function useThrottledStreamText(text: string, streaming: boolean): string {
  const [display, setDisplay] = useState(text);
  const latest = useRef(text);
  const lastFlush = useRef(0);
  const timerRef = useRef(0);

  latest.current = text;

  useEffect(() => {
    if (!streaming) {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = 0;
      }
      setDisplay(text);
      return;
    }

    const now = performance.now();
    if (now - lastFlush.current >= STREAM_THROTTLE_MS) {
      lastFlush.current = now;
      setDisplay(text);
      return;
    }

    if (timerRef.current) return;

    const wait = Math.max(0, STREAM_THROTTLE_MS - (now - lastFlush.current));
    timerRef.current = window.setTimeout(() => {
      timerRef.current = 0;
      lastFlush.current = performance.now();
      setDisplay(latest.current);
    }, wait);
  }, [text, streaming]);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  return display;
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
}

const MessageRow = memo(function MessageRow({
  message: m,
  showRetry,
  onRetry,
}: {
  message: ChatMessage;
  showRetry?: boolean;
  onRetry?: () => void;
}) {
  const streaming = Boolean(m.streaming);
  const throttledText = useThrottledStreamText(m.text || "", streaming);
  const [copied, setCopied] = useState(false);
  const sessionId = useAppStore((s) => s.sessionId);
  const upsertCanvasPaths = useAppStore((s) => s.upsertCanvasPaths);
  const openCanvasItem = useAppStore((s) => s.openCanvasItem);

  const artifactPaths = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    const push = (raw: string) => {
      const path = String(raw || "").replace(/\\/g, "/").replace(/^\/+/, "");
      if (!path || seen.has(path)) return;
      seen.add(path);
      out.push(path);
    };
    for (const card of m.toolCards || []) {
      for (const ref of card.artifactRefs || []) push(ref);
    }
    if (!streaming && m.role === "assistant") {
      for (const path of extractArtifactPaths(throttledText)) push(path);
    }
    return out.filter(isChatMediaArtifact);
  }, [m.toolCards, m.role, throttledText, streaming]);

  useEffect(() => {
    if (!artifactPaths.length || streaming) return;
    upsertCanvasPaths(artifactPaths);
  }, [artifactPaths, streaming, upsertCanvasPaths]);

  const bodyHtml = useMemo(() => {
    if (!throttledText) return "";
    if (streaming) {
      return rewriteMarkdownMediaHtml(lightMarkdown(throttledText), sessionId);
    }
    return renderMarkdownCached(m.id, throttledText, sessionId);
  }, [m.id, throttledText, streaming, sessionId]);

  const stepList = m.steps || [];
  const isUser = m.role === "user";
  const showActivity =
    !isUser && (streaming || stepList.length > 0);

  return (
    <article
      className={cn(
        "group relative mx-auto w-full max-w-3xl px-4 py-4 md:px-6 animate-[fadeInUp_250ms_ease-out]",
        isUser ? "bg-transparent" : "bg-transparent",
      )}
    >
      <div className="mb-1.5 flex items-center gap-2 text-xs">
        <span
          className={cn(
            "font-semibold tracking-wide",
            isUser ? "text-muted" : "text-accent",
          )}
        >
          {isUser ? "You" : "Kageha"}
        </span>
        {!streaming && m.statusLabel ? (
          <span className="text-faint">
            {friendlyActivityLabel(m.statusLabel) || m.statusLabel}
          </span>
        ) : null}
        {!streaming && (m.text || showRetry) ? (
          <div className="ml-auto flex gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
            {m.text ? (
              <button
                type="button"
                className="rounded px-1.5 py-0.5 text-faint hover:bg-line/70 hover:text-ink"
                onClick={() => {
                  void copyText(m.text).then(() => {
                    setCopied(true);
                    window.setTimeout(() => setCopied(false), 1400);
                  });
                }}
              >
                {copied ? "Copied" : "Copy"}
              </button>
            ) : null}
            {showRetry ? (
              <button
                type="button"
                className="rounded px-1.5 py-0.5 text-faint hover:bg-line/70 hover:text-ink"
                onClick={onRetry}
              >
                Retry
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      {streaming && m.toolCards && m.toolCards.length > 0 ? (
        <LiveToolPulse cards={m.toolCards} streaming={streaming} />
      ) : null}
      {showActivity ? (
        <TerminalActivity
          steps={stepList}
          liveLabel={m.statusLabel || m.statusDetail}
          streaming={streaming}
        />
      ) : null}

      {artifactPaths.length > 0 ? (
        <MessageArtifacts paths={artifactPaths} />
      ) : null}
      {m.computerFrames && m.computerFrames.length > 0 ? (
        <ComputerFrames frames={m.computerFrames} />
      ) : null}
      {bodyHtml ? (
        <div
          className={cn(
            "markdown",
            isUser &&
              "rounded-xl bg-surface px-3.5 py-2.5 shadow-[0_0_0_1px_var(--color-line)]",
          )}
          dangerouslySetInnerHTML={{ __html: bodyHtml }}
          onClick={(e) => {
            const target = e.target as HTMLElement | null;
            if (!target) return;
            const link = target.closest("a.artifact-path") as HTMLAnchorElement | null;
            if (link) {
              const path = link.getAttribute("data-artifact") || "";
              if (path) {
                e.preventDefault();
                openCanvasItem(path, {
                  expand: isPreviewableKind(canvasKindForPath(path)),
                });
              }
              return;
            }
            if (target.tagName !== "IMG") return;
            const src =
              (target as HTMLImageElement).currentSrc ||
              target.getAttribute("src") ||
              "";
            const marker = "/files/";
            const idx = src.indexOf(marker);
            if (idx < 0) return;
            const path = decodeURIComponent(
              src.slice(idx + marker.length).split("?")[0] || "",
            );
            if (!path) return;
            e.preventDefault();
            openCanvasItem(path, { expand: true });
          }}
        />
      ) : streaming ? (
        <div className="text-sm text-muted">
          <span className="inline-block h-4 w-[2px] rounded-full bg-accent animate-[blink_1s_steps(2,start)_infinite]" />
        </div>
      ) : null}
    </article>
  );
});

function MessageSkeletons() {
  return (
    <div
      className="mx-auto w-full max-w-3xl space-y-6 px-4 py-8 md:px-6"
      aria-busy="true"
      aria-label="Loading messages"
    >
      {[0, 1, 2].map((i) => (
        <div key={i} className="animate-pulse space-y-2">
          <div className="h-3 w-16 rounded bg-line" />
          <div className="h-4 w-full rounded bg-line/80" />
          <div className="h-4 w-2/3 rounded bg-line/60" />
        </div>
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex h-full min-h-[16rem] flex-col items-center justify-center px-6 text-center">
      <p className="text-lg font-medium text-ink">Message Kageha…</p>
      <p className="mt-2 max-w-sm text-sm text-muted">
        Ask a question, start a task, or type{" "}
        <span className="font-mono text-ink">/</span> for commands.
      </p>
    </div>
  );
}

export function MessageList({ messages }: { messages: ChatMessage[] }) {
  const sessionLoading = useAppStore((s) => s.sessionLoading);
  const runStatus = useAppStore((s) => s.runStatus);
  const sending = useAppStore((s) => s.sending);
  const retryLastTurn = useAppStore((s) => s.retryLastTurn);
  const showToast = useAppStore((s) => s.showToast);

  const stickToBottom = useRef(true);
  const [showJump, setShowJump] = useState(false);

  const getScroller = () =>
    document.getElementById("conversation") as HTMLElement | null;

  const lastUserText = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "user" && messages[i].text?.trim()) {
        return messages[i].text;
      }
    }
    return "";
  }, [messages]);

  const lastIndex = messages.length - 1;
  const lastMsg = lastIndex >= 0 ? messages[lastIndex] : null;
  const canRetry =
    Boolean(lastUserText) &&
    !sending &&
    Boolean(
      lastMsg &&
        (lastMsg.role === "assistant" || lastMsg.role === "system") &&
        (runStatus === "error" ||
          lastMsg.statusLabel === "Error" ||
          /error|fail/i.test(lastMsg.statusLabel || "")),
    );

  useEffect(() => {
    const el = getScroller();
    if (!el) return;
    const onScroll = () => {
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
      const stuck = dist < 80;
      stickToBottom.current = stuck;
      setShowJump(!stuck && messages.length > 0);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [messages.length]);

  useEffect(() => {
    if (!stickToBottom.current) return;
    const el = getScroller();
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, sending]);

  const jumpToLatest = () => {
    stickToBottom.current = true;
    setShowJump(false);
    const el = getScroller();
    if (el) el.scrollTop = el.scrollHeight;
  };

  if (sessionLoading) {
    return <MessageSkeletons />;
  }

  if (!messages.length) {
    return <EmptyState />;
  }

  return (
    <>
      <div className="pb-4 pt-2" id="messages">
        {messages.map((m, i) => (
          <MessageRow
            key={m.id}
            message={m}
            showRetry={canRetry && i === lastIndex}
            onRetry={() => {
              void retryLastTurn().catch((err: Error) =>
                showToast(err.message || String(err)),
              );
            }}
          />
        ))}
      </div>
      {showJump ? (
        <button
          type="button"
          className="sticky bottom-3 left-1/2 z-10 mx-auto -translate-x-1/2 rounded-full border border-line bg-surface px-3 py-1.5 text-xs font-medium shadow-md"
          onClick={jumpToLatest}
        >
          Jump to latest
        </button>
      ) : null}
    </>
  );
}
