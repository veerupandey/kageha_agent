import { cn } from "../../lib/cn";

export type DotStatus = "active" | "waiting" | "idle" | "error";

interface StatusDotProps {
  status: DotStatus;
  className?: string;
}

export function StatusDot({ status, className }: StatusDotProps) {
  return (
    <span
      className={cn("ka-dot", `ka-dot--${status}`, className)}
      aria-hidden="true"
    />
  );
}
