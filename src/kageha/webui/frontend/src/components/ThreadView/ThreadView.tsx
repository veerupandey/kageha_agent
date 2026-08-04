import { useCallback, useMemo, useState } from "react";
import { useAppStore } from "../../store";
import { AgentCanvas } from "../AgentCanvas";
import { ArtifactPanel } from "./ArtifactPanel";
import { ConversationPanel } from "./ConversationPanel";
import type { ArtifactFilter } from "./ThreadHeader";
import { ThreadHeader } from "./ThreadHeader";

interface ThreadViewProps {
  onOpenLightbox: (path: string) => void;
  rightPanelCollapsed?: boolean;
  onToggleRightPanel?: () => void;
}

export function ThreadView({ onOpenLightbox, rightPanelCollapsed, onToggleRightPanel }: ThreadViewProps) {
  const canvasItems = useAppStore((s) => s.canvasItems);
  const canvasOpen = useAppStore((s) => s.canvasOpen);
  const refreshArtifacts = useAppStore((s) => s.refreshArtifacts);
  const runStatus = useAppStore((s) => s.runStatus);
  const [artifactFilter, setArtifactFilter] = useState<ArtifactFilter>("all");

  // Count artifacts by type
  const artifactCounts = useMemo(() => {
    const counts: Record<ArtifactFilter, number> = {
      all: canvasItems.length,
      images: 0,
      video: 0,
      webpages: 0,
      documents: 0,
      code: 0,
    };
    for (const item of canvasItems) {
      if (item.kind === "image") counts.images++;
      else if (item.kind === "video") counts.video++;
      else if (item.kind === "markdown" || item.kind === "webpage") counts.webpages++;
      else if (item.kind === "code") counts.code++;
      else if (item.kind === "pdf" || item.kind === "document" || item.kind === "text" || item.kind === "spreadsheet" || item.kind === "presentation") counts.documents++;
    }
    return counts;
  }, [canvasItems]);

  const handleFilterChange = useCallback((filter: ArtifactFilter) => {
    setArtifactFilter(filter);
    if (filter !== "all") void refreshArtifacts();
  }, [refreshArtifacts]);

  // Show the agent canvas (timeline/stats) ONLY while actively running.
  // Once done, show the polished ArtifactPanel file browser.
  const showAgentCanvas = runStatus === "running" || (canvasOpen && runStatus !== "idle" && runStatus !== "success");

  return (
    <main className="ka-thread-view flex min-h-0 min-w-0 flex-1 flex-col" id="stage">
      <ThreadHeader
        artifactFilter={artifactFilter}
        onFilterChange={handleFilterChange}
        artifactCounts={artifactCounts}
      />

      <div className="flex min-h-0 flex-1">
        {/* Conversation panel (left) — expands to full width when right panel is collapsed */}
        <div className={`flex min-h-0 min-w-0 flex-1 flex-col border-r border-[var(--color-line)] ${rightPanelCollapsed ? "" : "md:max-w-[60%] lg:max-w-[50%]"}`}>
          <ConversationPanel />
        </div>

        {/* Right panel: collapsed rail or full panel */}
        {rightPanelCollapsed ? (
          <div className="hidden md:flex flex-col items-center w-10 border-l border-[var(--color-line)] bg-surface py-2">
            <button
              type="button"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-faint hover:bg-line/50 hover:text-ink transition-colors"
              aria-label="Expand right panel"
              title="Expand right panel"
              onClick={onToggleRightPanel}
            >
              ⊞
            </button>
            {canvasItems.length > 0 && (
              <span className="mt-2 rounded-full bg-accent/20 px-1.5 py-0.5 text-[0.6rem] text-accent font-medium">
                {canvasItems.length}
              </span>
            )}
          </div>
        ) : (
          <div className="hidden min-h-0 flex-1 flex-col md:flex">
            {showAgentCanvas ? (
              <AgentCanvas alwaysShow onCollapse={onToggleRightPanel} />
            ) : (
              <ArtifactPanel
                filter={artifactFilter}
                onOpenLightbox={onOpenLightbox}
                onCollapse={onToggleRightPanel}
              />
            )}
          </div>
        )}
      </div>
    </main>
  );
}
