interface DropOverlayProps {
  visible: boolean;
}

export function DropOverlay({ visible }: DropOverlayProps) {
  if (!visible) return null;
  return (
    <div
      className="pointer-events-none fixed inset-0 z-[70] flex items-center justify-center bg-accent/15"
      id="drop-overlay"
    >
      <p className="rounded-xl border border-accent/30 bg-surface px-6 py-4 text-sm font-medium text-ink shadow-lg">
        Drop files to attach
      </p>
    </div>
  );
}
