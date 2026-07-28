import { useCallback, useEffect, useState } from "react";
import { CommandPalette } from "./components/CommandPalette";
import { ConnectionBanner } from "./components/ConnectionBanner";
import { DropOverlay } from "./components/DropOverlay";
import { SessionsRail } from "./components/SessionsRail";
import { Stage } from "./components/Stage";
import { Toasts } from "./components/Toasts";
import { cn } from "./lib/cn";
import { useAppStore } from "./store";

export default function App() {
  const boot = useAppStore((s) => s.boot);
  const addPendingFiles = useAppStore((s) => s.addPendingFiles);
  const setConnectionOnline = useAppStore((s) => s.setConnectionOnline);

  const [paletteOpen, setPaletteOpen] = useState(false);
  const [dropping, setDropping] = useState(false);
  const [sessionsOpen, setSessionsOpen] = useState(false);

  useEffect(() => {
    boot().catch((err) => console.warn("boot failed", err));
  }, [boot]);

  useEffect(() => {
    const onOnline = () => setConnectionOnline(true);
    const onOffline = () => setConnectionOnline(false);
    setConnectionOnline(navigator.onLine !== false);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, [setConnectionOnline]);

  const closeAllOverlays = useCallback(() => {
    setPaletteOpen(false);
    setSessionsOpen(false);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const meta = e.metaKey || e.ctrlKey;
      if (meta && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
        return;
      }
      if (e.key === "Escape") {
        if (paletteOpen) {
          setPaletteOpen(false);
          return;
        }
        if (sessionsOpen) {
          setSessionsOpen(false);
          return;
        }
        closeAllOverlays();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [paletteOpen, sessionsOpen, closeAllOverlays]);

  useEffect(() => {
    const app = document.getElementById("app");
    if (!app) return;
    if (paletteOpen) app.setAttribute("inert", "");
    else app.removeAttribute("inert");
    return () => app.removeAttribute("inert");
  }, [paletteOpen]);

  useEffect(() => {
    let dragDepth = 0;
    const hasFiles = (e: DragEvent) =>
      Array.from(e.dataTransfer?.types || []).includes("Files");

    const onDragEnter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth += 1;
      setDropping(true);
    };
    const onDragLeave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth = Math.max(0, dragDepth - 1);
      if (dragDepth === 0) setDropping(false);
    };
    const onDragOver = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
    };
    const onDrop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth = 0;
      setDropping(false);
      if (e.dataTransfer?.files?.length) {
        addPendingFiles(e.dataTransfer.files);
      }
    };

    window.addEventListener("dragenter", onDragEnter);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onDragEnter);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("drop", onDrop);
    };
  }, [addPendingFiles]);

  return (
    <>
      <ConnectionBanner />
      <div
        className="flex h-full min-h-0 bg-canvas text-ink"
        id="app"
      >
        <SessionsRail
          open={sessionsOpen}
          onClose={() => setSessionsOpen(false)}
        />
        <Stage onToggleSessions={() => setSessionsOpen((v) => !v)} />
      </div>

      <div
        className={cn(
          "fixed inset-0 z-40 bg-ink/25 md:hidden",
          sessionsOpen ? "block" : "hidden",
        )}
        id="backdrop"
        onClick={closeAllOverlays}
        aria-hidden={!sessionsOpen}
      />

      <DropOverlay visible={dropping} />
      <Toasts />
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onAttach={() => {
          (
            document.getElementById("file-input") as HTMLInputElement | null
          )?.click();
        }}
        onFocusModel={() => {
          const el = document.getElementById(
            "model-input",
          ) as HTMLInputElement | null;
          el?.focus();
          el?.select();
        }}
      />
    </>
  );
}
