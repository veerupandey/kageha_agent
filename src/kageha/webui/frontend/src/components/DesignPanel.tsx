import { useEffect } from "react";
import { useAppStore } from "../store";

export function DesignPanel() {
  const open = useAppStore((s) => s.drawers.design);
  const design = useAppStore((s) => s.design);
  const closeDrawer = useAppStore((s) => s.closeDrawer);
  const loadDesign = useAppStore((s) => s.loadDesign);
  const saveDesign = useAppStore((s) => s.saveDesign);
  const buildDesign = useAppStore((s) => s.buildDesign);
  const setDesignFile = useAppStore((s) => s.setDesignFile);
  const setDesignActiveFile = useAppStore((s) => s.setDesignActiveFile);
  const resolveApproval = useAppStore((s) => s.resolveApproval);
  const showToast = useAppStore((s) => s.showToast);

  useEffect(() => {
    if (open) {
      void loadDesign().catch((err: Error) =>
        showToast(err.message || String(err)),
      );
    }
  }, [open, loadDesign, showToast]);

  if (!open) return null;

  const fileNames = Object.keys(design.files);
  const active = design.activeFile || fileNames[0] || "plan.md";
  const content = design.files[active] ?? "";

  return (
    <aside
      className="design-panel"
      id="design-panel"
      aria-label="Plan and Spec design"
    >
      <header className="design-panel-head">
        <div>
          <p className="eyebrow" id="design-panel-eyebrow">
            Design
          </p>
          <h2 className="drawer-title" id="design-panel-title">
            {String(design.agentMode || "plan")
              .replace(/^\w/, (c) => c.toUpperCase())}
          </h2>
        </div>
        <div className="design-panel-head-actions">
          <button
            type="button"
            className="btn ghost compact"
            id="btn-close-design"
            aria-label="Close design panel"
            onClick={() => closeDrawer("design")}
          >
            Close
          </button>
        </div>
      </header>

      <div
        className="design-tabs"
        id="design-tabs"
        role="tablist"
        aria-label="Design artifacts"
      >
        {fileNames.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            className={`design-tab${name === active ? " is-active" : ""}`}
            aria-selected={name === active}
            onClick={() => setDesignActiveFile(name)}
          >
            {name}
          </button>
        ))}
      </div>

      {design.exploreDegraded || design.exploreStatus ? (
        <div
          className="design-explore-banner"
          id="design-explore-banner"
          role="status"
        >
          {design.exploreDegraded
            ? "Explore degraded — plan may be incomplete."
            : String(
                (design.exploreStatus as { message?: string })?.message ||
                  "Explore status available",
              )}
        </div>
      ) : null}

      <textarea
        className="design-body md"
        id="design-body"
        value={content}
        spellCheck={false}
        onChange={(e) => setDesignFile(active, e.target.value)}
      />

      <footer className="design-panel-foot" id="design-panel-foot">
        <p className="design-foot-hint" id="design-foot-hint">
          Edit the plan, Save, then Build. Saves write session plan.md.
          {design.dirty ? " · unsaved" : ""}
          {design.saving ? " · saving…" : ""}
        </p>
        <div className="design-foot-actions">
          <button
            type="button"
            className="btn ghost"
            id="btn-design-save"
            disabled={design.saving}
            onClick={() => {
              void saveDesign({ force: true }).catch((err: Error) =>
                showToast(err.message || String(err)),
              );
            }}
          >
            Save
          </button>
          <button
            type="button"
            className="btn ghost"
            id="btn-design-deny"
            onClick={() => {
              if (design.awaitingBuild) {
                const ok = window.confirm(
                  "Deny the plan waiting for Build? This stops the build path.",
                );
                if (!ok) return;
              }
              void resolveApproval(false);
              closeDrawer("design");
            }}
          >
            Deny
          </button>
          <button
            type="button"
            className="btn primary"
            id="btn-design-build"
            onClick={() => {
              void buildDesign().catch((err: Error) =>
                showToast(err.message || String(err)),
              );
            }}
          >
            Build
          </button>
        </div>
      </footer>
    </aside>
  );
}
