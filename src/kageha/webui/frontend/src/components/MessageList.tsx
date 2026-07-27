import { memo, useEffect, useMemo, useRef, useState } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import type { ChatMessage, ComputerFrame, ToolCard } from "../api/types";
import { useAppStore } from "../store";

marked.setOptions({ gfm: true, breaks: true });

const MD_CACHE_MAX = 200;
const mdCache = new Map<string, string>();

const STARTER_CHIPS: { label: string; value: string }[] = [
  { label: "Explain this codebase", value: "Explain this codebase" },
  { label: "/plan a feature", value: "/plan " },
  { label: "/browser status", value: "/browser status" },
];

function cacheKey(id: string, text: string): string {
  return `${id}\0${text.length}\0${text.slice(0, 64)}\0${text.slice(-64)}`;
}

function renderMarkdownCached(id: string, text: string): string {
  const key = cacheKey(id, text);
  const hit = mdCache.get(key);
  if (hit != null) return hit;
  const raw = marked.parse(text || "", { async: false }) as string;
  const html = DOMPurify.sanitize(raw);
  mdCache.set(key, html);
  if (mdCache.size > MD_CACHE_MAX) {
    const first = mdCache.keys().next().value;
    if (first != null) mdCache.delete(first);
  }
  return html;
}

/** Escape HTML for safe plain-text streaming display. */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Light streaming markdown: paragraphs + line breaks, no full parse. */
function lightMarkdown(text: string): string {
  const escaped = escapeHtml(text || "");
  return escaped
    .split(/\n\n+/)
    .map((p) => `<p>${p.replace(/\n/g, "<br>")}</p>`)
    .join("");
}

const ToolCards = memo(function ToolCards({ cards }: { cards: ToolCard[] }) {
  if (!cards.length) return null;
  return (
    <div className="stream-tool-cards">
      {cards.map((card) => (
        <details
          key={card.id}
          className="tool-card"
          data-status={card.status || "running"}
          open={card.status === "running"}
        >
          <summary className="tool-card-summary">
            <span className="tool-card-status" />
            <span className="tool-card-name">{card.name}</span>
            <span className="tool-card-meta">
              {card.durationMs != null ? `${card.durationMs}ms` : card.status || ""}
            </span>
          </summary>
          <div className="tool-card-body">
            {card.argsPreview ? (
              <pre className="tool-card-args">{card.argsPreview}</pre>
            ) : null}
            {card.resultPreview ? (
              <pre className="tool-card-result">{card.resultPreview}</pre>
            ) : null}
            {card.artifactRefs && card.artifactRefs.length > 0 ? (
              <div className="tool-card-arts">
                {card.artifactRefs.map((ref) => (
                  <span key={ref} className="tool-card-art">
                    {ref}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        </details>
      ))}
    </div>
  );
});

const ComputerFrames = memo(function ComputerFrames({
  frames,
}: {
  frames: ComputerFrame[];
}) {
  if (!frames.length) return null;
  return (
    <div className="computer-frame-strip">
      {frames.map((frame, i) => (
        <a
          key={`${frame.url}-${i}`}
          className="computer-frame-thumb"
          href={frame.url}
          target="_blank"
          rel="noreferrer"
          title={frame.caption || frame.action || frame.app || "frame"}
        >
          <img src={frame.url} alt={frame.caption || "computer frame"} />
          {frame.caption ? (
            <span className="computer-frame-caption">{frame.caption}</span>
          ) : null}
        </a>
      ))}
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
  const [traceOpen, setTraceOpen] = useState(false);
  const showToolCards = useAppStore((s) => s.prefs.showToolCards);

  const bodyHtml = useMemo(() => {
    if (!throttledText) return "";
    if (streaming) return lightMarkdown(throttledText);
    return renderMarkdownCached(m.id, throttledText);
  }, [m.id, throttledText, streaming]);

  const activityLabel =
    m.statusLabel ||
    (streaming ? "Working…" : "") ||
    (m.steps && m.steps.length ? m.steps[m.steps.length - 1]?.label : "");
  const activityDetail = m.statusDetail || "";
  const stepList = m.steps || [];

  return (
    <article
      className={`message msg-bubble message-${m.role}${streaming ? " is-streaming" : ""}`}
    >
      <header className="message-head">
        <span className="message-role">{m.role}</span>
        {!streaming && m.statusLabel ? (
          <span className="message-status">{m.statusLabel}</span>
        ) : null}
        {!streaming && (m.text || showRetry) ? (
          <div className="message-actions">
            {m.text ? (
              <button
                type="button"
                className="btn ghost compact message-action"
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
                className="btn ghost compact message-action"
                onClick={onRetry}
              >
                Retry
              </button>
            ) : null}
          </div>
        ) : null}
      </header>

      {/* Live intermediate activity — pulse + optional detail subtitle */}
      {streaming && activityLabel ? (
        <div className="stream-activity" aria-live="polite">
          <span className="stream-activity-spinner" aria-hidden="true" />
          <div className="stream-activity-text">
            <span className="stream-activity-label">{activityLabel}</span>
            {activityDetail ? (
              <span className="stream-activity-detail">{activityDetail}</span>
            ) : null}
          </div>
        </div>
      ) : null}

      {stepList.length > 0 ? (
        <details
          className="stream-trace"
          open={streaming || traceOpen}
          onToggle={(e) => setTraceOpen((e.target as HTMLDetailsElement).open)}
        >
          <summary className="stream-trace-summary">
            {streaming
              ? `Activity · ${stepList.length} step${stepList.length === 1 ? "" : "s"}`
              : `Trace · ${stepList.length} step${stepList.length === 1 ? "" : "s"}`}
          </summary>
          <ol className="stream-trace-list">
            {stepList.map((step, i) => {
              const current = streaming && i === stepList.length - 1;
              const details = step.detail || [];
              return (
                <li
                  key={`${m.id}-step-${i}-${step.label}`}
                  className={
                    current
                      ? "stream-trace-item is-current"
                      : "stream-trace-item"
                  }
                >
                  <span className="stream-trace-title">{step.label}</span>
                  {details.length > 0 ? (
                    <ul className="stream-trace-detail">
                      {details.map((line, j) => (
                        <li key={`${m.id}-step-${i}-d-${j}`}>{line}</li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              );
            })}
          </ol>
        </details>
      ) : null}

      {showToolCards && m.toolCards && m.toolCards.length > 0 ? (
        <ToolCards cards={m.toolCards} />
      ) : null}
      {m.computerFrames && m.computerFrames.length > 0 ? (
        <ComputerFrames frames={m.computerFrames} />
      ) : null}
      {bodyHtml ? (
        <div
          className={`message-body markdown${streaming ? " is-stream-body" : ""}`}
          dangerouslySetInnerHTML={{ __html: bodyHtml }}
        />
      ) : streaming ? (
        <div className="message-body muted stream-placeholder">
          <span className="stream-cursor" aria-hidden="true" />
        </div>
      ) : null}
    </article>
  );
});

function MessageSkeletons() {
  return (
    <div className="messages message-skeletons" aria-busy="true" aria-label="Loading messages">
      {[0, 1, 2].map((i) => (
        <div key={i} className="message-skeleton" data-role={i % 2 === 0 ? "user" : "assistant"}>
          <div className="message-skeleton-head" />
          <div className="message-skeleton-line" />
          <div className="message-skeleton-line short" />
        </div>
      ))}
    </div>
  );
}

function HeroEmpty() {
  const setDraft = useAppStore((s) => s.setDraft);

  return (
    <div className="hero" id="hero">
      <p className="hero-kicker">Workspace</p>
      <h1 className="brand" id="brand">
        Kageha
      </h1>
      <p className="lede">
        Quiet power for durable turns — memory, parallel tasks, and work that lasts.
      </p>
      <div className="hero-starters" role="group" aria-label="Starter prompts">
        {STARTER_CHIPS.map((chip) => (
          <button
            key={chip.label}
            type="button"
            className="hero-starter-chip"
            onClick={() => {
              setDraft(chip.value);
              requestAnimationFrame(() => {
                document.getElementById("message-input")?.focus();
              });
            }}
          >
            {chip.label}
          </button>
        ))}
      </div>
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
    return <HeroEmpty />;
  }

  return (
    <>
      <div className="messages" id="messages">
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
          className="jump-to-latest"
          onClick={jumpToLatest}
        >
          Jump to latest
        </button>
      ) : null}
    </>
  );
}
