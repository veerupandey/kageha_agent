import { useCallback, useState } from "react";
import { cn } from "../lib/cn";
import { useAppStore } from "../store";
import { CommandCenter } from "./CommandCenter/CommandCenter";
import { ArtifactLightbox } from "./Lightbox/ArtifactLightbox";
import { Sidebar } from "./Sidebar/Sidebar";
import { ThreadView } from "./ThreadView/ThreadView";

/**
 * AppShell — 3-panel layout.
 * Switches between CommandCenter (home) and ThreadView (active session).
 */
export function AppShell() {
  const sessionId = useAppStore((s) => s.sessionId);
  const canvasItems = useAppStore((s) => s.canvasItems);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [lightboxPath, setLightboxPath] = useState<string | null>(null);

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

  const isHome = !sessionId;

  return (
    <>
      <div className="flex h-full min-h-0 bg-canvas text-ink" data-ui="kageha">
        {/* Sidebar */}
        <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

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
          <div className="flex h-11 shrink-0 items-center border-b border-[var(--color-line)] px-3 md:hidden">
            <button
              type="button"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted hover:text-ink"
              aria-label="Open menu"
              onClick={() => setSidebarOpen(true)}
            >
              ☰
            </button>
            <span className="ml-2 text-sm font-medium text-ink">Kageha</span>
          </div>

          {/* Content area */}
          {isHome ? (
            <CommandCenter />
          ) : (
            <ThreadView onOpenLightbox={handleOpenLightbox} />
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
