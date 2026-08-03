interface SidebarHeaderProps {
  onNewThread: () => void;
  onCollapse?: () => void;
}

export function SidebarHeader({ onNewThread, onCollapse }: SidebarHeaderProps) {
  return (
    <div className="flex flex-col gap-3 px-3 pt-4 pb-2">
      <div className="flex items-center justify-between">
        <span className="text-lg font-semibold tracking-tight text-ink">
          ✦ Kageha
        </span>
        <button
          type="button"
          className="inline-flex h-7 w-7 items-center justify-center rounded-md text-faint hover:bg-line/50 hover:text-ink transition-colors"
          aria-label="Collapse sidebar"
          title="Collapse sidebar"
          onClick={onCollapse}
        >
          ⊟
        </button>
      </div>
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2 text-sm font-medium text-ink transition-colors hover:border-[var(--color-line-strong)]"
        onClick={onNewThread}
      >
        <span className="text-accent">✎</span>
        New thread
      </button>
    </div>
  );
}
