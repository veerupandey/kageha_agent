const RESOURCES = [
  { id: "skills", label: "Skills", icon: "⚡" },
  { id: "memories", label: "Memories", icon: "🧠" },
  { id: "projects", label: "Projects", icon: "📁" },
  { id: "library", label: "Library", icon: "📚" },
] as const;

interface ResourcesNavProps {
  onNavigate?: (id: string) => void;
}

export function ResourcesNav({ onNavigate }: ResourcesNavProps) {
  return (
    <div className="px-3 py-2">
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-faint">
        Resources
      </p>
      <ul className="space-y-0.5">
        {RESOURCES.map((r) => (
          <li key={r.id}>
            <button
              type="button"
              className="ka-sidebar-item w-full"
              onClick={() => onNavigate?.(r.id)}
            >
              <span className="text-sm" aria-hidden="true">{r.icon}</span>
              <span>{r.label}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
