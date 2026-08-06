import { useCallback, useEffect, useRef, useState } from "react";
import {
  startMicRecording,
  stopSpokenReply,
  transcribeBlob,
} from "../../lib/voiceClient";
import { useAppStore } from "../../store";

export function MiniComposer() {
  const sendMessage = useAppStore((s) => s.sendMessage);
  const setDraft = useAppStore((s) => s.setDraft);
  const sending = useAppStore((s) => s.sending);
  const stopGeneration = useAppStore((s) => s.stopGeneration);
  const sessionId = useAppStore((s) => s.sessionId);
  const pendingFiles = useAppStore((s) => s.pendingFiles);
  const addPendingFiles = useAppStore((s) => s.addPendingFiles);
  const removePendingFile = useAppStore((s) => s.removePendingFile);
  const showToast = useAppStore((s) => s.showToast);
  const [localDraft, setLocalDraft] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const micStopRef = useRef<null | (() => Promise<Blob>)>(null);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);

  // Grow the textarea with its content (single line → up to max-h).
  const autosize = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, []);

  useEffect(() => {
    autosize();
  }, [localDraft, autosize]);

  const handleSend = () => {
    const text = localDraft.trim();
    if (!text || sending) return;
    setDraft(text);
    setLocalDraft("");
    void sendMessage(text);
  };

  const handleMic = useCallback(() => {
    void (async () => {
      if (!sessionId) return;
      if (recording && micStopRef.current) {
        setRecording(false);
        setTranscribing(true);
        try {
          const blob = await micStopRef.current();
          micStopRef.current = null;
          const text = await transcribeBlob(sessionId, blob);
          if (text) {
            const next = localDraft.trim() ? `${localDraft.trim()} ${text}` : text;
            setLocalDraft("");
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
  }, [localDraft, recording, sendMessage, sessionId, setDraft, showToast]);

  return (
    <div className="mt-4 border-t border-[var(--color-line)] pt-3">
      {pendingFiles.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
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
      <div className="flex items-end gap-2 rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2 focus-within:border-[var(--color-accent)]">
        <button
          type="button"
          className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-faint hover:text-ink"
          aria-label="Attach files"
          title="Attach files"
          onClick={() => fileInputRef.current?.click()}
        >
          +
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
          className="max-h-[160px] min-h-[24px] min-w-0 flex-1 resize-none bg-transparent py-0.5 text-sm leading-relaxed text-ink outline-none placeholder:text-faint"
          rows={1}
          placeholder="Add a follow-up…  (Shift+Enter for a new line)"
          value={localDraft}
          onChange={(e) => setLocalDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter") return;
            // Shift+Enter inserts a newline at the caret (explicit, IME-safe)
            // so follow-ups can be multi-line.
            if (e.shiftKey) {
              e.preventDefault();
              const el = e.currentTarget;
              const start = el.selectionStart ?? localDraft.length;
              const end = el.selectionEnd ?? localDraft.length;
              const pos = start + 1;
              setLocalDraft(
                localDraft.slice(0, start) + "\n" + localDraft.slice(end),
              );
              requestAnimationFrame(() => {
                el.focus();
                el.setSelectionRange(pos, pos);
              });
              return;
            }
            if (!e.nativeEvent.isComposing) {
              e.preventDefault();
              handleSend();
            }
          }}
        />
        <button
          type="button"
          className="shrink-0 px-1 text-[0.7rem] text-faint hover:text-ink disabled:opacity-40"
          title={recording ? "Stop recording" : "Voice input"}
          aria-label={recording ? "Stop recording" : "Voice input"}
          aria-pressed={recording}
          disabled={!sessionId || sending || transcribing}
          onClick={handleMic}
        >
          {transcribing ? "…" : recording ? "■" : "Mic"}
        </button>
        {sending ? (
          <button
            type="button"
            className="inline-flex h-7 items-center justify-center rounded-full bg-red-500 px-3 text-xs font-medium text-white hover:bg-red-600 transition-colors"
            aria-label="Stop"
            onClick={() => void stopGeneration()}
          >
            Stop
          </button>
        ) : (
          <button
            type="button"
            className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-accent text-xs text-white disabled:opacity-40"
            aria-label="Send"
            disabled={!localDraft.trim()}
            onClick={handleSend}
          >
            ↑
          </button>
        )}
      </div>
    </div>
  );
}
