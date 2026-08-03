import { useAppStore } from "../../store";
import { HeroInput } from "./HeroInput";
import { QuickActions } from "./QuickActions";
import { ThreadCard } from "./ThreadCard";

export function CommandCenter() {
  const sessions = useAppStore((s) => s.sessions);
  const openSession = useAppStore((s) => s.openSession);

  const recentSessions = sessions.filter((s) => !s.archived).slice(0, 6);

  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-4 py-12">
      <div className="flex w-full max-w-[700px] flex-col items-center gap-8">
        {/* Hero heading */}
        <h1 className="text-center text-4xl font-bold tracking-tight text-ink">
          Let's get to work.
        </h1>

        {/* Main input */}
        <HeroInput />

        {/* Quick action suggestions */}
        <QuickActions />

        {/* Recent threads */}
        {recentSessions.length > 0 && (
          <div className="w-full pt-4">
            <div className="mb-3 flex items-center justify-between">
              <p className="text-sm font-medium text-muted">Recent threads</p>
              <button
                type="button"
                className="text-xs text-faint hover:text-ink"
              >
                Show all
              </button>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {recentSessions.map((session) => (
                <ThreadCard
                  key={session.session_id}
                  session={session}
                  onClick={() => void openSession(session.session_id)}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
