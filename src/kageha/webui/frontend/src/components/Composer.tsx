import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { AgentMode, SlashCommand } from "../api/types";
import { filterSlashByCapabilities } from "../api/slashCatalog";
import {
  applySlashCommand,
  filterSlashCommands,
  getAtContext,
  getSlashContext,
  slashCommandTitle,
} from "../lib/slash";
import { useAppStore } from "../store";

const MODES: AgentMode[] = ["normal", "plan", "spec", "goal"];

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

export function Composer() {
  const draft = useAppStore((s) => s.draft);
  const setDraft = useAppStore((s) => s.setDraft);
  const agentMode = useAppStore((s) => s.agentMode);
  const setAgentMode = useAppStore((s) => s.setAgentMode);
  const autoApprove = useAppStore((s) => s.autoApprove);
  const setAskMode = useAppStore((s) => s.setAskMode);
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

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const modelInputRef = useRef<HTMLInputElement>(null);
  const [caret, setCaret] = useState(0);
  const [slashIndex, setSlashIndex] = useState(0);
  const [atIndex, setAtIndex] = useState(0);
  const [atHits, setAtHits] = useState<FileHit[]>([]);
  const [atLoading, setAtLoading] = useState(false);

  const queueItems = queue || [];

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

  return (
    <form
      className="composer composer-shell"
      id="composer"
      onSubmit={(e) => {
        e.preventDefault();
        void sendMessage();
      }}
    >
      <div className="composer-toolbar">
        <div className="mode-chips" id="mode-chips" role="group" aria-label="Agent mode">
          {MODES.map((mode) => (
            <button
              key={mode}
              type="button"
              className={`mode-chip${agentMode === mode ? " is-active" : ""}`}
              data-mode={mode}
              aria-pressed={agentMode === mode}
              title={
                mode === "goal"
                  ? "Goal — verifiable outcome, not Q&A"
                  : undefined
              }
              onClick={() => setAgentMode(mode)}
            >
              {mode[0].toUpperCase() + mode.slice(1)}
            </button>
          ))}
        </div>
        <div className="composer-toolbar-right">
          <button
            type="button"
            className={`mode-chip mode-chip-ask${!autoApprove ? " is-active" : ""}`}
            id="btn-ask-mode"
            data-ask={autoApprove ? "auto" : "ask"}
            aria-pressed={!autoApprove}
            title={
              autoApprove
                ? "Auto-approve risky tools (click for Ask)"
                : "Ask mode on · click for Auto"
            }
            onClick={() => setAskMode(autoApprove)}
          >
            {autoApprove ? "Auto" : "Ask"}
          </button>
          <input
            ref={modelInputRef}
            type="text"
            id="model-input"
            className="model-input"
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

      {showGoalBanner ? (
        <div className="goal-qa-banner" id="goal-qa-banner" role="status">
          <span className="goal-qa-banner-text" id="goal-qa-banner-text">
            This looks like Normal
          </span>
          <button
            type="button"
            className="btn ghost compact"
            id="btn-goal-qa-switch"
            onClick={() => setAgentMode("normal")}
          >
            Switch to Normal
          </button>
        </div>
      ) : null}

      {pendingFiles.length > 0 ? (
        <div className="attach-chips" id="attach-chips">
          {pendingFiles.map((file, i) => (
            <span key={`${file.name}-${i}`} className="attach-chip">
              <span className="name">{file.name}</span>
              <button
                type="button"
                className="chip-x"
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
        <div className="queue-chips" id="queue-chips">
          {queueItems.map((q, i) => (
            <span key={i} className="queue-chip">
              Queued · {(q.text || "(files)").slice(0, 48)}
            </span>
          ))}
        </div>
      ) : null}

      <div className="composer-stage">
        {slashCtx && slashItems.length > 0 ? (
          <div
            className="cmd-picker slash-picker"
            id="slash-picker"
            role="listbox"
            aria-label="Slash commands"
          >
            {slashItems.map((cmd, i) => (
              <button
                key={cmd.id}
                type="button"
                role="option"
                id={`slash-opt-${i}`}
                aria-selected={i === slashIndex}
                className={`slash-picker-item${i === slashIndex ? " is-active" : ""}`}
                onMouseEnter={() => setSlashIndex(i)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pickSlash(cmd);
                }}
              >
                <span className="slash-picker-title">
                  {slashCommandTitle(cmd)}
                </span>
                <span className="slash-picker-cmd">{cmd.label}</span>
                <span className="slash-picker-desc">{cmd.description}</span>
              </button>
            ))}
          </div>
        ) : null}

        {atCtx ? (
          <div
            className="cmd-picker at-picker"
            id="at-picker"
            role="listbox"
            aria-label="File mentions"
          >
            {atLoading ? (
              <p className="muted" style={{ padding: "0.5rem 0.75rem" }}>
                Searching…
              </p>
            ) : !atHits.length ? (
              <p className="muted" style={{ padding: "0.5rem 0.75rem" }}>
                No files
              </p>
            ) : (
              atHits.map((hit, i) => (
                <button
                  key={hit.path}
                  type="button"
                  role="option"
                  aria-selected={i === atIndex}
                  className={`at-picker-item${i === atIndex ? " is-active" : ""}`}
                  onMouseEnter={() => setAtIndex(i)}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    pickAt(hit);
                  }}
                >
                  <span className="at-picker-path">{hit.path}</span>
                  {hit.source ? (
                    <span className="at-picker-src">{hit.source}</span>
                  ) : null}
                </button>
              ))
            )}
          </div>
        ) : null}

        <div className="composer-row">
          <button
            type="button"
            className="btn ghost icon"
            id="btn-attach"
            title="Attach files"
            aria-label="Attach files"
            onClick={() => fileInputRef.current?.click()}
          >
            +
          </button>
          <input
            ref={fileInputRef}
            type="file"
            id="file-input"
            multiple
            hidden
            accept="image/*,video/*,.pdf,.doc,.docx,.txt,.md,.csv,.json,.yaml,.yml,.zip"
            onChange={(e) => {
              addPendingFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <div className="composer-input-wrap">
            {chipLabel ? (
              <div
                className="composer-cmd-chip"
                id="composer-cmd-chip"
                data-kind={composerChip.kind || ""}
              >
                <span
                  className="composer-cmd-chip-label"
                  id="composer-cmd-chip-label"
                >
                  {chipLabel}
                </span>
                <button
                  type="button"
                  className="composer-cmd-chip-x"
                  id="composer-cmd-chip-x"
                  aria-label="Clear command"
                  title="Clear"
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
              className="message-input"
              rows={1}
              placeholder="Message Kageha…  / commands · @ files"
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
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void sendMessage();
                }
              }}
            />
          </div>
          <div className="composer-actions">
            {sending ? (
              <>
                <button
                  type="submit"
                  className="btn ghost"
                  id="btn-queue"
                  title="Queue while sending"
                >
                  Queue
                </button>
                <button
                  type="button"
                  className="btn danger"
                  id="btn-stop"
                  onClick={() => {
                    void stopGeneration();
                  }}
                >
                  Stop
                </button>
              </>
            ) : (
              <button type="submit" className="btn primary" id="btn-send">
                Send
              </button>
            )}
          </div>
        </div>
      </div>
    </form>
  );
}
