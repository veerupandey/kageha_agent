import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { Icon } from "../../lib/icons";

interface BrainRule {
  name: string;
  globs: string[];
}

interface ProjectResponse {
  project_root: string;
  git: boolean;
  brain: {
    root_file: string;
    rules: BrainRule[];
    commands: string[];
    rendered: string;
  } | null;
  hooks: { event: string; matcher: string; command: string; http: string }[];
  worktrees: unknown[];
}

export function ProjectBrainPanel({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<ProjectResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchBrain = useCallback(async () => {
    try {
      const res = await api<ProjectResponse>("/api/project");
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load project brain");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchBrain();
  }, [fetchBrain]);

  return (
    <div className="flex h-full flex-col bg-surface">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold text-ink">Project Brain</h2>
        <button
          type="button"
          className="ka-icon-btn h-7 w-7"
          onClick={onClose}
          aria-label="Close"
        >
          <Icon.Delete size={15} />
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="border-b border-[var(--color-danger)]/20 bg-[var(--color-danger-soft)] px-4 py-2 text-xs text-[var(--color-danger)]">
          {error}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-8 text-sm text-muted">
            Loading…
          </div>
        ) : !data?.brain ? (
          <div className="flex flex-col items-center justify-center px-4 py-8 text-center">
            <Icon.Brain size={28} className="mb-2 text-faint" />
            <p className="text-sm text-muted">No project brain configured</p>
            <p className="mt-1 text-xs text-faint">
              Add an <code className="rounded bg-canvas px-1 py-0.5 font-mono text-[0.65rem]">AGENTS.md</code>,{" "}
              <code className="rounded bg-canvas px-1 py-0.5 font-mono text-[0.65rem]">.cursorrules</code>, or
              rules under <code className="rounded bg-canvas px-1 py-0.5 font-mono text-[0.65rem]">.kageha/rules/</code>{" "}
              to give the agent persistent project context.
            </p>
          </div>
        ) : (
          <div className="space-y-4 px-4 py-3">
            {/* Root file */}
            {data.brain.root_file && (
              <div>
                <p className="mb-1.5 flex items-center gap-1.5 text-[0.7rem] font-semibold uppercase tracking-wide text-faint">
                  <Icon.Attach size={12} />
                  Root instructions
                </p>
                <p className="rounded-md bg-canvas px-2.5 py-1.5 font-mono text-xs text-ink">
                  {data.brain.root_file}
                </p>
              </div>
            )}

            {/* Rules */}
            {data.brain.rules.length > 0 && (
              <div>
                <p className="mb-1.5 flex items-center gap-1.5 text-[0.7rem] font-semibold uppercase tracking-wide text-faint">
                  <Icon.Memories size={12} />
                  Rules ({data.brain.rules.length})
                </p>
                <ul className="space-y-1">
                  {data.brain.rules.map((rule) => (
                    <li
                      key={rule.name}
                      className="flex items-start justify-between gap-2 rounded-md bg-canvas px-2.5 py-1.5"
                    >
                      <span className="min-w-0 flex-1 truncate text-xs font-medium text-ink">
                        {rule.name}
                      </span>
                      {rule.globs.length > 0 && (
                        <span className="shrink-0 font-mono text-[0.62rem] text-faint">
                          {rule.globs.join(", ")}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Commands */}
            {data.brain.commands.length > 0 && (
              <div>
                <p className="mb-1.5 flex items-center gap-1.5 text-[0.7rem] font-semibold uppercase tracking-wide text-faint">
                  <Icon.Skills size={12} />
                  Commands ({data.brain.commands.length})
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {data.brain.commands.map((cmd) => (
                    <span
                      key={cmd}
                      className="rounded-md bg-accent/10 px-2 py-0.5 font-mono text-[0.68rem] text-accent"
                    >
                      {cmd}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Rendered preview */}
            {data.brain.rendered && (
              <div>
                <p className="mb-1.5 flex items-center gap-1.5 text-[0.7rem] font-semibold uppercase tracking-wide text-faint">
                  <Icon.Activity size={12} />
                  Rendered context
                </p>
                <details className="group">
                  <summary className="cursor-pointer text-xs text-accent hover:underline">
                    Show rendered brain ({data.brain.rendered.length} chars)
                  </summary>
                  <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded-md bg-canvas p-3 font-mono text-[0.68rem] leading-relaxed text-muted">
                    {data.brain.rendered}
                  </pre>
                </details>
              </div>
            )}

            {/* Summary stats */}
            <div className="flex flex-wrap gap-3 border-t border-line pt-3 text-[0.68rem] text-faint">
              <span className="inline-flex items-center gap-1">
                <Icon.Worktrees size={12} /> {data.worktrees.length} worktrees
              </span>
              <span className="inline-flex items-center gap-1">
                <Icon.Hooks size={12} /> {data.hooks.length} hooks
              </span>
              <span>{data.git ? "git ✓" : "not a git repo"}</span>
            </div>
          </div>
        )}
      </div>

      {/* Footer */}
      {data?.project_root && (
        <div className="border-t border-line px-4 py-2">
          <p className="truncate text-[0.62rem] text-faint" title={data.project_root}>
            {data.project_root}
          </p>
        </div>
      )}
    </div>
  );
}
