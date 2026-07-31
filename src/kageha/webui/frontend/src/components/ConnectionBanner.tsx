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
      className="flex items-center gap-3 border-b border-warn/30 bg-warn-soft px-4 py-2 text-sm text-warn animate-[slideDown_200ms_ease-out]"
      role="status"
      aria-live="polite"
      data-offline={offline ? "1" : "0"}
    >
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" className="shrink-0">
        <path d="M8 5v3.5M8 10.5h.007M14 8A6 6 0 112 8a6 6 0 0112 0z" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round"/>
      </svg>
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
