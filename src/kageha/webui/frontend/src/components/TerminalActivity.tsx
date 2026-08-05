import { memo, useEffect, useMemo, useRef } from "react";
import type { ActivityStep } from "../api/types";
import {
  activityGlyph,
  activityGlyphClass,
  activityLineText,
  activityTarget,
  activityUrl,
  friendlyActivityLabel,
} from "../lib/activityUi";
import { cn } from "../lib/cn";

/** Live/replay activity feed — looks like the CLI status stream. */
export const TerminalActivity = memo(function TerminalActivity({
  steps,
  liveLabel,
  streaming,
  onOpenBrowser,
  onOpenComputer,
}: {
  steps: ActivityStep[];
  liveLabel?: string;
  streaming: boolean;
  onOpenBrowser?: (url?: string) => void;
  onOpenComputer?: () => void;
}) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const lines = useMemo(() => {
    const rows = steps.map((step, i) => ({
      key: `${i}-${step.label}`,
      glyph: activityGlyph(step.label),
      glyphClass: activityGlyphClass(step.label),
      text: activityLineText(step),
      target: activityTarget(`${step.kind || ""} ${step.label}`),
      url: activityUrl(...(step.detail || [])),
    }));
    if (streaming && liveLabel) {
      const friendly = friendlyActivityLabel(liveLabel);
      const last = rows[rows.length - 1]?.text;
      if (friendly && friendly !== last) {
        rows.push({
          key: `live-${friendly}`,
          glyph: activityGlyph(friendly),
          glyphClass: activityGlyphClass(friendly),
          text: friendly,
          target: activityTarget(friendly),
          url: null,
        });
      }
    }
    return rows.slice(-14);
  }, [steps, liveLabel, streaming]);

  useEffect(() => {
    if (!streaming) return;
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines, streaming]);

  if (!lines.length && !streaming) return null;

  if (!streaming) {
    return (
      <details className="terminal-activity group mb-2 overflow-hidden rounded-md border border-line">
        <summary className="cursor-pointer list-none px-3 py-1.5 font-mono text-sm text-faint">
          <span className="inline-block transition-transform duration-200 group-open:rotate-90 text-accent">{"▸"}</span>
          {" Activity · "}
          {steps.length}
        </summary>
        <div className="terminal-activity-body max-h-48 space-y-0.5 overflow-y-auto border-t border-line px-3 py-2 font-mono text-sm">
          {lines.map((line) => (
            <button
              key={line.key}
              type="button"
              disabled={!line.target}
              className={cn(
                "flex w-full gap-2 rounded-sm text-left leading-5 animate-[fadeInUp_100ms_ease-out]",
                line.target && "cursor-pointer hover:bg-accent-soft hover:text-accent",
              )}
              title={line.target ? `Open ${line.target} view` : undefined}
              onClick={() => {
                if (line.target === "browser") onOpenBrowser?.(line.url || undefined);
                if (line.target === "computer") onOpenComputer?.();
              }}
            >
              <span className={cn("w-4 shrink-0 text-center", line.glyphClass)}>
                {line.glyph}
              </span>
              <span className="min-w-0 flex-1 truncate text-ink">{line.text}</span>
              {line.target ? <span className="shrink-0 text-faint">Open ↗</span> : null}
            </button>
          ))}
        </div>
      </details>
    );
  }

  return (
    <div
      className="terminal-activity mb-2 overflow-hidden rounded-md border border-line"
      aria-live="polite"
    >
      <div className="flex items-center gap-2 border-b border-line px-3 py-1.5 font-mono text-sm text-faint">
        <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-teal-600" />
        <span>kageha · running</span>
      </div>
      <div
        ref={scrollerRef}
        className="terminal-activity-body max-h-40 space-y-0.5 overflow-y-auto px-3 py-2 font-mono text-sm"
      >
        {lines.length ? (
          lines.map((line, i) => {
            const current = i === lines.length - 1;
            return (
              <button
                key={line.key}
                type="button"
                disabled={!line.target}
                className={cn(
                  "flex gap-2 leading-5 animate-[fadeInUp_100ms_ease-out]",
                  current ? "text-ink" : "text-faint",
                  line.target && "w-full cursor-pointer rounded-sm text-left hover:bg-accent-soft hover:text-accent",
                )}
                onClick={() => {
                  if (line.target === "browser") onOpenBrowser?.(line.url || undefined);
                  if (line.target === "computer") onOpenComputer?.();
                }}
              >
                <span
                  className={cn(
                    "w-4 shrink-0 text-center",
                    current ? line.glyphClass : "text-faint",
                  )}
                >
                  {line.glyph}
                </span>
                <span className="min-w-0 flex-1 truncate">{line.text}</span>
                {line.target ? <span className="shrink-0 text-[0.65rem] text-faint">Open ↗</span> : null}
              </button>
            );
          })
        ) : (
          <div className="flex gap-2 leading-5 text-ink">
            <span className="w-4 shrink-0 text-center text-accent">{"✦"}</span>
            <span>Working…</span>
          </div>
        )}
      </div>
    </div>
  );
});
