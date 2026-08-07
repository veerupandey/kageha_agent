import { useAppStore } from "../../store";
import { ApprovalBanner } from "../ApprovalBanner";
import { MessageList } from "../MessageList";
import { MiniComposer } from "./MiniComposer";
import { SuggestedFollowUps } from "./SuggestedFollowUps";

export function ConversationPanel() {
  const messages = useAppStore((s) => s.messages);
  const sendMessage = useAppStore((s) => s.sendMessage);
  const setDraft = useAppStore((s) => s.setDraft);
  const error = useAppStore((s) => s.error);
  const clearError = useAppStore((s) => s.clearError);

  // Extract suggested follow-ups from the last assistant message if available
  // For now, use static suggestions based on context
  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const suggestions: string[] = [];
  if (lastAssistant && !lastAssistant.streaming) {
    // Basic heuristic: suggest refinements if last message was substantive
    if (lastAssistant.text && lastAssistant.text.length > 100) {
      suggestions.push("Tell me more about this");
      suggestions.push("Can you make it simpler?");
      suggestions.push("Generate variations of this");
    }
  }

  const handleFollowUp = (text: string) => {
    setDraft(text);
    void sendMessage(text);
  };

  return (
    <div className="ka-conversation-panel flex min-h-0 flex-1 flex-col">
      <section
        className="ka-conversation min-h-0 flex-1 overflow-y-auto px-4 py-4"
        id="conversation"
      >
        <MessageList messages={messages} />
        {messages.length > 0 && (
          <SuggestedFollowUps
            suggestions={suggestions}
            onSelect={handleFollowUp}
          />
        )}
      </section>

      <ApprovalBanner />

      {error && (
        <p
          className="flex items-start gap-3 border-t border-[var(--color-danger)]/20 bg-[var(--color-danger-soft)] px-4 py-2 text-sm text-[var(--color-danger)]"
          role="alert"
        >
          <span className="min-w-0 flex-1">{error}</span>
          <button
            type="button"
            className="shrink-0 underline-offset-2 hover:underline"
            onClick={clearError}
          >
            Dismiss
          </button>
        </p>
      )}

      <div className="shrink-0 px-4 pb-4">
        <MiniComposer />
      </div>
    </div>
  );
}
