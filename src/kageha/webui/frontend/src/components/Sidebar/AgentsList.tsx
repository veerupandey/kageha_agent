import { StatusDot } from "../shared/StatusDot";
import type { DotStatus } from "../shared/StatusDot";

export interface AgentEntry {
  id: string;
  name: string;
  color: DotStatus;
  description?: string;
}

interface AgentsListProps {
  agents: AgentEntry[];
  onSelect: (agent: AgentEntry) => void;
}

const DEFAULT_AGENTS: AgentEntry[] = [
  { id: "command-center", name: "Command Center", color: "active" },
];

export function AgentsList({ agents, onSelect }: AgentsListProps) {
  const items = agents.length > 0 ? agents : DEFAULT_AGENTS;

  return (
    <div className="px-3 py-2">
      <div className="mb-1 flex items-center justify-between">
        <p className="text-xs font-semibold uppercase tracking-wide text-faint">
          Agents
        </p>
        <button
          type="button"
          className="text-xs text-faint hover:text-ink"
        >
          ▾
        </button>
      </div>
      <ul className="space-y-0.5">
        {items.map((agent) => (
          <li key={agent.id}>
            <button
              type="button"
              className="ka-sidebar-item w-full"
              onClick={() => onSelect(agent)}
            >
              <StatusDot status={agent.color} />
              <span className="min-w-0 flex-1 truncate">{agent.name}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
