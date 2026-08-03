import { cn } from "../../lib/cn";

interface IconButtonProps {
  children: React.ReactNode;
  onClick?: () => void;
  title?: string;
  ariaLabel: string;
  className?: string;
  disabled?: boolean;
}

export function IconButton({
  children,
  onClick,
  title,
  ariaLabel,
  className,
  disabled,
}: IconButtonProps) {
  return (
    <button
      type="button"
      className={cn(
        "inline-flex h-8 w-8 items-center justify-center rounded-md text-muted transition-colors hover:bg-[var(--color-surface-hover)] hover:text-ink disabled:opacity-40 disabled:pointer-events-none",
        className,
      )}
      onClick={onClick}
      title={title}
      aria-label={ariaLabel}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
