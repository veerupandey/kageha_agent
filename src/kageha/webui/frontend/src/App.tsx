import { useCallback, useEffect, useState } from "react";
import type { DrawerName } from "./api/types";
import { ArtifactsDrawer } from "./components/ArtifactsDrawer";
import { CommandPalette } from "./components/CommandPalette";
import { ConnectionBanner } from "./components/ConnectionBanner";
import { DropOverlay } from "./components/DropOverlay";
import { JobsDrawer } from "./components/JobsDrawer";
import { LabsDrawer } from "./components/LabsDrawer";
import { MemoryDrawer } from "./components/MemoryDrawer";
import { SessionsRail } from "./components/SessionsRail";
import { SettingsDrawer } from "./components/SettingsDrawer";
import { Stage } from "./components/Stage";
import { Toasts } from "./components/Toasts";
import { useAppStore } from "./store";
import "./styles/legacy.css";
import "./styles/react.css";

const LAZY_DRAWERS: DrawerName[] = [
  "artifacts",
  "memory",
  "jobs",
  "labs",
  "settings",
];

export default function App() {
  const boot = useAppStore((s) => s.boot);
  const drawers = useAppStore((s) => s.drawers);
  const closeDrawer = useAppStore((s) => s.closeDrawer);
  const addPendingFiles = useAppStore((s) => s.addPendingFiles);
  const setConnectionOnline = useAppStore((s) => s.setConnectionOnline);

  const [paletteOpen, setPaletteOpen] = useState(false);
  const [dropping, setDropping] = useState(false);
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [mounted, setMounted] = useState<Partial<Record<DrawerName, boolean>>>(
    {},
  );

  useEffect(() => {
    boot().catch((err) => console.warn("boot failed", err));
  }, [boot]);

  useEffect(() => {
    for (const name of LAZY_DRAWERS) {
      if (drawers[name]) {
        setMounted((m) => (m[name] ? m : { ...m, [name]: true }));
      }
    }
  }, [drawers]);

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
    (
      [
        "design",
        "artifacts",
        "memory",
        "jobs",
        "labs",
        "workbench",
        "settings",
      ] as const
    ).forEach((name) => {
      if (useAppStore.getState().drawers[name]) closeDrawer(name);
    });
  }, [closeDrawer]);

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

  const anyDrawerOpen =
    drawers.artifacts ||
    drawers.memory ||
    drawers.jobs ||
    drawers.labs ||
    drawers.settings;

  return (
    <>
      <div className="atmosphere" aria-hidden="true" />
      <ConnectionBanner />
      <div className="shell" id="app">
        <SessionsRail
          open={sessionsOpen}
          onClose={() => setSessionsOpen(false)}
        />
        <Stage
          sessionsOpen={sessionsOpen}
          onToggleSessions={() => setSessionsOpen((v) => !v)}
        />
        {mounted.artifacts ? <ArtifactsDrawer /> : null}
        {mounted.memory ? <MemoryDrawer /> : null}
        {mounted.jobs ? <JobsDrawer /> : null}
        {mounted.labs ? <LabsDrawer /> : null}
        {mounted.settings ? <SettingsDrawer /> : null}
      </div>

      <div
        className="backdrop"
        id="backdrop"
        hidden={!anyDrawerOpen && !sessionsOpen}
        onClick={closeAllOverlays}
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
