import { useCallback, useEffect, useRef } from "react";
import { useAppStore } from "../../store";

export function HeroInput() {
  const draft = useAppStore((s) => s.draft);
  const setDraft = useAppStore((s) => s.setDraft);
  const sendMessage = useAppStore((s) => s.sendMessage);
  const modelOverride = useAppStore((s) => s.modelOverride);
  const setModelOverride = useAppStore((s) => s.setModelOverride);
  const agentMode = useAppStore((s) => s.agentMode);
  const models = useAppStore((s) => s.models);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const autosize = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, []);

  useEffect(() => {
    autosize();
  }, [draft, autosize]);

  return (
    <div className="w-full max-w-[640px]">
      <div className="relative rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] shadow-[0_2px_12px_rgba(0,0,0,0.1)] transition-shadow focus-within:border-[var(--color-accent)] focus-within:shadow-[0_0_0_3px_var(--color-accent-soft)]">
        <div className="flex items-end px-4 pt-3 pb-2">
          <button
            type="button"
            className="mb-0.5 mr-2 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-faint hover:text-ink"
            aria-label="Attach"
            title="Attach files"
          >
            +
          </button>
          <textarea
            ref={inputRef}
            className="max-h-[120px] min-h-[44px] flex-1 resize-none bg-transparent text-[1rem] leading-relaxed text-ink outline-none placeholder:text-faint"
            rows={1}
            placeholder="Ask anything or start a task..."
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void sendMessage();
              }
            }}
          />
          <button
            type="button"
            className="mb-0.5 ml-2 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent text-white transition-opacity hover:opacity-90"
            aria-label="Send"
            title="Send"
            onClick={() => void sendMessage()}
          >
            ↑
          </button>
        </div>

        {/* Controls row */}
        <div className="flex flex-wrap items-center gap-2 border-t border-[var(--color-line)]/50 px-4 py-2">
          <span className="text-xs text-faint">⊕</span>
          <select
            className="rounded-md border border-[var(--color-line)] bg-transparent px-2 py-0.5 text-xs text-muted outline-none"
            title="Model"
            value={modelOverride}
            onChange={(e) => setModelOverride(e.target.value)}
          >
            <option value="">Model</option>
            {models.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
          <span className="text-xs text-faint">⊕ Agent</span>
          <div className="ml-auto flex items-center gap-1.5">
            <span className="rounded-md bg-[var(--color-accent-soft)] px-2.5 py-1 text-xs font-medium text-accent">
              {agentMode === "plan" ? "Plan" : agentMode === "goal" ? "Goal" : "Execute"}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
