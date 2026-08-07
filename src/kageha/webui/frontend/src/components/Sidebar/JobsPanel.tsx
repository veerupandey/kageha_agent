import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { cn } from "../../lib/cn";
import { useAppStore } from "../../store";

interface JobItem {
  id: string;
  objective: string;
  status: string;
  bucket: string;
  can_cancel: boolean;
  attachable: boolean;
  session_id?: string;
  created_at: number;
  updated_at: number;
  agent_mode: string;
  message?: string;
  error?: string;
  pr_url?: string;
}

interface JobsResponse {
  jobs?: JobItem[];
  items?: JobItem[];
}

function statusColor(bucket: string): string {
  if (bucket === "running" || bucket === "queued") return "text-accent";
  if (bucket === "paused") return "text-[var(--color-warn)]";
  if (bucket === "done") return "text-muted";
  return "text-muted";
}

function statusBg(bucket: string): string {
  if (bucket === "running" || bucket === "queued") return "bg-accent/15";
  if (bucket === "paused") return "bg-[var(--color-warn-soft)]";
  return "bg-line/50";
}

function timeAgo(ts: number): string {
  const diff = Date.now() - ts * 1000;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function JobsPanel({ onClose }: { onClose: () => void }) {
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [objective, setObjective] = useState("");
  const [agentMode, setAgentMode] = useState("plan");
  const [creating, setCreating] = useState(false);

  const fetchJobs = useCallback(async () => {
    try {
      const data = await api<JobsResponse>("/api/jobs?limit=20");
      setJobs(data.jobs || data.items || []);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchJobs();
    const interval = setInterval(fetchJobs, 5000);
    return () => clearInterval(interval);
  }, [fetchJobs]);

  const handleCreate = useCallback(async () => {
    if (!objective.trim()) return;
    setCreating(true);
    try {
      await api("/api/jobs", {
        method: "POST",
        body: JSON.stringify({
          objective: objective.trim(),
          agent_mode: agentMode,
        }),
      });
      setObjective("");
      setShowCreate(false);
      await fetchJobs();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to create job");
    } finally {
      setCreating(false);
    }
  }, [objective, agentMode, fetchJobs]);

  const handleCancel = useCallback(
    async (id: string) => {
      try {
        await api(`/api/jobs/${id}/cancel`, { method: "POST" });
        await fetchJobs();
      } catch {
        /* ignore */
      }
    },
    [fetchJobs],
  );

  const openSession = useAppStore((s) => s.openSession);

  const handleAttach = useCallback(
    async (job: JobItem) => {
      const sessionId = job.session_id || job.id;
      try {
        // Call attach endpoint to ensure session is ready
        await api(`/api/jobs/${job.id}/attach`);
      } catch {
        /* proceed anyway — session may still be loadable */
      }
      await openSession(sessionId);
      onClose();
    },
    [openSession, onClose],
  );

  return (
    <div className="flex h-full flex-col bg-surface">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold text-ink">Jobs</h2>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="rounded-md bg-accent/15 px-2.5 py-1 text-xs font-medium text-accent hover:bg-accent/25 transition-colors"
            onClick={() => setShowCreate(!showCreate)}
          >
            + New
          </button>
          <button
            type="button"
            className="rounded-md px-2 py-1 text-xs text-muted hover:text-ink hover:bg-line/50"
            onClick={onClose}
          >
            ✕
          </button>
        </div>
      </div>

      {/* Create form */}
      {showCreate && (
        <div className="border-b border-line px-4 py-3 space-y-2">
          <textarea
            className="w-full rounded-md border border-line bg-canvas px-3 py-2 text-sm text-ink placeholder:text-faint resize-none focus:border-accent focus:outline-none"
            rows={2}
            placeholder="What should the agent do?"
            value={objective}
            onChange={(e) => setObjective(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                void handleCreate();
              }
            }}
          />
          <div className="flex items-center justify-between">
            <select
              className="rounded-md border border-line bg-canvas px-2 py-1 text-xs text-ink"
              value={agentMode}
              onChange={(e) => setAgentMode(e.target.value)}
            >
              <option value="plan">Plan mode</option>
              <option value="goal">Goal mode</option>
              <option value="normal">Normal mode</option>
            </select>
            <button
              type="button"
              className="rounded-md bg-accent px-3 py-1 text-xs font-medium text-white hover:bg-accent/80 disabled:opacity-50"
              disabled={!objective.trim() || creating}
              onClick={handleCreate}
            >
              {creating ? "Starting…" : "Start Job"}
            </button>
          </div>
        </div>
      )}

      {/* Job list */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-8 text-sm text-muted">
            Loading…
          </div>
        ) : jobs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-center px-4">
            <p className="text-sm text-muted">No jobs yet</p>
            <p className="mt-1 text-xs text-faint">
              Jobs run in the background — create one to get started
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-line">
            {jobs.map((job) => (
              <li key={job.id} className="px-4 py-3 hover:bg-line/30 transition-colors">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-ink truncate">
                      {job.objective || job.id}
                    </p>
                    <div className="mt-1 flex items-center gap-2">
                      <span
                        className={cn(
                          "rounded px-1.5 py-0.5 text-[0.6rem] font-medium",
                          statusBg(job.bucket),
                          statusColor(job.bucket),
                        )}
                      >
                        {job.status}
                      </span>
                      <span className="text-[0.6rem] text-faint">
                        {timeAgo(job.updated_at)}
                      </span>
                      <span className="text-[0.6rem] text-faint">
                        {job.agent_mode}
                      </span>
                    </div>
                    {job.error && (
                      <p className="mt-1 text-xs text-[var(--color-danger)] truncate">
                        {job.error}
                      </p>
                    )}
                    {job.pr_url && (
                      <a
                        href={job.pr_url}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-1 block text-xs text-accent hover:underline truncate"
                      >
                        {job.pr_url}
                      </a>
                    )}
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1">
                    {job.can_cancel && (
                      <button
                        type="button"
                        className="rounded px-2 py-1 text-[0.65rem] text-[var(--color-danger)] hover:bg-[var(--color-danger-soft)]"
                        onClick={() => handleCancel(job.id)}
                      >
                        Cancel
                      </button>
                    )}
                    <button
                      type="button"
                      className="rounded px-2 py-1 text-[0.65rem] text-accent hover:bg-accent/15"
                      onClick={() => void handleAttach(job)}
                    >
                      {job.bucket === "running" ? "Attach" : "View"}
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
