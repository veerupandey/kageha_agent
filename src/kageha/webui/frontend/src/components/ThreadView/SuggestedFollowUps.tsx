interface SuggestedFollowUpsProps {
  suggestions: string[];
  onSelect: (text: string) => void;
}

export function SuggestedFollowUps({ suggestions, onSelect }: SuggestedFollowUpsProps) {
  if (!suggestions.length) return null;

  return (
    <div className="space-y-1.5 pt-3">
      <p className="text-xs font-medium text-muted">Suggested follow-ups</p>
      {suggestions.map((s, i) => (
        <button
          key={i}
          type="button"
          className="ka-followup w-full"
          onClick={() => onSelect(s)}
        >
          <span className="text-xs text-faint" aria-hidden="true">💡</span>
          <span className="min-w-0 flex-1 text-left">{s}</span>
          <span className="ka-followup-arrow">→</span>
        </button>
      ))}
    </div>
  );
}
