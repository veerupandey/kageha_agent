import type { ComponentType } from "react";
import { Icon, type IconProps } from "../../lib/icons";

const RESOURCES: { id: string; label: string; Icon: ComponentType<IconProps> }[] = [
  { id: "skills", label: "Skills", Icon: Icon.Skills },
  { id: "memories", label: "Memories", Icon: Icon.Memories },
  { id: "projects", label: "Projects", Icon: Icon.Projects },
  { id: "library", label: "Library", Icon: Icon.Library },
];

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
        {RESOURCES.map((r) => {
          const ItemIcon = r.Icon;
          return (
            <li key={r.id}>
              <button
                type="button"
                className="ka-sidebar-item w-full"
                onClick={() => onNavigate?.(r.id)}
              >
                <ItemIcon size={16} />
                <span>{r.label}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
