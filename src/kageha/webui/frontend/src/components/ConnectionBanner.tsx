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
      className="connection-banner"
      role="status"
      aria-live="polite"
      data-offline={offline ? "1" : "0"}
    >
      <span className="connection-banner-text">{message}</span>
      <button
        type="button"
        className="btn ghost compact"
        onClick={onRetry}
        title="Retry"
      >
        Retry
      </button>
    </div>
  );
}
