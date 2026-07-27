import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useFocusTrap } from "../lib/focusTrap";
import { useAppStore } from "../store";

interface WorktreeInfo {
  path?: string;
  branch?: string;
  label?: string;
  [key: string]: unknown;
}

export function LabsDrawer() {
  const open = useAppStore((s) => s.drawers.labs);
  const meta = useAppStore((s) => s.meta);
  const jobs = useAppStore((s) => s.jobs);
  const closeDrawer = useAppStore((s) => s.closeDrawer);
  const openDrawer = useAppStore((s) => s.openDrawer);
  const setWorkbenchTab = useAppStore((s) => s.setWorkbenchTab);
  const runBestOfN = useAppStore((s) => s.runBestOfN);
  const runReview = useAppStore((s) => s.runReview);
  const createJob = useAppStore((s) => s.createJob);
  const refreshJobs = useAppStore((s) => s.refreshJobs);
  const loadMeta = useAppStore((s) => s.loadMeta);
  const showToast = useAppStore((s) => s.showToast);

  const [worktrees, setWorktrees] = useState<WorktreeInfo[]>([]);
  const drawerRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useFocusTrap(open, drawerRef, { initialFocusRef: closeRef });

  const refresh = useCallback(async () => {
    await loadMeta();
    await refreshJobs().catch(() => {});
    try {
      const data = await api<{ worktrees?: WorktreeInfo[]; items?: WorktreeInfo[] }>(
        "/api/worktrees",
      );
      setWorktrees(data.worktrees || data.items || []);
    } catch {
      setWorktrees([]);
    }
  }, [loadMeta, refreshJobs]);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  const projectJson = JSON.stringify(
    meta?.project || {
      root: meta?.project_root || "(unknown)",
      brand: meta?.brand || "Kageha",
      features: meta?.features || {},
    },
    null,
    2,
  );

  return (
    <aside
      ref={drawerRef}
      className={`labs drawer${open ? " open" : ""}`}
      id="labs-drawer"
      aria-hidden={open ? "false" : "true"}
    >
      <header className="labs-head">
        <div>
          <p className="eyebrow">Labs</p>
          <h2 className="drawer-title">Project · review · BoN</h2>
        </div>
        <button
          ref={closeRef}
          type="button"
          className="btn ghost"
          id="btn-close-labs"
          aria-label="Close"
          onClick={() => closeDrawer("labs")}
        >
          Close
        </button>
      </header>

      <section className="labs-section">
        <h3>Project brain</h3>
        <pre className="labs-pre" id="labs-project">
          {projectJson}
        </pre>
      </section>

      <section className="labs-section">
        <h3>Best-of-N</h3>
        <p className="labs-hint">
          Runs in the stage workbench with live attempt cards.
        </p>
        <div className="labs-actions">
          <button
            type="button"
            className="btn ghost"
            id="labs-focus-bon"
            onClick={() => {
              setWorkbenchTab("bon");
              openDrawer("workbench");
            }}
          >
            Open workbench
          </button>
          <button
            type="button"
            className="btn primary compact"
            id="labs-best-of-n"
            onClick={() => {
              setWorkbenchTab("bon");
              openDrawer("workbench");
              void runBestOfN();
            }}
          >
            Run Best-of-N
          </button>
        </div>
      </section>

      <section className="labs-section">
        <h3>Review</h3>
        <p className="labs-hint">Diff panel opens in the stage workbench.</p>
        <div className="labs-actions">
          <button
            type="button"
            className="btn ghost"
            id="labs-focus-review"
            onClick={() => {
              setWorkbenchTab("review");
              openDrawer("workbench");
            }}
          >
            Open workbench
          </button>
          <button
            type="button"
            className="btn primary compact"
            id="labs-review"
            onClick={() => {
              setWorkbenchTab("review");
              openDrawer("workbench");
              void runReview().catch((err: Error) =>
                showToast(err.message || String(err)),
              );
            }}
          >
            Review diff
          </button>
        </div>
      </section>

      <section className="labs-section">
        <h3>Actions</h3>
        <div className="labs-actions">
          <button
            type="button"
            className="btn ghost"
            id="labs-babysit"
            onClick={() => {
              const pr = window.prompt("PR number or URL to babysit:");
              if (!pr?.trim()) return;
              void api("/api/babysit", {
                method: "POST",
                body: JSON.stringify({ pr: pr.trim(), max_rounds: 3 }),
              })
                .then((result) => {
                  showToast(
                    `Babysit ${String((result as { status?: string }).status || "done")}`,
                  );
                })
                .catch((err: Error) => showToast(err.message || String(err)));
            }}
          >
            Babysit PR
          </button>
          <button
            type="button"
            className="btn ghost"
            id="labs-job"
            onClick={() => {
              const objective = window.prompt("Background job objective:");
              if (!objective?.trim()) return;
              void createJob(objective.trim()).catch((err: Error) =>
                showToast(err.message || String(err)),
              );
            }}
          >
            Cloud job
          </button>
          <button
            type="button"
            className="btn ghost"
            id="labs-worktree"
            onClick={() => {
              const label = window.prompt("Worktree label", "agent") || "agent";
              void api<{ path?: string; branch?: string }>("/api/worktrees", {
                method: "POST",
                body: JSON.stringify({ label }),
              })
                .then((result) => {
                  showToast(
                    `Worktree ${result.branch || label} · ${result.path || "ready"}`,
                  );
                  void refresh();
                })
                .catch((err: Error) => showToast(err.message || String(err)));
            }}
          >
            New worktree
          </button>
          <button
            type="button"
            className="btn ghost"
            id="labs-refresh"
            onClick={() => {
              void refresh();
            }}
          >
            Refresh
          </button>
        </div>
      </section>

      <section className="labs-section">
        <h3>Jobs</h3>
        <p className="labs-hint">
          Full dashboard: stage-bar <strong>Jobs</strong> (reconnect + cancel).
        </p>
        <ul className="labs-list" id="labs-jobs">
          {jobs.slice(0, 8).map((j) => (
            <li key={j.id}>
              <span className="mono">{j.id.slice(0, 8)}</span> ·{" "}
              {j.status || "?"} · {j.objective || ""}
            </li>
          ))}
        </ul>
        <div className="labs-actions">
          <button
            type="button"
            className="btn ghost"
            id="labs-open-jobs"
            onClick={() => openDrawer("jobs")}
          >
            Open Jobs panel
          </button>
        </div>
      </section>

      <section className="labs-section">
        <h3>Worktrees</h3>
        <ul className="labs-list" id="labs-worktrees">
          {!worktrees.length ? (
            <li className="muted">No worktrees listed.</li>
          ) : (
            worktrees.map((wt, i) => (
              <li key={String(wt.path || wt.branch || i)}>
                {wt.label || wt.branch || "worktree"}
                {wt.path ? ` · ${wt.path}` : ""}
              </li>
            ))
          )}
        </ul>
      </section>
    </aside>
  );
}
