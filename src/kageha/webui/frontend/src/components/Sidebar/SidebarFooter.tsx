interface SidebarFooterProps {
  userName?: string;
}

export function SidebarFooter({ userName }: SidebarFooterProps) {
  const name = userName || "User";
  const initial = name.charAt(0).toUpperCase();

  return (
    <div className="mt-auto border-t border-[var(--color-line)] px-3 py-3">
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
        >
          ⚙
        </button>
      </div>
    </div>
  );
}
