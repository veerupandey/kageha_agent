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
  onClose?: () => void;
}

export function Sidebar({ open, onClose }: SidebarProps) {
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
      />
      <SidebarSearch query={query} onChange={setQuery} />

      <div className="min-h-0 flex-1 overflow-y-auto">
        <AgentsList
          agents={agents}
          onSelect={(agent) => {
            if (agent.id === "command-center") {
              // Go home — deselect session
            }
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
