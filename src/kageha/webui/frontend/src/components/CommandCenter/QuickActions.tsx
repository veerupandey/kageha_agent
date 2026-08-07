import type { ComponentType } from "react";
import { Icon, type IconProps } from "../../lib/icons";
import { useAppStore } from "../../store";

const SUGGESTIONS: {
  label: string;
  icon: ComponentType<IconProps>;
  prompt: string;
}[] = [
  { label: "Research a topic", icon: Icon.Search, prompt: "Research " },
  { label: "Write code", icon: Icon.Compose, prompt: "Write code for " },
  { label: "Browse the web", icon: Icon.Activity, prompt: "Go to " },
  { label: "Analyze files", icon: Icon.Projects, prompt: "Analyze " },
  { label: "Generate images", icon: Icon.Logo, prompt: "Generate an image of " },
  { label: "More...", icon: Icon.Plus, prompt: "/" },
];

export function QuickActions() {
  const setDraft = useAppStore((s) => s.setDraft);

  return (
    <div className="flex flex-wrap justify-center gap-2">
      {SUGGESTIONS.map((s) => {
        const ActionIcon = s.icon;
        return (
          <button
            key={s.label}
            type="button"
            className="ka-pill"
            onClick={() => setDraft(s.prompt)}
          >
            <ActionIcon size={14} />
            {s.label}
          </button>
        );
      })}
    </div>
  );
}
