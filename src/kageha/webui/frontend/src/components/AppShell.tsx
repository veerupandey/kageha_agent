import { useCallback, useEffect, useState } from "react";
import { cn } from "../lib/cn";
import { Icon } from "../lib/icons";
import { useAppStore } from "../store";
import { CommandCenter } from "./CommandCenter/CommandCenter";
import { ArtifactLightbox } from "./Lightbox/ArtifactLightbox";
import { HooksPanel } from "./Sidebar/HooksPanel";
import { JobsPanel } from "./Sidebar/JobsPanel";
import { ProjectBrainPanel } from "./Sidebar/ProjectBrainPanel";
import { Sidebar } from "./Sidebar/Sidebar";
import { ThreadView } from "./ThreadView/ThreadView";
import { WorktreesPanel } from "./Sidebar/WorktreesPanel";

type ActivePanel = null | "jobs" | "hooks" | "worktrees" | "brain";

/**
 * AppShell — 3-panel layout with collapsible sidebars.
 * Switches between CommandCenter (home) and ThreadView (active session).
 */
export function AppShell() {
  const sessionId = useAppStore((s) => s.sessionId);
  const canvasItems = useAppStore((s) => s.canvasItems);
  const newChat = useAppStore((s) => s.newChat);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [rightPanelCollapsed, setRightPanelCollapsed] = useState(false);
  const [activePanel, setActivePanel] = useState<ActivePanel>(null);
  const [lightboxPath, setLightboxPath] = useState<string | null>(null);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const meta = e.metaKey || e.ctrlKey;
      // Cmd+N: new thread
      if (meta && e.key === "n" && !e.shiftKey) {
        e.preventDefault();
        void newChat();
        return;
      }
      // Cmd+K: focus command palette / input
      if (meta && e.key === "k") {
        e.preventDefault();
        const input = document.getElementById("message-input") as HTMLTextAreaElement | null;
        input?.focus();
        return;
      }
      // Cmd+B: toggle sidebar
      if (meta && e.key === "b") {
        e.preventDefault();
        setSidebarCollapsed((v) => !v);
        return;
      }
      // Cmd+\\: toggle right panel
      if (meta && e.key === "\\") {
        e.preventDefault();
        setRightPanelCollapsed((v) => !v);
        return;
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [newChat]);

  const handleOpenLightbox = useCallback((path: string) => {
    setLightboxPath(path);
  }, []);

  const handleCloseLightbox = useCallback(() => {
    setLightboxPath(null);
  }, []);

  const handleNavigateLightbox = useCallback(
    (direction: "prev" | "next") => {
      if (!lightboxPath || canvasItems.length === 0) return;
      const idx = canvasItems.findIndex((i) => i.path === lightboxPath);
      if (idx === -1) return;
      const next =
        direction === "next"
          ? (idx + 1) % canvasItems.length
          : (idx - 1 + canvasItems.length) % canvasItems.length;
      setLightboxPath(canvasItems[next].path);
    },
    [lightboxPath, canvasItems],
  );

  const handleAgentSelect = useCallback((agentId: string) => {
    if (agentId === "jobs") setActivePanel("jobs");
    else if (agentId === "hooks") setActivePanel("hooks");
    else if (agentId === "worktrees") setActivePanel("worktrees");
    else if (agentId === "project_brain") setActivePanel("brain");
    else setActivePanel(null);
  }, []);

  const isHome = !sessionId && !activePanel;

  return (
    <>
      <div className="app-shell flex h-full min-h-0 bg-canvas text-ink" data-ui="kageha">
        {/* Sidebar */}
        <Sidebar
          open={sidebarOpen}
          collapsed={sidebarCollapsed}
          onClose={() => setSidebarOpen(false)}
          onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
          onAgentSelect={handleAgentSelect}
        />

        {/* Mobile sidebar backdrop */}
        <div
          className={cn(
            "fixed inset-0 z-40 bg-black/40 md:hidden",
            sidebarOpen ? "block" : "hidden",
          )}
          onClick={() => setSidebarOpen(false)}
          aria-hidden={!sidebarOpen}
        />

        {/* Main content */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {/* Mobile header with hamburger */}
          <div className="ka-mobile-header flex h-11 shrink-0 items-center border-b border-[var(--color-line)] px-3 md:hidden">
            <button
              type="button"
              className="ka-icon-btn h-8 w-8 text-muted"
              aria-label="Open menu"
              onClick={() => setSidebarOpen(true)}
            >
              <Icon.Menu size={18} />
            </button>
            <span className="ml-2 text-sm font-medium text-ink">Kageha</span>
          </div>

          {/* Content area */}
          {activePanel === "jobs" ? (
            <JobsPanel onClose={() => setActivePanel(null)} />
          ) : activePanel === "hooks" ? (
            <HooksPanel onClose={() => setActivePanel(null)} />
          ) : activePanel === "worktrees" ? (
            <WorktreesPanel onClose={() => setActivePanel(null)} />
          ) : activePanel === "brain" ? (
            <ProjectBrainPanel onClose={() => setActivePanel(null)} />
          ) : isHome ? (
            <CommandCenter />
          ) : (
            <ThreadView
              onOpenLightbox={handleOpenLightbox}
              rightPanelCollapsed={rightPanelCollapsed}
              onToggleRightPanel={() => setRightPanelCollapsed(!rightPanelCollapsed)}
            />
          )}
        </div>
      </div>

      {/* Lightbox overlay */}
      <ArtifactLightbox
        itemPath={lightboxPath}
        onClose={handleCloseLightbox}
        onNavigate={handleNavigateLightbox}
      />
    </>
  );
}
