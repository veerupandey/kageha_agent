import { useEffect, useMemo, useRef, useState } from "react";
import type { SlashCommand } from "../api/types";
import { filterSlashByCapabilities } from "../api/slashCatalog";
import { applySlashCommand, filterSlashCommands, slashCommandTitle } from "../lib/slash";
import { useFocusTrap } from "../lib/focusTrap";
import { useAppStore } from "../store";

export interface PaletteAction {
  id: string;
  label: string;
  description: string;
  kind: string;
  run: () => void;
}

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  onAttach?: () => void;
  onFocusModel?: () => void;
}

export function CommandPalette({
  open,
  onClose,
  onAttach,
  onFocusModel,
}: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const slashCatalog = useAppStore((s) => s.slashCatalog);
  const capabilities = useAppStore((s) => s.capabilities);
  const newChat = useAppStore((s) => s.newChat);
  const showToast = useAppStore((s) => s.showToast);

  const slashCommands = useMemo(
    () => filterSlashByCapabilities(slashCatalog, capabilities),
    [slashCatalog, capabilities],
  );

  const actions: PaletteAction[] = useMemo(
    () => [
      {
        id: "action-new-chat",
        label: "New chat",
        description: "Fresh chat in place",
        kind: "action",
        run: () => {
          void newChat();
          showToast("New chat");
        },
      },
    ],
    [newChat, showToast],
  );

  const slashItems = useMemo(() => {
    const q = query.replace(/^\//, "").trim().toLowerCase();
    return filterSlashCommands(slashCommands, q);
  }, [query, slashCommands]);

  const filteredActions = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return actions;
    return actions.filter(
      (a) =>
        a.label.toLowerCase().includes(q) ||
        a.description.toLowerCase().includes(q) ||
        a.id.includes(q),
    );
  }, [actions, query]);

  type Row =
    | { type: "slash"; cmd: SlashCommand }
    | { type: "action"; action: PaletteAction };

  const rows: Row[] = useMemo(
    () => [
      ...slashItems.map((cmd) => ({ type: "slash" as const, cmd })),
      ...filteredActions.map((action) => ({ type: "action" as const, action })),
    ],
    [slashItems, filteredActions],
  );

  useEffect(() => {
    if (open) {
      setQuery("");
      setIndex(0);
      requestAnimationFrame(() => inputRef.current?.focus());
      document.body.classList.add("command-palette-open");
      document.getElementById("app")?.setAttribute("inert", "");
    } else {
      document.body.classList.remove("command-palette-open");
      document.getElementById("app")?.removeAttribute("inert");
    }
    return () => {
      document.body.classList.remove("command-palette-open");
      document.getElementById("app")?.removeAttribute("inert");
    };
  }, [open]);

  useFocusTrap(open, panelRef, { initialFocusRef: inputRef });

  useEffect(() => {
    setIndex(0);
  }, [query]);

  const runRow = (row: Row) => {
    if (row.type === "slash") {
      const result = applySlashCommand(row.cmd);
      if (result === "attach") onAttach?.();
      if (result === "focus-model") onFocusModel?.();
    } else {
      row.action.run();
    }
    onClose();
  };

  if (!open) return null;

  return (
    <div
      className="command-palette"
      id="command-palette"
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
    >
      <div
        className="command-palette-backdrop"
        id="command-palette-backdrop"
        onClick={onClose}
      />
      <div className="command-palette-panel" ref={panelRef}>
        <input
          ref={inputRef}
          type="search"
          id="command-palette-input"
          className="command-palette-input"
          placeholder="Commands, actions, files…"
          autoComplete="off"
          aria-label="Filter commands"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              onClose();
              return;
            }
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setIndex((i) => Math.min(i + 1, Math.max(0, rows.length - 1)));
              return;
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setIndex((i) => Math.max(0, i - 1));
              return;
            }
            if (e.key === "Enter") {
              e.preventDefault();
              const row = rows[index];
              if (row) runRow(row);
            }
          }}
        />
        <div
          id="command-palette-results"
          className="command-palette-results"
          role="listbox"
          aria-label="Palette results"
        >
          {!rows.length ? (
            <p className="command-palette-empty">No matches</p>
          ) : (
            <>
              {slashItems.length ? (
                <div className="command-palette-group">
                  {slashItems.map((cmd, i) => {
                    const active = index === i;
                    return (
                      <button
                        key={cmd.id}
                        type="button"
                        role="option"
                        aria-selected={active}
                        className={`command-palette-item${active ? " is-active" : ""}`}
                        onMouseEnter={() => setIndex(i)}
                        onClick={() => runRow({ type: "slash", cmd })}
                      >
                        <span className="cmd-label">
                          {slashCommandTitle(cmd)}
                        </span>
                        <span className="cmd-kind">{cmd.label}</span>
                        <span className="cmd-desc">{cmd.description}</span>
                      </button>
                    );
                  })}
                </div>
              ) : null}
              {filteredActions.length ? (
                <div className="command-palette-group">
                  {filteredActions.map((action, i) => {
                    const rowIndex = slashItems.length + i;
                    const active = index === rowIndex;
                    return (
                      <button
                        key={action.id}
                        type="button"
                        role="option"
                        aria-selected={active}
                        className={`command-palette-item${active ? " is-active" : ""}`}
                        onMouseEnter={() => setIndex(rowIndex)}
                        onClick={() => runRow({ type: "action", action })}
                      >
                        <span className="cmd-label">{action.label}</span>
                        <span className="cmd-kind">action</span>
                        <span className="cmd-desc">{action.description}</span>
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </>
          )}
        </div>
        <p className="command-palette-hint">↑↓ navigate · Enter run · Esc close</p>
      </div>
    </div>
  );
}
