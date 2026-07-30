import { cn } from "../../lib/cn";

interface PillProps {
  children: React.ReactNode;
  active?: boolean;
  onClick?: () => void;
  className?: string;
}

export function Pill({ children, active, onClick, className }: PillProps) {
  return (
    <button
      type="button"
      className={cn("ka-pill", active && "ka-pill--active", className)}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
