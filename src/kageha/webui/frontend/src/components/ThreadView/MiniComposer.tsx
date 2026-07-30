import { useRef, useState } from "react";
import { useAppStore } from "../../store";

export function MiniComposer() {
  const sendMessage = useAppStore((s) => s.sendMessage);
  const setDraft = useAppStore((s) => s.setDraft);
  const sending = useAppStore((s) => s.sending);
  const [localDraft, setLocalDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSend = () => {
    const text = localDraft.trim();
    if (!text || sending) return;
    setDraft(text);
    setLocalDraft("");
    void sendMessage(text);
  };

  return (
    <div className="mt-4 border-t border-[var(--color-line)] pt-3">
      <div className="flex items-center gap-2 rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2 focus-within:border-[var(--color-accent)]">
        <input
          ref={inputRef}
          type="text"
          className="min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-faint"
          placeholder="Add a follow-up..."
          value={localDraft}
          onChange={(e) => setLocalDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              handleSend();
            }
          }}
        />
        <select
          className="rounded-md border-0 bg-transparent px-1 py-0.5 text-[0.7rem] text-faint outline-none"
          aria-label="Mode"
          disabled
        >
          <option>Execute</option>
        </select>
        <button
          type="button"
          className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-accent text-xs text-white disabled:opacity-40"
          aria-label="Send"
          disabled={!localDraft.trim() || sending}
          onClick={handleSend}
        >
          ↑
        </button>
      </div>
    </div>
  );
}
