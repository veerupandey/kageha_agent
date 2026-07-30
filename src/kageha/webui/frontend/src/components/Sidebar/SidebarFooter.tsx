import { useState } from "react";
import { useAppStore } from "../../store";

interface SidebarFooterProps {
  userName?: string;
}

export function SidebarFooter({ userName }: SidebarFooterProps) {
  const name = userName || "User";
  const initial = name.charAt(0).toUpperCase();
  const newUi = useAppStore((s) => s.prefs.newUi);
  const setPrefs = useAppStore((s) => s.setPrefs);
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="relative mt-auto border-t border-[var(--color-line)] px-3 py-3">
      {menuOpen && (
        <div
          className="absolute bottom-full left-3 z-20 mb-2 w-56 rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] p-2 shadow-lg"
          role="menu"
        >
          <label className="flex items-center justify-between gap-2 px-2 py-1.5 text-sm text-ink">
            <span>New UI (Canvas)</span>
            <input
              type="checkbox"
              checked={newUi}
              onChange={(e) => setPrefs({ newUi: e.target.checked })}
              aria-label="Toggle new UI"
            />
          </label>
        </div>
      )}
      <div className="flex items-center gap-2.5">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent-soft)] text-xs font-medium text-accent">
          {initial}
        </div>
        <span className="min-w-0 flex-1 truncate text-sm text-ink">
          {name}
        </span>
        <button
          type="button"
          className="text-xs text-faint hover:text-ink"
          aria-label="Settings"
          title="Settings"
          onClick={() => setMenuOpen((v) => !v)}
        >
          ⚙
        </button>
      </div>
    </div>
  );
}
