import { Command } from "cmdk";
import { useEffect, useMemo } from "react";
import type { SlashCommand } from "../api/types";
import { filterSlashByCapabilities } from "../api/slashCatalog";
import {
  applySlashCommand,
  filterSlashCommands,
  slashCommandTitle,
} from "../lib/slash";
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

  useEffect(() => {
    if (!open) return;
    document.body.classList.add("command-palette-open");
    document.getElementById("app")?.setAttribute("inert", "");
    return () => {
      document.body.classList.remove("command-palette-open");
      document.getElementById("app")?.removeAttribute("inert");
    };
  }, [open]);

  const runSlash = (cmd: SlashCommand) => {
    const result = applySlashCommand(cmd);
    if (result === "attach") onAttach?.();
    if (result === "focus-model") onFocusModel?.();
    onClose();
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center bg-ink/30 px-4 pt-[12vh]"
      id="command-palette"
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
    >
      <button
        type="button"
        className="absolute inset-0 cursor-default"
        aria-label="Close command palette"
        onClick={onClose}
      />
      <Command
        className="relative z-10 w-full max-w-xl overflow-hidden rounded-xl border border-line bg-surface shadow-2xl"
        label="Command palette"
        loop
      >
        <Command.Input
          id="command-palette-input"
          placeholder="Commands and actions…"
          autoFocus
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              onClose();
            }
          }}
        />
        <Command.List id="command-palette-results">
          <Command.Empty>No matches</Command.Empty>
          <Command.Group heading="Commands">
            {filterSlashCommands(slashCommands, "").map((cmd) => (
              <Command.Item
                key={cmd.id}
                value={`${slashCommandTitle(cmd)} ${cmd.label} ${cmd.description}`}
                onSelect={() => runSlash(cmd)}
              >
                <span className="font-medium">{slashCommandTitle(cmd)}</span>
                <span className="text-xs text-faint">{cmd.label}</span>
                <span className="text-xs text-muted">{cmd.description}</span>
              </Command.Item>
            ))}
          </Command.Group>
          <Command.Group heading="Actions">
            {actions.map((action) => (
              <Command.Item
                key={action.id}
                value={`${action.label} ${action.description}`}
                onSelect={() => {
                  action.run();
                  onClose();
                }}
              >
                <span className="font-medium">{action.label}</span>
                <span className="text-xs text-muted">{action.description}</span>
              </Command.Item>
            ))}
          </Command.Group>
        </Command.List>
        <p className="border-t border-line px-3 py-2 text-[0.7rem] text-faint">
          ↑↓ navigate · Enter run · Esc close
        </p>
      </Command>
    </div>
  );
}
