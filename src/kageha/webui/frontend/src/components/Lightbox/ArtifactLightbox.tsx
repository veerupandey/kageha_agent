import { useCallback, useEffect, useMemo, useRef } from "react";
import type { CanvasItem } from "../../lib/artifactMedia";
import { useAppStore } from "../../store";
import { LightboxPreview } from "./LightboxPreview";
import { LightboxSidebar } from "./LightboxSidebar";

interface ArtifactLightboxProps {
  itemPath: string | null;
  onClose: () => void;
  onNavigate: (direction: "prev" | "next") => void;
}

const FOCUSABLE_SELECTOR =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

export function ArtifactLightbox({ itemPath, onClose, onNavigate }: ArtifactLightboxProps) {
  const canvasItems = useAppStore((s) => s.canvasItems);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const touchStartRef = useRef<{ x: number; y: number } | null>(null);

  const item: CanvasItem | undefined = useMemo(
    () => canvasItems.find((i) => i.path === itemPath),
    [canvasItems, itemPath],
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        onNavigate("prev");
        return;
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        onNavigate("next");
        return;
      }
      if (e.key !== "Tab") return;
      // Focus trap: keep Tab/Shift+Tab cycling within the lightbox.
      const container = containerRef.current;
      if (!container) return;
      const focusable = Array.from(
        container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      ).filter((el) => !el.hasAttribute("disabled"));
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey) {
        if (active === first || !container.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else if (active === last || !container.contains(active)) {
        e.preventDefault();
        first.focus();
      }
    },
    [onClose, onNavigate],
  );

  useEffect(() => {
    if (!itemPath) return;
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    window.addEventListener("keydown", handleKeyDown);
    // Move focus into the dialog once it mounts.
    const container = containerRef.current;
    const first = container?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
    first?.focus();
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      previouslyFocused.current?.focus();
    };
  }, [itemPath, handleKeyDown]);

  if (!itemPath || !item) return null;

  const SWIPE_THRESHOLD_PX = 50;

  const handleTouchStart = (e: React.TouchEvent) => {
    const t = e.touches[0];
    if (t) touchStartRef.current = { x: t.clientX, y: t.clientY };
  };

  const handleTouchEnd = (e: React.TouchEvent) => {
    const start = touchStartRef.current;
    touchStartRef.current = null;
    if (!start) return;
    const t = e.changedTouches[0];
    if (!t) return;
    const dx = t.clientX - start.x;
    const dy = t.clientY - start.y;
    // Horizontal swipes only — ignore vertical scroll gestures.
    if (Math.abs(dx) < SWIPE_THRESHOLD_PX || Math.abs(dx) <= Math.abs(dy)) return;
    onNavigate(dx < 0 ? "next" : "prev");
  };

  return (
    <div
      className="ka-lightbox-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Artifact preview"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="ka-lightbox-frame"
        ref={containerRef}
      >
        {/* Navigation arrows sit in the gutter, outside the canvas. */}
        <button
          type="button"
          className="absolute -left-14 top-1/2 z-10 inline-flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full border border-line bg-[var(--color-surface)]/90 text-muted shadow-lg backdrop-blur-sm hover:bg-[var(--color-surface)] hover:text-ink"
          aria-label="Previous artifact"
          onClick={() => onNavigate("prev")}
        >
          <svg aria-hidden="true" viewBox="0 0 20 20" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
            <path d="m12.5 4.5-5.5 5.5 5.5 5.5" />
          </svg>
        </button>
        <button
          type="button"
          className="absolute -right-14 top-1/2 z-10 inline-flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full border border-line bg-[var(--color-surface)]/90 text-muted shadow-lg backdrop-blur-sm hover:bg-[var(--color-surface)] hover:text-ink"
          aria-label="Next artifact"
          onClick={() => onNavigate("next")}
        >
          <svg aria-hidden="true" viewBox="0 0 20 20" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
            <path d="m7.5 4.5 5.5 5.5-5.5 5.5" />
          </svg>
        </button>

        <div
          className="ka-lightbox"
          onTouchStart={handleTouchStart}
          onTouchEnd={handleTouchEnd}
        >
        {/* Close button */}
        <button
          type="button"
          className="absolute right-4 top-4 z-10 inline-flex h-8 w-8 items-center justify-center rounded-md bg-[var(--color-surface)]/80 text-muted hover:text-ink"
          aria-label="Close"
          onClick={onClose}
        >
          ✕
        </button>

        <LightboxPreview item={item} />
        <LightboxSidebar item={item} />
        </div>
      </div>
    </div>
  );
}
