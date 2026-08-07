import { useState, type ComponentType } from "react";
import { Icon, type IconProps } from "../../lib/icons";
import { StatusDot } from "../shared/StatusDot";
import type { DotStatus } from "../shared/StatusDot";

export interface AgentEntry {
  id: string;
  name: string;
  color: DotStatus;
  description?: string;
  icon?: ComponentType<IconProps>;
}

interface AgentsListProps {
  agents: AgentEntry[];
  onSelect: (agent: AgentEntry) => void;
}

const AGENT_ICONS: Record<string, ComponentType<IconProps>> = {
  "command-center": Icon.CommandCenter,
  jobs: Icon.Jobs,
  worktrees: Icon.Worktrees,
  project_brain: Icon.Brain,
  hooks: Icon.Hooks,
  attach: Icon.Attach,
};

const AGENT_DESCRIPTIONS: Record<string, string> = {
  "command-center": "Main agent interface",
  jobs: "Background & queued runs",
  worktrees: "Git worktree branches",
  project_brain: "Project rules & context",
  hooks: "Pre/post tool automation",
  attach: "File attachments",
};

const DEFAULT_AGENTS: AgentEntry[] = [
  { id: "command-center", name: "Command Center", color: "active", icon: Icon.CommandCenter, description: "Main agent interface" },
];

export function AgentsList({ agents, onSelect }: AgentsListProps) {
  const [collapsed, setCollapsed] = useState(false);
  const items = agents.length > 0 ? agents : DEFAULT_AGENTS;

  // Enrich items with icons and descriptions
  const enriched = items.map((agent) => ({
    ...agent,
    icon: agent.icon || AGENT_ICONS[agent.id] || Icon.CommandCenter,
    description: agent.description || AGENT_DESCRIPTIONS[agent.id] || "",
  }));

  return (
    <div className="px-3 py-2">
      <button
        type="button"
        className="mb-1 flex w-full items-center justify-between"
        onClick={() => setCollapsed(!collapsed)}
      >
        <p className="text-xs font-semibold uppercase tracking-wide text-faint">
          Agents
        </p>
        <span
          className="text-faint transition-transform duration-150"
          style={{ transform: collapsed ? "rotate(-90deg)" : undefined }}
        >
          <Icon.Chevron size={14} />
        </span>
      </button>
      {!collapsed && (
        <ul className="space-y-0.5">
          {enriched.map((agent) => {
            const AgentIcon = agent.icon!;
            return (
              <li key={agent.id}>
                <button
                  type="button"
                  className="ka-sidebar-item group w-full"
                  onClick={() => onSelect(agent)}
                  title={agent.description}
                >
                  <AgentIcon size={16} />
                  <span className="min-w-0 flex-1 truncate text-left">{agent.name}</span>
                  <StatusDot status={agent.color} />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
