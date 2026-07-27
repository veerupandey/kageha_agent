import { useAppStore } from "../store";
import type { BonAttempt } from "../api/types";

function attemptList(
  bonLive: {
    attempts: BonAttempt[];
    placeholders: Record<number, BonAttempt>;
    n: number;
  } | null,
): BonAttempt[] {
  if (!bonLive) return [];
  const byIndex = new Map<number, BonAttempt>();
  for (const p of Object.values(bonLive.placeholders || {})) {
    byIndex.set(Number(p.index), p);
  }
  for (const a of bonLive.attempts || []) {
    byIndex.set(Number(a.index), { ...byIndex.get(Number(a.index)), ...a });
  }
  return Array.from({ length: bonLive.n }, (_, i) =>
    byIndex.get(i)
      ? byIndex.get(i)!
      : { index: i, label: `n${i + 1}`, status: "idle" },
  );
}

export function Workbench() {
  const open = useAppStore((s) => s.drawers.workbench);
  const workbenchTab = useAppStore((s) => s.workbenchTab);
  const setWorkbenchTab = useAppStore((s) => s.setWorkbenchTab);
  const closeDrawer = useAppStore((s) => s.closeDrawer);
  const bonObjective = useAppStore((s) => s.bonObjective);
  const bonN = useAppStore((s) => s.bonN);
  const bonLive = useAppStore((s) => s.bonLive);
  const reviewResult = useAppStore((s) => s.reviewResult);
  const runBestOfN = useAppStore((s) => s.runBestOfN);
  const runReview = useAppStore((s) => s.runReview);
  const showToast = useAppStore((s) => s.showToast);

  if (!open) return null;

  const attempts = attemptList(bonLive);

  return (
    <>
      <div
        className="workbench-resizer"
        id="workbench-resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize workbench"
        title="Drag to resize workbench"
        tabIndex={0}
      />
      <aside
        className="workbench"
        id="workbench"
        aria-hidden="false"
        aria-label="Stage workbench"
      >
        <header className="workbench-head">
          <div className="workbench-tabs" role="tablist" aria-label="Workbench panes">
            <button
              type="button"
              className={`workbench-tab${workbenchTab === "bon" ? " is-active" : ""}`}
              id="wb-tab-bon"
              role="tab"
              aria-selected={workbenchTab === "bon"}
              data-wb-tab="bon"
              onClick={() => setWorkbenchTab("bon")}
            >
              Best-of-N
            </button>
            <button
              type="button"
              className={`workbench-tab${workbenchTab === "review" ? " is-active" : ""}`}
              id="wb-tab-review"
              role="tab"
              aria-selected={workbenchTab === "review"}
              data-wb-tab="review"
              onClick={() => setWorkbenchTab("review")}
            >
              Review
            </button>
          </div>
          <button
            type="button"
            className="btn ghost"
            id="btn-close-workbench"
            aria-label="Close workbench"
            onClick={() => closeDrawer("workbench")}
          >
            Close
          </button>
        </header>

        <div className="workbench-body">
          <section
            className="workbench-pane"
            id="workbench-pane-bon"
            role="tabpanel"
            aria-labelledby="wb-tab-bon"
            hidden={workbenchTab !== "bon"}
          >
            <div className="workbench-form">
              <label className="labs-field">
                <span>Objective</span>
                <input
                  type="text"
                  id="labs-bon-objective"
                  placeholder="What should each attempt solve?"
                  value={bonObjective}
                  onChange={(e) =>
                    useAppStore.setState({ bonObjective: e.target.value })
                  }
                />
              </label>
              <label className="labs-field workbench-n-field">
                <span>Attempts</span>
                <input
                  type="number"
                  id="labs-bon-n"
                  min={2}
                  max={5}
                  value={bonN}
                  onChange={(e) =>
                    useAppStore.setState({
                      bonN: Math.max(
                        2,
                        Math.min(5, Number(e.target.value) || 2),
                      ),
                    })
                  }
                />
              </label>
              <button
                type="button"
                className="btn primary compact"
                id="wb-run-bon"
                onClick={() => {
                  void runBestOfN();
                }}
              >
                Run Best-of-N
              </button>
            </div>

            {attempts.length ? (
              <div className="parallel-panel" id="parallel-panel">
                {attempts.map((a) => {
                  const winner =
                    bonLive?.winner_index != null &&
                    Number(bonLive.winner_index) === Number(a.index);
                  return (
                    <article
                      key={a.index}
                      className={
                        "bon-attempt" +
                        (a.running ? " is-running" : "") +
                        (winner ? " is-winner" : "") +
                        (a.ok === false ? " is-failed" : "")
                      }
                    >
                      <header className="bon-attempt-head">
                        <strong>{a.label || `n${a.index + 1}`}</strong>
                        <span className="muted">
                          {a.status || (a.running ? "running" : "idle")}
                          {a.score != null ? ` · ${Number(a.score).toFixed(2)}` : ""}
                        </span>
                      </header>
                      {a.branch || a.worktree ? (
                        <p className="bon-attempt-meta muted">
                          {[a.branch, a.worktree].filter(Boolean).join(" · ")}
                        </p>
                      ) : null}
                      {a.error || a.message ? (
                        <p className="bon-attempt-msg">{a.error || a.message}</p>
                      ) : null}
                    </article>
                  );
                })}
              </div>
            ) : (
              <p className="workbench-empty" id="workbench-bon-empty">
                Live attempt cards appear here while Best-of-N runs.
              </p>
            )}
          </section>

          <section
            className="workbench-pane"
            id="workbench-pane-review"
            role="tabpanel"
            aria-labelledby="wb-tab-review"
            hidden={workbenchTab !== "review"}
          >
            <div className="workbench-form">
              <label className="labs-field">
                <span>Base ref</span>
                <input
                  type="text"
                  id="labs-review-base"
                  defaultValue="main"
                />
              </label>
              <button
                type="button"
                className="btn primary compact"
                id="wb-run-review"
                onClick={() => {
                  const base =
                    (
                      document.getElementById(
                        "labs-review-base",
                      ) as HTMLInputElement | null
                    )?.value.trim() || "main";
                  void runReview({ base }).catch((err: Error) =>
                    showToast(err.message || String(err)),
                  );
                }}
              >
                Review diff
              </button>
            </div>

            {reviewResult ? (
              <div className="diff-panel" id="diff-panel">
                {reviewResult.diff_stat ? (
                  <pre className="diff-stat">{reviewResult.diff_stat}</pre>
                ) : null}
                {reviewResult.message ? (
                  <p>{reviewResult.message}</p>
                ) : null}
                {(reviewResult.findings || []).map((f, i) => (
                  <article key={i} className="review-finding">
                    <strong>{f.severity || "info"}</strong>
                    <p>{f.summary || ""}</p>
                  </article>
                ))}
                {!reviewResult.findings?.length &&
                !reviewResult.diff_stat &&
                !reviewResult.message ? (
                  <p className="muted">Review complete · no findings.</p>
                ) : null}
              </div>
            ) : (
              <p className="workbench-empty" id="workbench-review-empty">
                Diff and findings open here after /review.
              </p>
            )}
          </section>
        </div>
      </aside>
    </>
  );
}
