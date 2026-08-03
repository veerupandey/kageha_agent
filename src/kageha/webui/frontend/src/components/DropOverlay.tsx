import { cn } from "../lib/cn";

interface DropOverlayProps {
  visible: boolean;
}

export function DropOverlay({ visible }: DropOverlayProps) {
  return (
    <div
      className={cn(
        "pointer-events-none fixed inset-0 z-[70] flex items-center justify-center",
        "bg-accent/15 backdrop-blur-[2px]",
        "transition-opacity duration-200",
        visible ? "opacity-100" : "opacity-0 pointer-events-none",
      )}
      id="drop-overlay"
      aria-hidden={!visible}
    >
      <div
        className={cn(
          "flex flex-col items-center gap-3 rounded-xl border-2 border-dashed border-accent/40",
          "bg-surface px-8 py-6 shadow-xl",
          "transition-transform duration-200",
          visible ? "scale-100" : "scale-95",
        )}
      >
        <svg
          width="32"
          height="32"
          viewBox="0 0 24 24"
          fill="none"
          className="text-accent"
        >
          <path
            d="M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <p className="text-sm font-medium text-ink">Drop files to attach</p>
        <p className="text-xs text-muted">Images, documents, code files</p>
      </div>
    </div>
  );
}
