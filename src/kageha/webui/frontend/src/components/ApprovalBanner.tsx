import { useEffect, useRef, useState } from "react";
import { cn } from "../lib/cn";
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

  const btn =
    "rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-40";

  return (
    <div
      className="border-t border-warn/25 bg-warn-soft px-4 py-3 md:px-5"
      id="approval-banner"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="approval-banner-title"
      aria-describedby={detail ? "approval-detail" : undefined}
    >
      <div className="mx-auto max-w-3xl">
        <p
          className="text-sm font-semibold text-ink"
          id="approval-banner-title"
        >
          {title}
        </p>
        {detail ? (
          <p
            className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap font-mono text-xs text-muted"
            id="approval-detail"
          >
            {detail}
          </p>
        ) : null}
        <label
          className="mt-2 block text-xs text-muted"
          htmlFor="approval-suggest"
        >
          Suggest (optional)
        </label>
        <input
          id="approval-suggest"
          className="mt-1 w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm outline-none focus:border-accent/40"
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
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {isPlan ? (
            <>
              <button
                ref={approveRef}
                type="button"
                className={cn(btn, "bg-accent text-white")}
                id="btn-approval-approve"
                onClick={() => void resolveApproval(true, "", "once")}
              >
                Build
              </button>
              <button
                type="button"
                className={cn(btn, "bg-surface text-ink hover:bg-line/70")}
                id="btn-approval-view-plan"
                onClick={() => void resolveApproval(true, "", "once")}
              >
                Approve plan
              </button>
            </>
          ) : (
            <>
              <button
                ref={approveRef}
                type="button"
                className={cn(btn, "bg-accent text-white")}
                id="btn-approval-approve"
                onClick={() => void resolveApproval(true, "", "once")}
              >
                Once
              </button>
              <button
                type="button"
                className={cn(btn, "bg-surface text-ink hover:bg-line/70")}
                id="btn-approval-session"
                onClick={() => void resolveApproval(true, "", "session")}
              >
                Session
              </button>
              <button
                type="button"
                className={cn(btn, "bg-surface text-ink hover:bg-line/70")}
                id="btn-approval-full"
                onClick={() => void resolveApproval(true, "", "full")}
              >
                Full
              </button>
            </>
          )}
          <button
            type="button"
            className={cn(btn, "bg-surface text-ink hover:bg-line/70")}
            id="btn-approval-suggest"
            disabled={!suggestion.trim()}
            onClick={() => void resolveApproval(false, suggestion.trim())}
          >
            Suggest
          </button>
          <button
            type="button"
            className={cn(btn, "text-danger hover:bg-danger-soft")}
            id="btn-approval-deny"
            onClick={() => void resolveApproval(false)}
          >
            Deny
          </button>
        </div>
      </div>
    </div>
  );
}
