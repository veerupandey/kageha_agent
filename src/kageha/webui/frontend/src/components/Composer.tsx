import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { AgentMode, SlashCommand } from "../api/types";
import { filterSlashByCapabilities } from "../api/slashCatalog";
import { cn } from "../lib/cn";
import { Icon } from "../lib/icons";
import {
  applySlashCommand,
  filterSlashCommands,
  getAtContext,
  getSlashContext,
  slashCommandGroup,
  slashCommandTitle,
  slashCommandUsage,
} from "../lib/slash";
import {
  startMicRecording,
  stopSpokenReply,
  transcribeBlob,
} from "../lib/voiceClient";
import { useAppStore } from "../store";

const MODES: AgentMode[] = ["normal", "plan", "goal", "multitask"];

const QA_START =
  /^(what|who|when|where|why|how|is|are|can|could|would|should|do|does|did)\b/i;

function looksLikeGoalQA(text: string): boolean {
  const t = String(text || "").trim();
  if (!t) return false;
  if (t.endsWith("?")) return true;
  return QA_START.test(t);
}

interface FileHit {
  path: string;
  source?: string;
}

type PermissionMode = "ask" | "auto" | "full";

export function Composer() {
  const draft = useAppStore((s) => s.draft);
  const setDraft = useAppStore((s) => s.setDraft);
  const agentMode = useAppStore((s) => s.agentMode);
  const setAgentMode = useAppStore((s) => s.setAgentMode);
  const autoApprove = useAppStore((s) => s.autoApprove);
  const permissionScope = useAppStore((s) => s.permissionScope);
  const setPermissionsMode = useAppStore((s) => s.setPermissionsMode);
  const modelOverride = useAppStore((s) => s.modelOverride);
  const setModelOverride = useAppStore((s) => s.setModelOverride);
  const sending = useAppStore((s) => s.sending);
  const sendMessage = useAppStore((s) => s.sendMessage);
  const stopGeneration = useAppStore((s) => s.stopGeneration);
  const pendingFiles = useAppStore((s) => s.pendingFiles);
  const addPendingFiles = useAppStore((s) => s.addPendingFiles);
  const removePendingFile = useAppStore((s) => s.removePendingFile);
  const composerChip = useAppStore((s) => s.composerChip);
  const clearComposerChip = useAppStore((s) => s.clearComposerChip);
  const queue = useAppStore((s) =>
    s.sessionId ? s.runs[s.sessionId]?.queue : undefined,
  );
  const slashCatalog = useAppStore((s) => s.slashCatalog);
  const capabilities = useAppStore((s) => s.capabilities);
  const models = useAppStore((s) => s.models);
  const sessionId = useAppStore((s) => s.sessionId);
  const voiceReply = useAppStore((s) => s.prefs.voiceReply);
  const setPrefs = useAppStore((s) => s.setPrefs);
  const showToast = useAppStore((s) => s.showToast);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const modelInputRef = useRef<HTMLInputElement>(null);
  const micStopRef = useRef<null | (() => Promise<Blob>)>(null);
  const [caret, setCaret] = useState(0);
  const [slashIndex, setSlashIndex] = useState(0);
  const [atIndex, setAtIndex] = useState(0);
  const [atHits, setAtHits] = useState<FileHit[]>([]);
  const [atLoading, setAtLoading] = useState(false);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);

  const queueItems = queue || [];

  const permMode: PermissionMode =
    permissionScope === "full"
      ? "full"
      : autoApprove
        ? "auto"
        : "ask";

  const slashCommands = useMemo(
    () => filterSlashByCapabilities(slashCatalog, capabilities),
    [slashCatalog, capabilities],
  );

  const slashCtx = useMemo(
    () => getSlashContext(draft, caret),
    [draft, caret],
  );
  const atCtx = useMemo(() => {
    if (slashCtx) return null;
    return getAtContext(draft, caret);
  }, [draft, caret, slashCtx]);

  const slashItems = useMemo(() => {
    if (!slashCtx) return [] as SlashCommand[];
    return filterSlashCommands(slashCommands, slashCtx.query);
  }, [slashCtx, slashCommands]);

  const showGoalBanner = agentMode === "goal" && looksLikeGoalQA(draft);

  const autosize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, []);

  useEffect(() => {
    autosize();
  }, [draft, autosize]);

  useEffect(() => {
    setSlashIndex(0);
  }, [slashCtx?.query, slashItems.length]);

  useEffect(() => {
    setAtIndex(0);
  }, [atCtx?.query]);

  const atOpen = Boolean(atCtx);
  const atQuery = atCtx?.query ?? "";

  useEffect(() => {
    if (!atOpen || capabilities.projectFiles === false) {
      setAtHits([]);
      setAtLoading(false);
      return;
    }
    let cancelled = false;
    setAtLoading(true);
    const q = encodeURIComponent(atQuery);
    const timer = window.setTimeout(() => {
      void fetch(`/api/project/files?q=${q}&limit=20`)
        .then((r) => r.json().catch(() => ({})))
        .then((data: { files?: FileHit[]; items?: FileHit[] }) => {
          if (cancelled) return;
          const list = data.files || data.items || [];
          setAtHits(
            list
              .map((f) =>
                typeof f === "string"
                  ? { path: f }
                  : { path: String(f.path || ""), source: f.source },
              )
              .filter((f) => f.path),
          );
          setAtLoading(false);
        })
        .catch(() => {
          if (!cancelled) {
            setAtHits([]);
            setAtLoading(false);
          }
        });
    }, 220);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [atOpen, atQuery, capabilities.projectFiles]);

  const syncCaret = () => {
    const el = textareaRef.current;
    if (el) setCaret(el.selectionStart ?? el.value.length);
  };

  const replaceRange = (start: number, end: number, insert: string) => {
    const head = draft.slice(0, start);
    const after = draft.slice(end);
    const next = head + insert + after;
    setDraft(next);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      const pos = head.length + insert.length;
      el.focus();
      el.setSelectionRange(pos, pos);
      setCaret(pos);
    });
  };

  const pickSlash = (cmd: SlashCommand) => {
    if (!slashCtx) return;
    const result = applySlashCommand(cmd, {
      start: slashCtx.start,
      end: Math.max(slashCtx.end, caret),
    });
    if (result === "attach") fileInputRef.current?.click();
    if (result === "focus-model") {
      modelInputRef.current?.focus();
      modelInputRef.current?.select();
    }
    requestAnimationFrame(() => textareaRef.current?.focus());
  };

  const pickAt = (hit: FileHit) => {
    if (!atCtx) return;
    replaceRange(atCtx.start, Math.max(atCtx.end, caret), `@${hit.path} `);
  };

  const chipLabel =
    composerChip.kind === "multitask"
      ? "Multitask"
      : composerChip.kind === "mode" && composerChip.value
        ? String(composerChip.value)[0].toUpperCase() +
          String(composerChip.value).slice(1)
        : null;

  const modeLabel = agentMode[0].toUpperCase() + agentMode.slice(1);
  const permLabel =
    permMode === "full" ? "Full" : permMode === "auto" ? "Auto" : "Ask";

  return (
    <form
      className="ka-composer shrink-0 border-t border-line bg-canvas px-3 pb-3 pt-2 md:px-5"
      id="composer"
      onSubmit={(e) => {
        e.preventDefault();
        // Only send via button click — keyboard Enter is handled in onKeyDown
      }}
    >
      <div className="mx-auto max-w-3xl">
        {showGoalBanner ? (
          <div
            className="mb-2 flex items-center gap-2 rounded-md bg-warn-soft px-3 py-1.5 text-sm text-warn"
            role="status"
          >
            <span className="flex-1">This looks like Normal</span>
            <button
              type="button"
              className="font-medium underline-offset-2 hover:underline"
              onClick={() => setAgentMode("normal")}
            >
              Switch to Normal
            </button>
          </div>
        ) : null}

        {pendingFiles.length > 0 ? (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {pendingFiles.map((file, i) => (
              <span
                key={`${file.name}-${i}`}
                className="inline-flex items-center gap-1 rounded-full border border-line bg-surface px-2 py-0.5 text-xs"
              >
                <span className="max-w-[10rem] truncate">{file.name}</span>
                <button
                  type="button"
                  className="text-faint hover:text-ink"
                  aria-label={`Remove ${file.name}`}
                  onClick={() => removePendingFile(i)}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        ) : null}

        {queueItems.length > 0 ? (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {queueItems.map((q, i) => (
              <span
                key={i}
                className="rounded-full bg-accent-soft px-2 py-0.5 text-xs text-accent"
              >
                Queued · {(q.text || "(files)").slice(0, 48)}
              </span>
            ))}
          </div>
        ) : null}

        <div className="ka-composer-surface relative rounded-2xl border border-line/80 bg-surface shadow-[0_2px_12px_rgba(0,0,0,0.08)] ring-1 ring-line/20 focus-within:border-accent/50 focus-within:ring-accent/20 transition-all">
          {slashCtx && slashItems.length > 0 ? (
            <div
              className="absolute bottom-full left-0 right-0 z-20 mb-1 max-h-56 overflow-auto rounded-lg border border-line bg-surface p-1 shadow-lg"
              id="slash-picker"
              role="listbox"
              aria-label="Slash commands"
            >
              {slashItems.map((cmd, i) => (
                <button
                  key={cmd.id}
                  type="button"
                  role="option"
                  aria-selected={i === slashIndex}
                  className={cn(
                    "grid w-full gap-0.5 rounded-md px-2.5 py-1.5 text-left text-sm",
                    i === slashIndex && "bg-accent-soft",
                  )}
                  onMouseEnter={() => setSlashIndex(i)}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    pickSlash(cmd);
                  }}
                >
                  <span className="flex items-center gap-2">
                    <span className="font-medium">{slashCommandTitle(cmd)}</span>
                    <code className="text-xs text-faint">{slashCommandUsage(cmd)}</code>
                    <span className="ml-auto text-[0.65rem] uppercase tracking-wide text-faint">
                      {slashCommandGroup(cmd)}
                    </span>
                  </span>
                  <span className="flex items-center gap-2 text-xs text-muted">
                    <span className="min-w-0 flex-1 truncate">{cmd.description}</span>
                    {i === slashIndex ? <kbd className="text-faint">Enter ↵</kbd> : null}
                  </span>
                </button>
              ))}
            </div>
          ) : null}

          {atCtx ? (
            <div
              className="absolute bottom-full left-0 right-0 z-20 mb-1 max-h-56 overflow-auto rounded-lg border border-line bg-surface p-1 shadow-lg"
              id="at-picker"
              role="listbox"
              aria-label="File mentions"
            >
              {atLoading ? (
                <p className="px-2.5 py-2 text-sm text-muted">Searching…</p>
              ) : !atHits.length ? (
                <p className="px-2.5 py-2 text-sm text-muted">No files</p>
              ) : (
                atHits.map((hit, i) => (
                  <button
                    key={hit.path}
                    type="button"
                    role="option"
                    aria-selected={i === atIndex}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm",
                      i === atIndex && "bg-accent-soft",
                    )}
                    onMouseEnter={() => setAtIndex(i)}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      pickAt(hit);
                    }}
                  >
                    <span className="min-w-0 flex-1 truncate font-mono text-xs">
                      {hit.path}
                    </span>
                    {hit.source ? (
                      <span className="text-xs text-faint">{hit.source}</span>
                    ) : null}
                  </button>
                ))
              )}
            </div>
          ) : null}

          <div className="flex items-end gap-1.5 px-3 pt-2.5">
            <button
              type="button"
              className="ka-icon-btn mb-1.5 h-8 w-8 shrink-0"
              id="btn-attach"
              title="Attach files (drag & drop also works)"
              aria-label="Attach files"
              onClick={() => fileInputRef.current?.click()}
            >
              <Icon.Attach size={18} />
            </button>
            <button
              type="button"
              className={cn(
                "mb-1.5 inline-flex h-8 shrink-0 items-center justify-center rounded-lg px-2.5 text-xs font-medium transition-colors",
                recording
                  ? "bg-danger/15 text-danger"
                  : "text-muted hover:bg-line/50 hover:text-ink",
              )}
              id="btn-mic"
              title={
                recording
                  ? "Stop recording"
                  : "Click to record, click again to send"
              }
              aria-label={recording ? "Stop recording" : "Voice input"}
              aria-pressed={recording}
              disabled={!sessionId || sending || transcribing}
              onClick={() => {
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
                        const next = draft.trim()
                          ? `${draft.trim()} ${text}`
                          : text;
                        setDraft(next);
                        await sendMessage(next);
                      } else {
                        showToast("No speech detected");
                      }
                    } catch (err) {
                      showToast(
                        `Mic: ${err instanceof Error ? err.message : err}`,
                      );
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
                    showToast(
                      `Mic: ${err instanceof Error ? err.message : err}`,
                    );
                  }
                })();
              }}
            >
              {transcribing ? "…" : <Icon.Mic size={15} className={recording ? "text-danger" : undefined} />}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              id="file-input"
              multiple
              hidden
              accept="image/*,video/*,.pdf,.doc,.docx,.txt,.md,.csv,.json,.yaml,.yml,.zip,audio/*"
              onChange={(e) => {
                addPendingFiles(e.target.files);
                e.target.value = "";
              }}
            />
            <div className="min-w-0 flex-1">
              {chipLabel ? (
                <div className="mb-1 inline-flex items-center gap-1 rounded-full bg-accent-soft px-2 py-0.5 text-xs text-accent">
                  <span>{chipLabel}</span>
                  <button
                    type="button"
                    aria-label="Clear command"
                    onClick={() =>
                      clearComposerChip({
                        resetMode: composerChip.kind === "mode",
                      })
                    }
                  >
                    ×
                  </button>
                </div>
              ) : null}
              <label className="sr-only" htmlFor="message-input">
                Message
              </label>
              <textarea
                ref={textareaRef}
                id="message-input"
                className="max-h-[200px] min-h-[48px] w-full resize-none bg-transparent px-2 py-2.5 text-[0.92rem] leading-relaxed outline-none placeholder:text-faint/70"
                rows={1}
                placeholder="Ask anything or describe a task…"
                autoComplete="off"
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value);
                  setCaret(e.target.selectionStart ?? e.target.value.length);
                }}
                onClick={syncCaret}
                onKeyUp={syncCaret}
                onSelect={syncCaret}
                onPaste={(e) => {
                  const files = e.clipboardData?.files;
                  if (files?.length) {
                    e.preventDefault();
                    addPendingFiles(files);
                  }
                }}
                onKeyDown={(e) => {
                  if (slashCtx && slashItems.length) {
                    if (e.key === "ArrowDown") {
                      e.preventDefault();
                      setSlashIndex((i) =>
                        Math.min(i + 1, slashItems.length - 1),
                      );
                      return;
                    }
                    if (e.key === "ArrowUp") {
                      e.preventDefault();
                      setSlashIndex((i) => Math.max(0, i - 1));
                      return;
                    }
                    if (e.key === "Enter" || e.key === "Tab") {
                      e.preventDefault();
                      const cmd = slashItems[slashIndex];
                      if (cmd) pickSlash(cmd);
                      return;
                    }
                    if (e.key === "Escape") {
                      e.preventDefault();
                      replaceRange(slashCtx.start, caret, "");
                      return;
                    }
                  }
                  if (atCtx && atHits.length) {
                    if (e.key === "ArrowDown") {
                      e.preventDefault();
                      setAtIndex((i) => Math.min(i + 1, atHits.length - 1));
                      return;
                    }
                    if (e.key === "ArrowUp") {
                      e.preventDefault();
                      setAtIndex((i) => Math.max(0, i - 1));
                      return;
                    }
                    if (e.key === "Enter" || e.key === "Tab") {
                      e.preventDefault();
                      const hit = atHits[atIndex];
                      if (hit) pickAt(hit);
                      return;
                    }
                    if (e.key === "Escape") {
                      e.preventDefault();
                      replaceRange(atCtx.start, caret, "");
                      return;
                    }
                  }
                  if (e.key === "Enter") {
                    // Shift+Enter / Alt+Enter: insert a newline at the caret so
                    // multi-line drafts work reliably across IME / platforms
                    // (we own the insertion rather than relying on the default).
                    if (e.shiftKey || e.altKey) {
                      e.preventDefault();
                      const el = e.currentTarget;
                      const start = el.selectionStart ?? draft.length;
                      const end = el.selectionEnd ?? draft.length;
                      const pos = start + 1;
                      setDraft(draft.slice(0, start) + "\n" + draft.slice(end));
                      requestAnimationFrame(() => {
                        el.focus();
                        el.setSelectionRange(pos, pos);
                        setCaret(pos);
                      });
                      return;
                    }
                    // Plain Enter sends — but not while composing (IME).
                    if (!e.nativeEvent.isComposing) {
                      e.preventDefault();
                      void sendMessage();
                    }
                  }
                }}
              />
            </div>
            <div className="mb-1.5 flex shrink-0 items-center gap-1.5">
              {sending ? (
                <>
                  <button
                    type="button"
                    className="rounded-lg px-2.5 py-1.5 text-sm text-muted hover:bg-line/50 transition-colors"
                    id="btn-queue"
                    title="Queue while sending"
                    onClick={() => void sendMessage()}
                  >
                    Queue
                  </button>
                  <button
                    type="button"
                    className="rounded-lg bg-danger px-3.5 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-danger/90 transition-colors"
                    id="btn-stop"
                    onClick={() => {
                      void stopGeneration();
                    }}
                  >
                    Stop
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className={cn(
                    "rounded-lg bg-accent px-3.5 py-1.5 text-sm font-medium text-white shadow-sm",
                    "hover:opacity-90 active:scale-[0.97] transition-all",
                    "disabled:opacity-40 disabled:cursor-not-allowed disabled:active:scale-100",
                  )}
                  id="btn-send"
                  disabled={!draft.trim() && !pendingFiles.length}
                  onClick={() => void sendMessage()}
                >
                  Send
                </button>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-1.5 border-t border-line/50 px-3 py-1.5">
            <span className="text-[0.6rem] text-faint/60 mr-1">⇧↵ newline</span>
            <DropdownMenu.Root>
              <DropdownMenu.Trigger asChild>
                <button
                  type="button"
                  className={cn(
                    "rounded-md px-2 py-1 text-xs font-medium transition-colors",
                    agentMode === "normal"
                      ? "text-muted hover:bg-line/70 hover:text-ink"
                      : "bg-accent-soft text-accent",
                  )}
                  aria-pressed={agentMode !== "normal"}
                >
                  <span className="inline-flex items-center gap-1.5">
                    {agentMode !== "normal" && (
                      <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true" />
                    )}
                    {modeLabel}
                    {agentMode !== "normal" && (
                      <span className="rounded-full bg-accent/15 px-1.5 text-[0.6rem] uppercase tracking-wide">
                        Active
                      </span>
                    )}
                  </span>
                </button>
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Content
                  className="z-50 min-w-40 rounded-lg border border-line bg-surface p-1 shadow-lg"
                  sideOffset={4}
                >
                  {MODES.map((mode) => (
                    <DropdownMenu.Item
                      key={mode}
                      className={cn(
                        "cursor-pointer rounded-md px-2.5 py-1.5 text-sm outline-none data-[highlighted]:bg-accent-soft",
                        agentMode === mode && "font-medium text-accent",
                      )}
                      onSelect={() => setAgentMode(mode)}
                    >
                      {mode[0].toUpperCase() + mode.slice(1)}
                    </DropdownMenu.Item>
                  ))}
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>

            <button
              type="button"
              className={cn(
                "rounded-md px-2 py-1 text-xs font-medium",
                voiceReply
                  ? "bg-accent-soft text-accent"
                  : "text-muted hover:bg-line/70 hover:text-ink",
              )}
              title="Speak assistant replies aloud (Gemini TTS)"
              aria-pressed={voiceReply}
              onClick={() => {
                const next = !voiceReply;
                setPrefs({ voiceReply: next });
                if (!next) stopSpokenReply();
              }}
            >
              Speak
            </button>

            <DropdownMenu.Root>
              <DropdownMenu.Trigger asChild>
                <button
                  type="button"
                  className="rounded-md px-2 py-1 text-xs font-medium text-muted hover:bg-line/70 hover:text-ink"
                  id="btn-ask-mode"
                  data-ask={permMode}
                >
                  {permLabel}
                </button>
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Content
                  className="z-50 min-w-44 rounded-lg border border-line bg-surface p-1 shadow-lg"
                  sideOffset={4}
                >
                  {(
                    [
                      ["ask", "Ask — confirm risky tools"],
                      ["auto", "Auto — approve routine requests"],
                      ["full", "Full access — no prompts or sandbox boundary"],
                    ] as const
                  ).map(([mode, label]) => (
                    <DropdownMenu.Item
                      key={mode}
                      className={cn(
                        "cursor-pointer rounded-md px-2.5 py-1.5 text-sm outline-none data-[highlighted]:bg-accent-soft",
                        permMode === mode && "font-medium text-accent",
                      )}
                      onSelect={() => void setPermissionsMode(mode)}
                    >
                      {label}
                    </DropdownMenu.Item>
                  ))}
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>

            <input
              ref={modelInputRef}
              type="text"
              id="model-input"
              className="ml-auto min-w-0 max-w-[10rem] truncate border-0 bg-transparent px-1 py-1 text-right text-xs text-muted outline-none placeholder:text-faint"
              placeholder="Model"
              title="Optional model override"
              autoComplete="off"
              value={modelOverride}
              onChange={(e) => setModelOverride(e.target.value)}
              list="model-suggestions"
            />
            <datalist id="model-suggestions">
              {models.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </div>
        </div>
      </div>
    </form>
  );
}
