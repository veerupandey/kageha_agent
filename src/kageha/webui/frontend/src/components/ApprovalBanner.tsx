import { useEffect, useRef } from "react";
import { useAppStore } from "../store";

function detailText(detail: string | string[] | undefined): string {
  if (!detail) return "";
  if (Array.isArray(detail)) return detail.map(String).join("\n");
  return String(detail);
}

export function ApprovalBanner() {
  const pending = useAppStore((s) => s.pendingApproval);
  const resolveApproval = useAppStore((s) => s.resolveApproval);
  const openDrawer = useAppStore((s) => s.openDrawer);
  const loadDesign = useAppStore((s) => s.loadDesign);
  const approveRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (pending?.approval_id) {
      requestAnimationFrame(() => approveRef.current?.focus());
    }
  }, [pending?.approval_id]);

  if (!pending?.approval_id) return null;

  const isPlan =
    pending.risk_class === "plan" || pending.action === "approve_plan";
  const isClarify =
    pending.risk_class === "clarify" || pending.action === "spec_clarify";

  const title = isClarify
    ? "Clarify requirements"
    : isPlan
      ? "Approve plan to build"
      : pending.label || pending.action || "Approval needed";

  const detail = detailText(pending.detail);

  return (
    <div
      className="approval-banner"
      id="approval-banner"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="approval-banner-title"
      aria-describedby={detail ? "approval-detail" : undefined}
    >
      <div className="approval-banner-body">
        <p className="approval-title" id="approval-banner-title">
          {title}
        </p>
        {detail ? (
          <p className="approval-detail" id="approval-detail">
            {detail}
          </p>
        ) : null}
      </div>
      <div className="approval-actions">
        {isPlan || isClarify ? (
          <button
            type="button"
            className="btn ghost"
            id="btn-approval-view-plan"
            onClick={() => {
              openDrawer("design");
              void loadDesign({
                activeFile: isClarify ? "requirements.md" : "plan.md",
                awaitingClarify: isClarify,
                forceBuild: isPlan,
              });
            }}
          >
            View plan
          </button>
        ) : null}
        <button
          type="button"
          className="btn ghost"
          id="btn-approval-deny"
          onClick={() => {
            void resolveApproval(false);
          }}
        >
          Deny
        </button>
        <button
          ref={approveRef}
          type="button"
          className="btn primary"
          id="btn-approval-approve"
          onClick={() => {
            void resolveApproval(true);
          }}
        >
          {isClarify ? "Continue" : "Approve"}
        </button>
      </div>
    </div>
  );
}
