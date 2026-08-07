interface SidebarHeaderProps {
  onNewThread: () => void;
  onCollapse?: () => void;
}

import { Icon } from "../../lib/icons";

export function SidebarHeader({ onNewThread, onCollapse }: SidebarHeaderProps) {
  return (
    <div className="flex flex-col gap-3 px-3 pt-4 pb-2">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-lg font-semibold tracking-tight text-ink">
          <Icon.Logo size={18} className="text-accent" />
          Kageha
        </span>
        <button
          type="button"
          className="ka-icon-btn h-7 w-7"
          aria-label="Collapse sidebar"
          title="Collapse sidebar"
          onClick={onCollapse}
        >
          <Icon.Collapse size={17} />
        </button>
      </div>
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2 text-sm font-medium text-ink transition-colors hover:border-[var(--color-line-strong)]"
        onClick={onNewThread}
      >
        <Icon.NewThread size={16} className="text-accent" />
        New thread
      </button>
    </div>
  );
}
