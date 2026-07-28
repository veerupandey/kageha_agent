import { useAppStore } from "../store";

export function ConnectionBanner() {
  const connectionOnline = useAppStore((s) => s.connectionOnline);
  const bootError = useAppStore((s) => s.bootError);
  const sessionsError = useAppStore((s) => s.sessionsError);
  const retryBoot = useAppStore((s) => s.retryBoot);
  const refreshSessions = useAppStore((s) => s.refreshSessions);

  const offline = !connectionOnline;
  const error = bootError || sessionsError;
  if (!offline && !error) return null;

  const message = offline
    ? "You're offline — reconnect to sync sessions."
    : error;

  const onRetry = () => {
    if (offline || bootError) {
      void retryBoot();
    } else {
      void refreshSessions();
    }
  };

  return (
    <div
      className="flex items-center gap-3 border-b border-warn/30 bg-warn-soft px-4 py-2 text-sm text-warn"
      role="status"
      aria-live="polite"
      data-offline={offline ? "1" : "0"}
    >
      <span className="min-w-0 flex-1">{message}</span>
      <button
        type="button"
        className="shrink-0 font-medium underline-offset-2 hover:underline"
        onClick={onRetry}
        title="Retry"
      >
        Retry
      </button>
    </div>
  );
}
