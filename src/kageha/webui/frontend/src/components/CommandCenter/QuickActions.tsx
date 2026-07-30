import { useAppStore } from "../../store";

const SUGGESTIONS = [
  { label: "Research a topic", icon: "🔍", prompt: "/browser research " },
  { label: "Write code", icon: "💻", prompt: "Write code for " },
  { label: "Browse the web", icon: "🌐", prompt: "/browser " },
  { label: "Analyze files", icon: "📊", prompt: "Analyze " },
  { label: "Generate images", icon: "🎨", prompt: "Generate an image of " },
  { label: "More...", icon: "⊕", prompt: "/" },
];

export function QuickActions() {
  const setDraft = useAppStore((s) => s.setDraft);

  return (
    <div className="flex flex-wrap justify-center gap-2">
      {SUGGESTIONS.map((s) => (
        <button
          key={s.label}
          type="button"
          className="ka-pill"
          onClick={() => setDraft(s.prompt)}
        >
          <span aria-hidden="true">{s.icon}</span>
          {s.label}
        </button>
      ))}
    </div>
  );
}
