import { useCallback, useMemo, useState } from "react";
import { useAppStore } from "../../store";
import { AgentCanvas } from "../AgentCanvas";
import { ArtifactPanel } from "./ArtifactPanel";
import { ConversationPanel } from "./ConversationPanel";
import type { ArtifactFilter } from "./ThreadHeader";
import { ThreadHeader } from "./ThreadHeader";

interface ThreadViewProps {
  onOpenLightbox: (path: string) => void;
}

export function ThreadView({ onOpenLightbox }: ThreadViewProps) {
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
      webpages: 0,
      documents: 0,
    };
    for (const item of canvasItems) {
      if (item.kind === "image") counts.images++;
      else if (item.kind === "markdown") counts.webpages++;
      else if (item.kind === "pdf" || item.kind === "document" || item.kind === "text" || item.kind === "spreadsheet" || item.kind === "presentation") counts.documents++;
    }
    return counts;
  }, [canvasItems]);

  const handleFilterChange = useCallback((filter: ArtifactFilter) => {
    setArtifactFilter(filter);
    if (filter !== "all") void refreshArtifacts();
  }, [refreshArtifacts]);

  // Show the agent canvas (timeline/stats) when running, when user toggled it, or when there are tool cards
  const messages = useAppStore((s) => s.messages);
  const hasActivity = useMemo(() => {
    return messages.some((m) => m.role === "assistant" && ((m.toolCards && m.toolCards.length > 0) || (m.steps && m.steps.length > 0)));
  }, [messages]);
  const showAgentCanvas = runStatus === "running" || canvasOpen || hasActivity;

  return (
    <main className="flex min-h-0 min-w-0 flex-1 flex-col" id="stage">
      <ThreadHeader
        artifactFilter={artifactFilter}
        onFilterChange={handleFilterChange}
        artifactCounts={artifactCounts}
      />

      <div className="flex min-h-0 flex-1">
        {/* Conversation panel (left) */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col border-r border-[var(--color-line)] lg:max-w-[50%]">
          <ConversationPanel />
        </div>

        {/* Right panel: Agent Canvas (live activity) OR Artifact Panel */}
        <div className="hidden min-h-0 flex-1 flex-col lg:flex">
          {showAgentCanvas ? (
            <AgentCanvas alwaysShow />
          ) : (
            <ArtifactPanel
              filter={artifactFilter}
              onOpenLightbox={onOpenLightbox}
            />
          )}
        </div>
      </div>
    </main>
  );
}
