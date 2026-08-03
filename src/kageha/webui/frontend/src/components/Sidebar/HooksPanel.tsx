import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { cn } from "../../lib/cn";

interface HookItem {
  event: string;
  command: string;
  http: string;
  deny_message: string;
  matcher: string;
  timeout_s: number;
}

interface HooksResponse {
  hooks: HookItem[];
  project_root: string;
}

const HOOK_EVENTS = [
  "preToolUse",
  "postToolUse",
  "beforeShell",
  "afterFileEdit",
  "preFileWrite",
  "postFileWrite",
  "postFileCreate",
  "postFileDelete",
  "preCommit",
  "postCommit",
  "sessionStart",
  "sessionEnd",
  "planApproved",
  "specStageComplete",
  "agentStuck",
  "budgetWarning",
  "contextOverflow",
  "stop",
];

function eventColor(event: string): string {
  if (event.startsWith("pre")) return "bg-[var(--color-warn-soft)] text-[var(--color-warn)]";
  if (event.startsWith("post") || event.startsWith("after")) return "bg-accent/15 text-accent";
  if (event.includes("Stuck") || event.includes("Warning") || event.includes("Overflow"))
    return "bg-[var(--color-danger-soft)] text-[var(--color-danger)]";
  return "bg-line/50 text-muted";
}

export function HooksPanel({ onClose }: { onClose: () => void }) {
  const [hooks, setHooks] = useState<HookItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);

  // Create form state
  const [event, setEvent] = useState("preToolUse");
  const [command, setCommand] = useState("");
  const [http, setHttp] = useState("");
  const [matcher, setMatcher] = useState("");
  const [denyMessage, setDenyMessage] = useState("");
  const [creating, setCreating] = useState(false);

  const fetchHooks = useCallback(async () => {
    try {
      const data = await api<HooksResponse>("/api/hooks");
      setHooks(data.hooks || []);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchHooks();
  }, [fetchHooks]);

  const handleCreate = useCallback(async () => {
    if (!event) return;
    if (!command.trim() && !http.trim() && !denyMessage.trim()) {
      alert("Provide a command, HTTP URL, or deny message");
      return;
    }
    setCreating(true);
    try {
      await api("/api/hooks", {
        method: "POST",
        body: JSON.stringify({
          event,
          command: command.trim(),
          http: http.trim(),
          matcher: matcher.trim(),
          deny_message: denyMessage.trim(),
        }),
      });
      setCommand("");
      setHttp("");
      setMatcher("");
      setDenyMessage("");
      setShowCreate(false);
      await fetchHooks();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to create hook");
    } finally {
      setCreating(false);
    }
  }, [event, command, http, matcher, denyMessage, fetchHooks]);

  const handleDelete = useCallback(
    async (index: number) => {
      const confirmed = window.confirm("Delete this hook?");
      if (!confirmed) return;
      try {
        await api("/api/hooks", {
          method: "DELETE",
          body: JSON.stringify({ index }),
        });
        await fetchHooks();
      } catch (err) {
        alert(err instanceof Error ? err.message : "Failed to delete hook");
      }
    },
    [fetchHooks],
  );

  return (
    <div className="flex h-full flex-col bg-surface">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold text-ink">Hooks</h2>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="rounded-md bg-accent/15 px-2.5 py-1 text-xs font-medium text-accent hover:bg-accent/25 transition-colors"
            onClick={() => setShowCreate(!showCreate)}
          >
            + New
          </button>
          <button
            type="button"
            className="rounded-md px-2 py-1 text-xs text-muted hover:text-ink hover:bg-line/50"
            onClick={onClose}
          >
            ✕
          </button>
        </div>
      </div>

      {/* Create form */}
      {showCreate && (
        <div className="border-b border-line px-4 py-3 space-y-2.5">
          <div>
            <label className="block text-[0.65rem] uppercase tracking-wide text-muted mb-1">
              Event
            </label>
            <select
              className="w-full rounded-md border border-line bg-canvas px-2.5 py-1.5 text-sm text-ink focus:border-accent focus:outline-none"
              value={event}
              onChange={(e) => setEvent(e.target.value)}
            >
              {HOOK_EVENTS.map((ev) => (
                <option key={ev} value={ev}>
                  {ev}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-[0.65rem] uppercase tracking-wide text-muted mb-1">
              Shell command
            </label>
            <input
              type="text"
              className="w-full rounded-md border border-line bg-canvas px-2.5 py-1.5 text-sm text-ink placeholder:text-faint focus:border-accent focus:outline-none"
              placeholder="e.g. npm run lint"
              value={command}
              onChange={(e) => setCommand(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-[0.65rem] uppercase tracking-wide text-muted mb-1">
              Or webhook URL
            </label>
            <input
              type="text"
              className="w-full rounded-md border border-line bg-canvas px-2.5 py-1.5 text-sm text-ink placeholder:text-faint focus:border-accent focus:outline-none"
              placeholder="https://..."
              value={http}
              onChange={(e) => setHttp(e.target.value)}
            />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-[0.65rem] uppercase tracking-wide text-muted mb-1">
                Tool matcher
              </label>
              <input
                type="text"
                className="w-full rounded-md border border-line bg-canvas px-2.5 py-1.5 text-xs text-ink placeholder:text-faint focus:border-accent focus:outline-none"
                placeholder="e.g. bash"
                value={matcher}
                onChange={(e) => setMatcher(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-[0.65rem] uppercase tracking-wide text-muted mb-1">
                Deny message
              </label>
              <input
                type="text"
                className="w-full rounded-md border border-line bg-canvas px-2.5 py-1.5 text-xs text-ink placeholder:text-faint focus:border-accent focus:outline-none"
                placeholder="Block reason"
                value={denyMessage}
                onChange={(e) => setDenyMessage(e.target.value)}
              />
            </div>
          </div>
          <div className="flex justify-end">
            <button
              type="button"
              className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent/80 disabled:opacity-50"
              disabled={creating}
              onClick={handleCreate}
            >
              {creating ? "Saving…" : "Add Hook"}
            </button>
          </div>
        </div>
      )}

      {/* Hook list */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-8 text-sm text-muted">
            Loading…
          </div>
        ) : hooks.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-center px-4">
            <p className="text-sm text-muted">No hooks configured</p>
            <p className="mt-1 text-xs text-faint">
              Hooks run shell commands or webhooks on agent events
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-line">
            {hooks.map((hook, idx) => (
              <li
                key={`${hook.event}-${idx}`}
                className="group px-4 py-3 hover:bg-line/30 transition-colors"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span
                        className={cn(
                          "rounded px-1.5 py-0.5 text-[0.6rem] font-medium",
                          eventColor(hook.event),
                        )}
                      >
                        {hook.event}
                      </span>
                      {hook.matcher && (
                        <span className="text-[0.6rem] text-faint">
                          match: {hook.matcher}
                        </span>
                      )}
                    </div>
                    {hook.command && (
                      <p className="mt-1 font-mono text-xs text-ink truncate">
                        $ {hook.command}
                      </p>
                    )}
                    {hook.http && (
                      <p className="mt-1 text-xs text-accent truncate">
                        → {hook.http}
                      </p>
                    )}
                    {hook.deny_message && !hook.command && !hook.http && (
                      <p className="mt-1 text-xs text-[var(--color-danger)]">
                        ✕ {hook.deny_message}
                      </p>
                    )}
                  </div>
                  <button
                    type="button"
                    className="hidden shrink-0 rounded px-2 py-1 text-[0.65rem] text-[var(--color-danger)] hover:bg-[var(--color-danger-soft)] group-hover:block"
                    onClick={() => handleDelete(idx)}
                  >
                    Delete
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Footer hint */}
      <div className="border-t border-line px-4 py-2">
        <p className="text-[0.6rem] text-faint">
          Hooks saved to .kageha/hooks.json
        </p>
      </div>
    </div>
  );
}
