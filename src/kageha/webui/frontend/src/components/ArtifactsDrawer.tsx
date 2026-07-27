import { useEffect, useMemo, useRef, useState } from "react";
import { useAppStore } from "../store";
import { useFocusTrap } from "../lib/focusTrap";
import {
  artifactFileUrl,
  canvasKindForPath,
  fileBasename,
  isPreviewableKind,
  type CanvasItem,
} from "../lib/artifactMedia";
import { MediaCanvas, buildCanvasItems } from "./MediaCanvas";

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function ArtifactsDrawer() {
  const open = useAppStore((s) => s.drawers.artifacts);
  const artifacts = useAppStore((s) => s.artifacts);
  const sessionId = useAppStore((s) => s.sessionId);
  const closeDrawer = useAppStore((s) => s.closeDrawer);
  const refreshArtifacts = useAppStore((s) => s.refreshArtifacts);
  const drawerRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [canvasItems, setCanvasItems] = useState<CanvasItem[]>([]);
  const [canvasIndex, setCanvasIndex] = useState(0);

  useFocusTrap(open, drawerRef, { initialFocusRef: closeRef });

  useEffect(() => {
    if (open) void refreshArtifacts().catch(() => {});
  }, [open, sessionId, refreshArtifacts]);

  useEffect(() => {
    if (!artifacts.length) {
      setSelectedPath(null);
      return;
    }
    if (
      selectedPath &&
      artifacts.some((a) => String(a.path || a.name) === selectedPath)
    ) {
      return;
    }
    setSelectedPath(
      String(artifacts[0]?.path || artifacts[0]?.name || "") || null,
    );
  }, [artifacts, selectedPath]);

  const gallery = useMemo(
    () =>
      buildCanvasItems(artifacts, sessionId, artifactFileUrl, canvasKindForPath),
    [artifacts, sessionId],
  );

  const selected = artifacts.find(
    (a) => String(a.path || a.name) === selectedPath,
  );
  const selectedUrl = selected
    ? artifactFileUrl(sessionId, String(selected.path || ""), selected.url)
    : undefined;
  const selectedKind = selected
    ? canvasKindForPath(String(selected.path || ""), selected.kind)
    : null;

  const openCanvasAt = (path: string) => {
    const url = artifactFileUrl(sessionId, path);
    if (!url) return;
    const kind = canvasKindForPath(path);
    let items = gallery.slice();
    let idx = items.findIndex((it) => it.url === url || it.caption === path);
    if (idx < 0) {
      items = [{ url, kind, caption: path }, ...items];
      idx = 0;
    }
    setCanvasItems(items);
    setCanvasIndex(idx);
    setCanvasOpen(true);
  };

  return (
    <>
      <aside
        ref={drawerRef}
        className={`artifacts drawer${open ? " open" : ""}`}
        id="artifact-drawer"
        aria-hidden={open ? "false" : "true"}
      >
        <header className="artifacts-head">
          <div>
            <p className="eyebrow">Workspace</p>
            <h2 className="drawer-title">Session artifacts</h2>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="btn ghost"
            id="btn-close-artifacts"
            aria-label="Close"
            onClick={() => closeDrawer("artifacts")}
          >
            Close
          </button>
        </header>

        {!artifacts.length ? (
          <p className="artifacts-empty" id="artifacts-empty">
            No artifacts in this session yet.
          </p>
        ) : (
          <>
            {selected && selectedUrl && selectedKind ? (
              <div className="artifact-preview" id="artifact-preview">
                {selectedKind === "image" ? (
                  <img
                    src={selectedUrl}
                    alt={String(selected.path)}
                    onClick={() => openCanvasAt(String(selected.path))}
                  />
                ) : null}
                {selectedKind === "video" ? (
                  <video
                    controls
                    playsInline
                    preload="metadata"
                    src={selectedUrl}
                  />
                ) : null}
                {selectedKind === "pdf" ? (
                  <p className="preview-doc-note">
                    PDF — open in canvas to preview, or download.
                  </p>
                ) : null}
                {(selectedKind === "markdown" || selectedKind === "text") && (
                  <p className="preview-doc-note">
                    Document — open in canvas for a full-size reading view.
                  </p>
                )}
                {selectedKind === "download" ? (
                  <p className="preview-doc-note">
                    Generated file — download or open in canvas.
                  </p>
                ) : null}
                <div className="preview-actions">
                  {isPreviewableKind(selectedKind) ||
                  selectedKind === "download" ? (
                    <button
                      type="button"
                      className="btn ghost compact"
                      onClick={() => openCanvasAt(String(selected.path))}
                    >
                      Open canvas
                    </button>
                  ) : null}
                  <a
                    href={selectedUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    download={
                      selectedKind === "download"
                        ? fileBasename(String(selected.path))
                        : undefined
                    }
                  >
                    {selectedKind === "download"
                      ? "Download"
                      : "Open in new tab"}
                  </a>
                </div>
              </div>
            ) : null}

            <ul className="artifact-list" id="artifact-list">
              {artifacts.map((a) => {
                const path = String(a.path || a.name || "");
                const kind = canvasKindForPath(path, a.kind);
                const active = path === selectedPath;
                return (
                  <li key={path}>
                    <button
                      type="button"
                      className={`artifact-item${active ? " active" : ""}`}
                      onClick={() => {
                        setSelectedPath(path);
                        if (isPreviewableKind(kind)) {
                          openCanvasAt(path);
                        }
                      }}
                    >
                      <span className="path" title={path}>
                        {path}
                      </span>
                      <span className="meta">
                        <span className="kind-tag">{a.kind || kind}</span>
                        {typeof a.size === "number" ? (
                          <span>{formatBytes(a.size)}</span>
                        ) : null}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </aside>

      <MediaCanvas
        open={canvasOpen}
        items={canvasItems}
        index={canvasIndex}
        onClose={() => setCanvasOpen(false)}
        onIndexChange={setCanvasIndex}
      />
    </>
  );
}
