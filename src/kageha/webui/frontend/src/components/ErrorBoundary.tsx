import { Component, type ErrorInfo, type ReactNode } from "react";
import { useAppStore } from "../store";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

function BoundaryFallback({
  error,
  onReload,
  onNewChat,
}: {
  error: Error;
  onReload: () => void;
  onNewChat: () => void;
}) {
  return (
    <div className="error-boundary" role="alert">
      <div className="error-boundary-card">
        <p className="eyebrow">Something went wrong</p>
        <h1 className="error-boundary-title">Kageha hit an unexpected error</h1>
        <p className="error-boundary-message muted">
          {error.message || "Unknown render error"}
        </p>
        <div className="error-boundary-actions">
          <button type="button" className="btn primary" onClick={onReload}>
            Reload
          </button>
          <button type="button" className="btn ghost" onClick={onNewChat}>
            New chat
          </button>
        </div>
      </div>
    </div>
  );
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("WebUI ErrorBoundary", error, info.componentStack);
  }

  private handleReload = () => {
    window.location.reload();
  };

  private handleNewChat = () => {
    this.setState({ error: null });
    void useAppStore
      .getState()
      .newChat()
      .catch(() => {
        window.location.reload();
      });
  };

  render() {
    if (this.state.error) {
      return (
        <BoundaryFallback
          error={this.state.error}
          onReload={this.handleReload}
          onNewChat={this.handleNewChat}
        />
      );
    }
    return this.props.children;
  }
}
