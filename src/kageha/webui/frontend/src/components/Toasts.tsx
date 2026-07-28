import { useAppStore } from "../store";

export function Toasts() {
  const toasts = useAppStore((s) => s.toasts);
  const dismissToast = useAppStore((s) => s.dismissToast);

  if (!toasts.length) return null;

  return (
    <div
      className="fixed bottom-4 right-4 z-[80] flex max-w-sm flex-col gap-2"
      id="toast-stack"
      aria-live="polite"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          className="flex items-start gap-3 rounded-lg border border-line bg-surface px-3 py-2.5 text-sm shadow-lg"
          role="status"
        >
          <span className="min-w-0 flex-1">{t.text}</span>
          <button
            type="button"
            className="shrink-0 text-faint hover:text-ink"
            aria-label="Dismiss"
            onClick={() => dismissToast(t.id)}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
