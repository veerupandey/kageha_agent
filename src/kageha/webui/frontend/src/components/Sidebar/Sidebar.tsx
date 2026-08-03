import { useMemo, useState } from "react";
import { cn } from "../../lib/cn";
import { useAppStore } from "../../store";
import type { AgentEntry } from "./AgentsList";
import { AgentsList } from "./AgentsList";
import { RecentThreadsList } from "./RecentThreadsList";
import { ResourcesNav } from "./ResourcesNav";
import { SidebarFooter } from "./SidebarFooter";
import { SidebarHeader } from "./SidebarHeader";
import { SidebarSearch } from "./SidebarSearch";

interface SidebarProps {
  open: boolean;
  collapsed: boolean;
  onClose?: () => void;
  onToggleCollapse?: () => void;
  onAgentSelect?: (agentId: string) => void;
}

export function Sidebar({ open, collapsed, onClose, onToggleCollapse, onAgentSelect }: SidebarProps) {
  const sessions = useAppStore((s) => s.sessions);
  const sessionId = useAppStore((s) => s.sessionId);
  const openSession = useAppStore((s) => s.openSession);
  const newChat = useAppStore((s) => s.newChat);
  const meta = useAppStore((s) => s.meta);
  const [query, setQuery] = useState("");

  const agents: AgentEntry[] = useMemo(() => {
    const list: AgentEntry[] = [
      { id: "command-center", name: "Command Center", color: "active" },
    ];
    // Derive agents from meta features/skills if available
    if (meta?.features) {
      for (const [key, enabled] of Object.entries(meta.features)) {
        if (enabled && key !== "sandbox") {
          list.push({
            id: key,
            name: key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
            color: "idle",
          });
        }
      }
    }
    return list;
  }, [meta]);

  const filteredSessions = useMemo(() => {
    if (!query.trim()) return sessions;
    const q = query.toLowerCase();
    return sessions.filter(
      (s) =>
        (s.title || "").toLowerCase().includes(q) ||
        s.session_id.toLowerCase().includes(q),
    );
  }, [sessions, query]);

  // Collapsed rail: show only icons
  if (collapsed) {
    return (
      <aside
        className="hidden md:flex flex-col items-center w-12 border-r border-[var(--color-line)] bg-surface py-3 gap-2"
        aria-label="Collapsed sidebar"
      >
        <button
          type="button"
          className="inline-flex h-8 w-8 items-center justify-center rounded-md text-faint hover:bg-line/50 hover:text-ink transition-colors"
          aria-label="Expand sidebar"
          title="Expand sidebar"
          onClick={onToggleCollapse}
        >
          ⊞
        </button>
        <button
          type="button"
          className="inline-flex h-8 w-8 items-center justify-center rounded-md text-accent hover:bg-accent-soft transition-colors"
          aria-label="New thread"
          title="New thread"
          onClick={() => { newChat().catch(() => {}); }}
        >
          ✎
        </button>
        <div className="w-6 border-t border-[var(--color-line)] my-1" />
        {agents.slice(0, 6).map((agent) => (
          <button
            key={agent.id}
            type="button"
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-sm hover:bg-line/50 transition-colors"
            title={agent.name}
            onClick={() => {
              if (agent.id === "command-center") {
                // Go home
              }
            }}
          >
            {agent.id === "command-center" ? "⚙️" :
             agent.id === "jobs" ? "📋" :
             agent.id === "worktrees" ? "🌿" :
             agent.id === "project_brain" ? "🧠" :
             agent.id === "hooks" ? "🪝" :
             agent.id === "attach" ? "📎" : "·"}
          </button>
        ))}
      </aside>
    );
  }

  return (
    <aside
      className={cn(
        "ka-sidebar fixed inset-y-0 left-0 z-50 flex w-[250px] flex-col overflow-hidden transition-transform md:static md:z-0 md:translate-x-0",
        open ? "translate-x-0" : "-translate-x-full md:translate-x-0",
      )}
      aria-label="Main navigation"
    >
      <SidebarHeader
        onNewThread={() => {
          newChat().then(() => onClose?.()).catch(() => {});
        }}
        onCollapse={onToggleCollapse}
      />
      <SidebarSearch query={query} onChange={setQuery} />

      <div className="min-h-0 flex-1 overflow-y-auto">
        <AgentsList
          agents={agents}
          onSelect={(agent) => {
            if (agent.id === "command-center") {
              // Go home — deselect session
            }
            onAgentSelect?.(agent.id);
            onClose?.();
          }}
        />
        <div className="mx-3 border-t border-[var(--color-line)]" />
        <RecentThreadsList
          sessions={filteredSessions}
          activeSessionId={sessionId}
          onOpen={(id) => {
            openSession(id).then(() => onClose?.()).catch(() => {});
          }}
        />
        <div className="mx-3 border-t border-[var(--color-line)]" />
        <ResourcesNav />
      </div>

      <SidebarFooter />
    </aside>
  );
}
