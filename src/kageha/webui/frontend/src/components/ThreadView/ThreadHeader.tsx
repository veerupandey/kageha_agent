import { useEffect, useRef, useState } from "react";
import { cn } from "../../lib/cn";
import { useAppStore } from "../../store";
import { Pill } from "../shared/Pill";
import { StatusDot } from "../shared/StatusDot";
import type { DotStatus } from "../shared/StatusDot";

export type ArtifactFilter = "all" | "images" | "webpages" | "documents";

interface ThreadHeaderProps {
  artifactFilter: ArtifactFilter;
  onFilterChange: (filter: ArtifactFilter) => void;
  artifactCounts: Record<ArtifactFilter, number>;
}

function runStatusToDot(status: string): DotStatus {
  if (status === "running" || status === "streaming") return "active";
  if (status === "error") return "error";
  if (status === "waiting_approval") return "waiting";
  return "idle";
}

export function ThreadHeader({
  artifactFilter,
  onFilterChange,
  artifactCounts,
}: ThreadHeaderProps) {
  const sessionTitle = useAppStore((s) => s.sessionTitle);
  const sessionId = useAppStore((s) => s.sessionId);
  const runStatus = useAppStore((s) => s.runStatus);
  const modelOverride = useAppStore((s) => s.modelOverride);
  const renameSession = useAppStore((s) => s.renameSession);
  const showToast = useAppStore((s) => s.showToast);
  const [editing, setEditing] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  const title = (sessionTitle && sessionTitle.trim()) || sessionId?.slice(0, 8) || "Thread";

  const commitTitle = async () => {
    setEditing(false);
    const next = titleDraft.trim();
    if (!sessionId || next === (sessionTitle || "")) return;
    try {
      await renameSession(next);
    } catch (err) {
      showToast(`Rename failed: ${err instanceof Error ? err.message : err}`);
    }
  };

  const filters: ArtifactFilter[] = ["all", "images", "webpages", "documents"];

  return (
    <header className="flex shrink-0 items-center gap-3 border-b border-[var(--color-line)] px-4 py-2.5">
      {/* Title + status */}
      <div className="flex min-w-0 flex-1 items-center gap-2.5">
        {editing ? (
          <input
            ref={inputRef}
            type="text"
            className="min-w-0 flex-1 rounded-md border border-[var(--color-line)] bg-[var(--color-surface)] px-2 py-1 text-sm text-ink outline-none focus:border-[var(--color-accent)]"
            value={titleDraft}
            onChange={(e) => setTitleDraft(e.target.value)}
            onBlur={() => void commitTitle()}
            onKeyDown={(e) => {
              if (e.key === "Enter") void commitTitle();
              if (e.key === "Escape") setEditing(false);
            }}
          />
        ) : (
          <button
            type="button"
            className="min-w-0 truncate text-left text-sm font-semibold text-ink"
            disabled={!sessionId}
            onClick={() => {
              setTitleDraft(sessionTitle || "");
              setEditing(true);
            }}
            title="Click to rename"
          >
            {title}
          </button>
        )}
        <StatusDot status={runStatusToDot(runStatus)} />
        {runStatus === "running" && (
          <span className="text-xs text-success">Live</span>
        )}
        {modelOverride && (
          <span className="rounded-md bg-[var(--color-surface)] px-2 py-0.5 text-[0.7rem] text-faint">
            {modelOverride}
          </span>
        )}
      </div>

      {/* Artifact filter tabs */}
      <nav className="flex items-center gap-1" role="tablist" aria-label="Artifact filters">
        {filters.map((f) => (
          <Pill
            key={f}
            active={artifactFilter === f}
            onClick={() => onFilterChange(f)}
            className="text-xs"
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
            {artifactCounts[f] > 0 && (
              <span className={cn("text-[0.65rem]", artifactFilter === f ? "text-accent" : "text-faint")}>
                ({artifactCounts[f]})
              </span>
            )}
          </Pill>
        ))}
      </nav>
    </header>
  );
}
