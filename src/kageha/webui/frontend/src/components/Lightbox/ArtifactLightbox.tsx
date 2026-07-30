import { useCallback, useEffect, useMemo } from "react";
import type { CanvasItem } from "../../lib/artifactMedia";
import { useAppStore } from "../../store";
import { LightboxPreview } from "./LightboxPreview";
import { LightboxSidebar } from "./LightboxSidebar";

interface ArtifactLightboxProps {
  itemPath: string | null;
  onClose: () => void;
  onNavigate: (direction: "prev" | "next") => void;
}

export function ArtifactLightbox({ itemPath, onClose, onNavigate }: ArtifactLightboxProps) {
  const canvasItems = useAppStore((s) => s.canvasItems);

  const item: CanvasItem | undefined = useMemo(
    () => canvasItems.find((i) => i.path === itemPath),
    [canvasItems, itemPath],
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        onNavigate("prev");
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        onNavigate("next");
      }
    },
    [onClose, onNavigate],
  );

  useEffect(() => {
    if (!itemPath) return;
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [itemPath, handleKeyDown]);

  if (!itemPath || !item) return null;

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
      <div className="ka-lightbox">
        {/* Close button */}
        <button
          type="button"
          className="absolute right-4 top-4 z-10 inline-flex h-8 w-8 items-center justify-center rounded-md bg-[var(--color-surface)]/80 text-muted hover:text-ink"
          aria-label="Close"
          onClick={onClose}
        >
          ✕
        </button>

        {/* Navigation arrows */}
        <button
          type="button"
          className="absolute left-4 top-1/2 z-10 inline-flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full bg-[var(--color-surface)]/80 text-lg text-muted hover:text-ink"
          aria-label="Previous artifact"
          onClick={() => onNavigate("prev")}
        >
          ←
        </button>
        <button
          type="button"
          className="absolute right-[296px] top-1/2 z-10 inline-flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full bg-[var(--color-surface)]/80 text-lg text-muted hover:text-ink"
          aria-label="Next artifact"
          onClick={() => onNavigate("next")}
        >
          →
        </button>

        <LightboxPreview item={item} />
        <LightboxSidebar item={item} />
      </div>
    </div>
  );
}
