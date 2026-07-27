import { useEffect, useRef, useState } from "react";
import { useAppStore } from "../store";

function detailText(detail: string | string[] | undefined): string {
  if (!detail) return "";
  if (Array.isArray(detail)) return detail.map(String).join("\n");
  return String(detail);
}

export function ApprovalBanner() {
  const pending = useAppStore((s) => s.pendingApproval);
  const resolveApproval = useAppStore((s) => s.resolveApproval);
  const approveRef = useRef<HTMLButtonElement>(null);
  const [suggestion, setSuggestion] = useState("");

  useEffect(() => {
    setSuggestion("");
    if (pending?.approval_id) {
      requestAnimationFrame(() => approveRef.current?.focus());
    }
  }, [pending?.approval_id]);

  if (!pending?.approval_id) return null;

  const isPlan =
    pending.risk_class === "plan" || pending.action === "approve_plan";
  const title = isPlan
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
        <label className="approval-suggest-label" htmlFor="approval-suggest">
          Suggest (optional)
        </label>
        <input
          id="approval-suggest"
          className="approval-suggest-input"
          type="text"
          value={suggestion}
          placeholder={
            isPlan
              ? "e.g. prefer Redis; skip step 3…"
              : "e.g. use a safer command…"
          }
          onChange={(e) => setSuggestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && suggestion.trim()) {
              e.preventDefault();
              void resolveApproval(false, suggestion.trim());
            }
          }}
        />
      </div>
      <div className="approval-actions">
        {isPlan ? (
          <button
            type="button"
            className="btn ghost"
            id="btn-approval-view-plan"
            onClick={() => void resolveApproval(true)}
          >
            Approve plan
          </button>
        ) : null}
        <button
          type="button"
          className="btn ghost"
          id="btn-approval-suggest"
          disabled={!suggestion.trim()}
          onClick={() => {
            void resolveApproval(false, suggestion.trim());
          }}
        >
          Suggest
        </button>
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
          {isPlan ? "Build" : "Approve"}
        </button>
      </div>
    </div>
  );
}
