import { useMemo } from "react";
import type { CanvasItem } from "../../lib/artifactMedia";
import { useAppStore } from "../../store";
import type { ArtifactFilter } from "./ThreadHeader";
import { ArtifactThumb } from "./ArtifactThumb";

interface ArtifactPanelProps {
  filter: ArtifactFilter;
  onOpenLightbox: (path: string) => void;
}

function matchesFilter(item: CanvasItem, filter: ArtifactFilter): boolean {
  if (filter === "all") return true;
  if (filter === "images") return item.kind === "image";
  if (filter === "documents") return item.kind === "pdf" || item.kind === "document" || item.kind === "text" || item.kind === "spreadsheet" || item.kind === "presentation";
  if (filter === "webpages") return item.kind === "markdown";
  return true;
}

export function ArtifactPanel({ filter, onOpenLightbox }: ArtifactPanelProps) {
  const canvasItems = useAppStore((s) => s.canvasItems);

  const filtered = useMemo(
    () => canvasItems.filter((item) => matchesFilter(item, filter)),
    [canvasItems, filter],
  );

  if (filtered.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-6 py-12 text-center">
        <p className="text-3xl" aria-hidden="true">📂</p>
        <p className="mt-2 text-sm text-muted">No artifacts yet</p>
        <p className="mt-1 text-xs text-faint">
          Generated images, documents, and web captures will appear here
        </p>
      </div>
    );
  }

  // Group by kind for display
  const imageCount = canvasItems.filter((i) => i.kind === "image").length;
  const docCount = canvasItems.filter(
    (i) => i.kind === "pdf" || i.kind === "document" || i.kind === "text" || i.kind === "spreadsheet",
  ).length;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-4">
      {/* Group badges */}
      {filter === "all" && (imageCount > 0 || docCount > 0) && (
        <div className="mb-3 flex items-center gap-2">
          {imageCount > 0 && (
            <span className="rounded-md bg-[var(--color-accent-soft)] px-2 py-0.5 text-xs text-accent">
              {imageCount} Images
            </span>
          )}
          {docCount > 0 && (
            <span className="rounded-md bg-[var(--color-surface)] px-2 py-0.5 text-xs text-muted">
              {docCount} Documents
            </span>
          )}
        </div>
      )}

      {/* Grid */}
      <div className="ka-artifact-grid">
        {filtered.map((item) => (
          <ArtifactThumb
            key={item.path}
            item={item}
            onClick={() => onOpenLightbox(item.path)}
          />
        ))}
      </div>
    </div>
  );
}
