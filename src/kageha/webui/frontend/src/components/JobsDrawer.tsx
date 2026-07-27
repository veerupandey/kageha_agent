import { useEffect, useRef, useState } from "react";
import { useFocusTrap } from "../lib/focusTrap";
import { useAppStore } from "../store";

const FILTERS: { id: string; label: string }[] = [
  { id: "", label: "All" },
  { id: "active", label: "Active" },
  { id: "queued", label: "Queued" },
  { id: "running", label: "Running" },
  { id: "done", label: "Done" },
];

function formatCounts(counts: {
  queued?: number;
  running?: number;
  done?: number;
} | null): string {
  if (!counts) return "Queued · running · done from durable job files.";
  return `Queued ${counts.queued || 0} · running ${counts.running || 0} · done ${
    counts.done || 0
  }`;
}

export function JobsDrawer() {
  const open = useAppStore((s) => s.drawers.jobs);
  const jobs = useAppStore((s) => s.jobs);
  const jobsCounts = useAppStore((s) => s.jobsCounts);
  const jobsFilter = useAppStore((s) => s.jobsFilter);
  const jobsLoading = useAppStore((s) => s.jobsLoading);
  const closeDrawer = useAppStore((s) => s.closeDrawer);
  const refreshJobs = useAppStore((s) => s.refreshJobs);
  const setJobsFilter = useAppStore((s) => s.setJobsFilter);
  const cancelJob = useAppStore((s) => s.cancelJob);
  const attachJob = useAppStore((s) => s.attachJob);
  const createJob = useAppStore((s) => s.createJob);
  const showToast = useAppStore((s) => s.showToast);
  const drawerRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [newObjective, setNewObjective] = useState("");
  const [creating, setCreating] = useState(false);

  useFocusTrap(open, drawerRef, { initialFocusRef: closeRef });

  useEffect(() => {
    if (open) void refreshJobs().catch(() => {});
  }, [open, refreshJobs]);

  const submitNewJob = async () => {
    const objective = newObjective.trim();
    if (!objective || creating) return;
    setCreating(true);
    try {
      await createJob(objective);
      setNewObjective("");
    } catch (err) {
      showToast(err instanceof Error ? err.message : String(err));
    } finally {
      setCreating(false);
    }
  };

  return (
    <aside
      ref={drawerRef}
      className={`jobs drawer${open ? " open" : ""}`}
      id="jobs-drawer"
      aria-hidden={open ? "false" : "true"}
    >
      <header className="jobs-head">
        <div>
          <p className="eyebrow">Background</p>
          <h2 className="drawer-title">Jobs</h2>
        </div>
        <div className="jobs-head-actions">
          <button
            type="button"
            className="btn ghost compact"
            id="btn-jobs-refresh"
            title="Refresh"
            onClick={() => {
              void refreshJobs();
            }}
          >
            Refresh
          </button>
          <button
            ref={closeRef}
            type="button"
            className="btn ghost"
            id="btn-close-jobs"
            aria-label="Close"
            onClick={() => closeDrawer("jobs")}
          >
            Close
          </button>
        </div>
      </header>

      <p className="jobs-lede" id="jobs-counts">
        {jobsLoading ? "Loading…" : formatCounts(jobsCounts)}
      </p>

      <div className="jobs-filters" role="tablist" aria-label="Job status filter">
        {FILTERS.map((f) => (
          <button
            key={f.id || "all"}
            type="button"
            className={`jobs-filter${jobsFilter === f.id ? " is-active" : ""}`}
            data-jobs-filter={f.id}
            role="tab"
            aria-selected={jobsFilter === f.id}
            onClick={() => setJobsFilter(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>

      <ul className="jobs-list" id="jobs-list" aria-live="polite">
        {jobs.map((job) => (
          <li key={job.id} className="jobs-item" data-status={job.status || ""}>
            <div className="jobs-item-main">
              <span className="jobs-item-id mono">{job.id.slice(0, 10)}</span>
              <span className="jobs-item-status">{job.status || "unknown"}</span>
            </div>
            <p className="jobs-item-objective">
              {job.objective || "(no objective)"}
            </p>
            <div className="jobs-item-actions">
              {job.attachable !== false && job.session_id ? (
                <button
                  type="button"
                  className="btn ghost compact"
                  onClick={() => {
                    void attachJob(job.id).catch((err: Error) =>
                      showToast(err.message || String(err)),
                    );
                  }}
                >
                  Attach
                </button>
              ) : null}
              {job.can_cancel !== false &&
              (job.status === "queued" || job.status === "running") ? (
                <button
                  type="button"
                  className="btn ghost compact"
                  onClick={() => {
                    const ok = window.confirm(
                      `Cancel job ${job.id.slice(0, 8)}?`,
                    );
                    if (!ok) return;
                    void cancelJob(job.id).catch((err: Error) =>
                      showToast(err.message || String(err)),
                    );
                  }}
                >
                  Cancel
                </button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>

      <div className="jobs-foot">
        <form
          className="jobs-new-form"
          onSubmit={(e) => {
            e.preventDefault();
            void submitNewJob();
          }}
        >
          <label className="sr-only" htmlFor="jobs-new-input">
            New job objective
          </label>
          <input
            id="jobs-new-input"
            type="text"
            className="jobs-new-input"
            placeholder="Background job objective…"
            value={newObjective}
            onChange={(e) => setNewObjective(e.target.value)}
            autoComplete="off"
          />
          <button
            type="submit"
            className="btn primary compact"
            id="btn-jobs-new"
            disabled={!newObjective.trim() || creating}
          >
            {creating ? "Creating…" : "New job"}
          </button>
        </form>
      </div>
    </aside>
  );
}
