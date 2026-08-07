import { useCallback, useEffect, useRef, useState } from "react";
import {
  startMicRecording,
  stopSpokenReply,
  transcribeBlob,
} from "../../lib/voiceClient";
import { Icon } from "../../lib/icons";
import { useAppStore } from "../../store";

export function HeroInput() {
  const draft = useAppStore((s) => s.draft);
  const setDraft = useAppStore((s) => s.setDraft);
  const sendMessage = useAppStore((s) => s.sendMessage);
  const modelOverride = useAppStore((s) => s.modelOverride);
  const setModelOverride = useAppStore((s) => s.setModelOverride);
  const agentMode = useAppStore((s) => s.agentMode);
  const models = useAppStore((s) => s.models);
  const sessionId = useAppStore((s) => s.sessionId);
  const sending = useAppStore((s) => s.sending);
  const pendingFiles = useAppStore((s) => s.pendingFiles);
  const addPendingFiles = useAppStore((s) => s.addPendingFiles);
  const removePendingFile = useAppStore((s) => s.removePendingFile);
  const showToast = useAppStore((s) => s.showToast);

  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const micStopRef = useRef<null | (() => Promise<Blob>)>(null);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);

  const autosize = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, []);

  useEffect(() => {
    autosize();
  }, [draft, autosize]);

  const handleMic = useCallback(() => {
    void (async () => {
      if (!sessionId) {
        showToast("Start a thread before using voice input");
        return;
      }
      if (recording && micStopRef.current) {
        setRecording(false);
        setTranscribing(true);
        try {
          const blob = await micStopRef.current();
          micStopRef.current = null;
          const text = await transcribeBlob(sessionId, blob);
          if (text) {
            const next = draft.trim() ? `${draft.trim()} ${text}` : text;
            setDraft(next);
            await sendMessage(next);
          } else {
            showToast("No speech detected");
          }
        } catch (err) {
          showToast(`Mic: ${err instanceof Error ? err.message : err}`);
        } finally {
          setTranscribing(false);
        }
        return;
      }
      try {
        stopSpokenReply();
        const rec = await startMicRecording();
        micStopRef.current = rec.stop;
        setRecording(true);
        showToast("Listening… click mic again to send");
      } catch (err) {
        showToast(`Mic: ${err instanceof Error ? err.message : err}`);
      }
    })();
  }, [draft, recording, sendMessage, sessionId, setDraft, showToast]);

  return (
    <div className="ka-hero-input w-full max-w-[640px]">
      <div className="relative rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] shadow-[0_2px_12px_rgba(0,0,0,0.1)] transition-shadow focus-within:border-[var(--color-accent)] focus-within:shadow-[0_0_0_3px_var(--color-accent-soft)]">
        {pendingFiles.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-4 pt-3">
            {pendingFiles.map((f, i) => (
              <span
                key={`${f.name}-${i}`}
                className="inline-flex items-center gap-1 rounded-md bg-[var(--color-canvas)] px-2 py-0.5 text-xs text-muted"
              >
                {f.name}
                <button
                  type="button"
                  className="text-faint hover:text-ink"
                  aria-label={`Remove ${f.name}`}
                  onClick={() => removePendingFile(i)}
                >
                  ✕
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="flex items-end px-4 pt-3 pb-2">
          <button
            type="button"
            className="ka-icon-btn mb-0.5 mr-2 h-7 w-7 shrink-0"
            aria-label="Attach files"
            title="Attach files"
            onClick={() => fileInputRef.current?.click()}
          >
            <Icon.Attach size={18} />
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.length) addPendingFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <textarea
            ref={inputRef}
            className="max-h-[120px] min-h-[44px] flex-1 resize-none bg-transparent text-[1rem] leading-relaxed text-ink outline-none placeholder:text-faint"
            rows={1}
            placeholder="Ask anything or start a task..."
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== "Enter") return;
              // Shift+Enter inserts a newline (explicit, IME-safe).
              if (e.shiftKey) {
                e.preventDefault();
                const el = e.currentTarget;
                const start = el.selectionStart ?? draft.length;
                const end = el.selectionEnd ?? draft.length;
                const pos = start + 1;
                setDraft(draft.slice(0, start) + "\n" + draft.slice(end));
                requestAnimationFrame(() => {
                  el.focus();
                  el.setSelectionRange(pos, pos);
                });
                return;
              }
              if (!e.nativeEvent.isComposing) {
                e.preventDefault();
                void sendMessage();
              }
            }}
          />
          <button
            type="button"
            className="ka-icon-btn mb-0.5 ml-2 h-8 w-8 shrink-0"
            title={
              recording ? "Stop recording" : "Click to record, click again to send"
            }
            aria-label={recording ? "Stop recording" : "Voice input"}
            aria-pressed={recording}
            disabled={sending || transcribing}
            onClick={handleMic}
          >
            {transcribing ? "…" : <Icon.Mic size={17} className={recording ? "text-danger" : undefined} />}
          </button>
          <button
            type="button"
            className="ka-send mb-0.5 ml-2 h-8 w-8 shrink-0"
            aria-label="Send"
            title="Send"
            disabled={sending}
            onClick={() => void sendMessage()}
          >
            <Icon.ArrowUp size={18} />
          </button>
        </div>

        {/* Controls row */}
        <div className="flex flex-wrap items-center gap-2 border-t border-[var(--color-line)]/50 px-4 py-2">
          <Icon.Canvas size={13} className="text-faint" />
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
          <span className="inline-flex items-center gap-1 text-xs text-faint">
            <Icon.Settings size={13} /> Agent
          </span>
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
