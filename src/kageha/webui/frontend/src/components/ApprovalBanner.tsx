import { useEffect, useRef, useState } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { useAppStore } from "../store";

function detailText(detail: string | string[] | undefined): string {
  if (!detail) return "";
  if (Array.isArray(detail)) return detail.map(String).join("\n");
  return String(detail);
}

/** Friendly question banner for ask_human — just an answer input. */
function QuestionBanner({
  question,
  yesLabel,
  noLabel,
  onAnswer,
  onDeny,
}: {
  question: string;
  yesLabel?: string;
  noLabel?: string;
  onAnswer: (answer: string) => void;
  onDeny: () => void;
}) {
  const [answer, setAnswer] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    requestAnimationFrame(() => inputRef.current?.focus());
  }, []);

  const hasOptions = Boolean(yesLabel || noLabel);

  return (
    <div
      className="border-t border-accent/20 bg-accent-soft/50 px-4 py-4 md:px-5 animate-[slideUp_200ms_ease-out]"
      role="alertdialog"
      aria-label="Agent is asking a question"
    >
      <div className="mx-auto max-w-3xl">
        {/* Question icon + text */}
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent text-white text-xs font-bold">
            ?
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-ink leading-relaxed">
              {question}
            </p>
          </div>
        </div>

        {/* Binary choice (yes/no labels) */}
        {hasOptions ? (
          <div className="mt-3 ml-10 flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 active:scale-[0.97]"
              onClick={() => onAnswer(yesLabel || "yes")}
            >
              {yesLabel || "Yes"}
            </button>
            <button
              type="button"
              className="rounded-md border border-line bg-surface px-4 py-2 text-sm font-medium text-ink hover:bg-line/50 active:scale-[0.97]"
              onClick={() => onAnswer(noLabel || "no")}
            >
              {noLabel || "No"}
            </button>
            <button
              type="button"
              className="rounded-md px-3 py-2 text-sm text-muted hover:text-ink hover:bg-line/50"
              onClick={onDeny}
            >
              Skip
            </button>
          </div>
        ) : (
          /* Free-text answer */
          <div className="mt-3 ml-10 flex gap-2">
            <input
              ref={inputRef}
              type="text"
              className="min-w-0 flex-1 rounded-md border border-line bg-surface px-3 py-2 text-sm outline-none placeholder:text-faint focus:border-accent/40 focus:ring-2 focus:ring-accent/10"
              placeholder="Type your answer…"
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && answer.trim()) {
                  e.preventDefault();
                  onAnswer(answer.trim());
                }
              }}
            />
            <button
              type="button"
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 active:scale-[0.97] disabled:opacity-40 disabled:cursor-not-allowed"
              disabled={!answer.trim()}
              onClick={() => onAnswer(answer.trim())}
            >
              Reply
            </button>
            <button
              type="button"
              className="rounded-md px-3 py-2 text-sm text-muted hover:text-ink hover:bg-line/50"
              onClick={onDeny}
            >
              Skip
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/** Plan Build approval — focused on approve/suggest/deny. */
function PlanApprovalBanner({
  detail,
  onApprove,
  onSuggest,
  onDeny,
}: {
  detail: string;
  onApprove: () => void;
  onSuggest: (feedback: string) => void;
  onDeny: () => void;
}) {
  const [suggestion, setSuggestion] = useState("");
  const [expanded, setExpanded] = useState(false);
  const approveRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    requestAnimationFrame(() => approveRef.current?.focus());
  }, []);

  // Parse plan steps from the detail text — handles multiple formats:
  // "- [ ] `id`: description" OR "- [ ] `id` description" OR "- [ ] description"
  const steps: { id: string; text: string }[] = [];
  let tldr = "";
  let objective = "";
  if (detail) {
    for (const line of detail.split("\n")) {
      // Try: - [ ] `id`: description
      let stepMatch = line.match(/^-\s*\[[ x]]\s*`([^`]+)`[:\s]+(.+)/);
      if (!stepMatch) {
        // Try: - [ ] description (no id)
        stepMatch = line.match(/^-\s*\[[ x]]\s+(.{3,})/);
        if (stepMatch) {
          steps.push({ id: String(steps.length + 1), text: stepMatch[1].trim() });
          continue;
        }
      }
      if (stepMatch) {
        steps.push({ id: stepMatch[1], text: (stepMatch[2] || stepMatch[1]).trim() });
      }
      // Extract metadata
      if (line.includes("**Objective:**")) {
        objective = line.replace(/.*\*\*Objective:\*\*\s*/, "").trim();
      } else if (line.includes("**TL;DR:**")) {
        tldr = line.replace(/.*\*\*TL;DR:\*\*\s*/, "").trim();
      }
    }
  }

  return (
    <div
      className="border-t border-accent/20 bg-accent-soft/30 px-4 py-5 md:px-5 animate-[slideUp_200ms_ease-out]"
      role="alertdialog"
      aria-label="Plan ready for approval"
    >
      <div className="mx-auto max-w-3xl">
        {/* Header */}
        <div className="flex items-center gap-2.5 mb-3">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent/15">
            <svg width="18" height="18" viewBox="0 0 16 16" fill="none" className="text-accent">
              <path d="M3 8.5L6.5 12L13 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </span>
          <div>
            <p className="text-sm font-semibold text-ink">
              Plan ready — approve to build
            </p>
            {(objective || tldr) && (
              <p className="text-xs text-muted mt-0.5">
                {tldr || objective}
              </p>
            )}
          </div>
        </div>

        {/* Plan steps — rendered as a visual checklist */}
        {steps.length > 0 ? (
          <div className="mb-3 rounded-lg border border-line/60 bg-surface overflow-hidden">
            <div className="px-3 py-2 border-b border-line/40 flex items-center justify-between">
              <span className="text-[0.65rem] font-semibold uppercase tracking-wider text-muted">
                {steps.length} step{steps.length === 1 ? "" : "s"}
              </span>
              <button
                type="button"
                className="text-[0.65rem] text-accent hover:underline"
                onClick={() => setExpanded((v) => !v)}
              >
                {expanded ? "Collapse" : "Show full plan"}
              </button>
            </div>
            <ol className="divide-y divide-line/30">
              {steps.map((step, i) => (
                <li key={step.id} className="flex items-start gap-2.5 px-3 py-2">
                  <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-line/60 text-[0.6rem] font-bold text-muted">
                    {i + 1}
                  </span>
                  <span className="text-[0.82rem] leading-snug text-ink">{step.text}</span>
                </li>
              ))}
            </ol>
            {expanded && detail && (
              <div
                className="markdown border-t border-line/40 px-3 py-3 text-[0.8rem] overflow-auto max-h-[50vh]"
                dangerouslySetInnerHTML={{
                  __html: DOMPurify.sanitize(
                    marked.parse(detail, { async: false }) as string
                  ),
                }}
              />
            )}
          </div>
        ) : detail ? (
          <div className="mb-3 rounded-lg border border-line/60 bg-surface overflow-hidden">
            <div className="px-3 py-2 border-b border-line/40 flex items-center justify-between">
              <span className="text-[0.65rem] font-semibold uppercase tracking-wider text-muted">
                Plan
              </span>
              <button
                type="button"
                className="text-[0.65rem] text-accent hover:underline"
                onClick={() => setExpanded((v) => !v)}
              >
                {expanded ? "Collapse" : "Expand"}
              </button>
            </div>
            <div
              className={`markdown px-3 py-3 text-[0.8rem] overflow-auto ${expanded ? "max-h-[50vh]" : "max-h-48"}`}
              dangerouslySetInnerHTML={{
                __html: DOMPurify.sanitize(
                  marked.parse(detail, { async: false }) as string
                ),
              }}
            />
          </div>
        ) : null}

        {/* Suggestion input */}
        <div className="flex items-center gap-2">
          <input
            type="text"
            className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none placeholder:text-faint focus:border-accent/40 focus:ring-2 focus:ring-accent/10"
            placeholder="Suggest changes to the plan…"
            value={suggestion}
            onChange={(e) => setSuggestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && suggestion.trim()) {
                e.preventDefault();
                onSuggest(suggestion.trim());
                setSuggestion("");
              }
            }}
          />
        </div>

        {/* Action buttons */}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            ref={approveRef}
            type="button"
            className="rounded-lg bg-accent px-5 py-2 text-sm font-medium text-white shadow-sm hover:opacity-90 active:scale-[0.97] transition-all"
            onClick={onApprove}
          >
            ▶ Build
          </button>
          <button
            type="button"
            className="rounded-lg border border-line bg-surface px-4 py-2 text-sm font-medium text-ink shadow-sm hover:bg-line/50 active:scale-[0.97] disabled:opacity-40 transition-all"
            disabled={!suggestion.trim()}
            onClick={() => {
              onSuggest(suggestion.trim());
              setSuggestion("");
            }}
          >
            ✎ Suggest
          </button>
          <button
            type="button"
            className="rounded-lg px-3 py-2 text-sm text-danger hover:bg-danger-soft transition-colors"
            onClick={onDeny}
          >
            Reject
          </button>
        </div>
      </div>
    </div>
  );
}

/** Tool approval — Once / Session / Full / Deny. */
function ToolApprovalBanner({
  title,
  detail,
  onApprove,
  onDeny,
}: {
  title: string;
  detail: string;
  onApprove: (scope: "once" | "session" | "full") => void;
  onDeny: () => void;
}) {
  const approveRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    requestAnimationFrame(() => approveRef.current?.focus());
  }, []);

  return (
    <div
      className="border-t border-warn/25 bg-warn-soft px-4 py-4 md:px-5 animate-[slideUp_200ms_ease-out]"
      role="alertdialog"
      aria-label="Tool needs approval"
    >
      <div className="mx-auto max-w-3xl">
        <div className="flex items-center gap-2 mb-2">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" className="text-warn shrink-0">
            <path d="M8 5v3.5M8 10.5h.007M14 8A6 6 0 112 8a6 6 0 0112 0z" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round"/>
          </svg>
          <p className="text-sm font-semibold text-ink">{title}</p>
        </div>

        {detail ? (
          <pre className="mt-1 max-h-24 overflow-auto rounded-md border border-line bg-surface p-2.5 font-mono text-xs text-muted whitespace-pre-wrap">
            {detail}
          </pre>
        ) : null}

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            ref={approveRef}
            type="button"
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 active:scale-[0.97]"
            onClick={() => onApprove("once")}
          >
            Allow once
          </button>
          <button
            type="button"
            className="rounded-md border border-line bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-line/50"
            onClick={() => onApprove("session")}
          >
            This session
          </button>
          <button
            type="button"
            className="rounded-md border border-danger/30 bg-surface px-3 py-1.5 text-sm font-medium text-danger hover:bg-danger-soft"
            onClick={() => onApprove("full")}
            title="Disable future approval prompts and allow host access"
          >
            Full access
          </button>
          <span className="mx-1 text-line-strong" aria-hidden="true">|</span>
          <button
            type="button"
            className="rounded-md px-3 py-1.5 text-sm text-danger hover:bg-danger-soft"
            onClick={onDeny}
          >
            Deny
          </button>
        </div>
      </div>
    </div>
  );
}

/** Routes to the correct banner type based on approval context. */
export function ApprovalBanner() {
  const pending = useAppStore((s) => s.pendingApproval);
  const resolveApproval = useAppStore((s) => s.resolveApproval);

  if (!pending?.approval_id) return null;

  const isPlan =
    pending.risk_class === "plan" || pending.action === "approve_plan";
  const isQuestion =
    pending.risk_class === "hitl" || pending.action === "ask_human";

  const detail = detailText(pending.detail);

  // Extract yes/no labels from detail if it's a structured question
  let yesLabel = "";
  let noLabel = "";
  if (isQuestion && detail) {
    const yesMatch = detail.match(/\[Y(?:es)?]\s*(.+)/i);
    const noMatch = detail.match(/\[N(?:o)?]\s*(.+)/i);
    if (yesMatch) yesLabel = yesMatch[1].trim();
    if (noMatch) noLabel = noMatch[1].trim();
  }

  const questionText = isQuestion
    ? (detail.split(/\[Y(?:es)?]/i)[0] || detail).trim()
    : "";

  if (isQuestion) {
    return (
      <QuestionBanner
        question={questionText || pending.action || "The agent has a question"}
        yesLabel={yesLabel}
        noLabel={noLabel}
        onAnswer={(answer) => void resolveApproval(true, answer, "once")}
        onDeny={() => void resolveApproval(false)}
      />
    );
  }

  if (isPlan) {
    return (
      <PlanApprovalBanner
        detail={detail}
        onApprove={() => void resolveApproval(true, "", "once")}
        onSuggest={(feedback) => void resolveApproval(false, feedback)}
        onDeny={() => void resolveApproval(false)}
      />
    );
  }

  // Default: tool approval
  const title = pending.label || pending.action || "Approval needed";
  return (
    <ToolApprovalBanner
      title={title}
      detail={detail}
      onApprove={(scope) => void resolveApproval(true, "", scope)}
      onDeny={() => void resolveApproval(false)}
    />
  );
}
